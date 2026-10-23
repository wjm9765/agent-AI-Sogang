"""Parser 에이전트: 문서를 읽어 raw_chunks를 만들고, Gemini로 structured_summary를 채운다.

- PDF 파일: parse_document(path)
- 데이터셋 케이스(공고명 + 출처 링크): parse_notice(case)
"""

import re
from pathlib import Path

from google.genai import types

from dataset import Case
from llm import get_client, get_model
from parser_agent.reader import Page, read_document, split_chunks
from parser_agent.schema import ParsedDocument, Source, StructuredSummary

SYSTEM_PROMPT = """너는 한국 공문서(공고문, 안내문 등)를 읽고 구조화된 데이터로 정리하는 Parser 에이전트다.

규칙:
1. 문서에 적힌 사실만 추출한다. 추측하거나 일반 상식으로 빈칸을 채우지 않는다.
2. 모든 항목의 evidence에는 근거가 되는 원문 문장을 그대로 인용하고, 앞에 페이지를 "p.2: " 형태로 붙인다. 페이지는 문서의 [p.N] 표시를 따르고, 표시가 없으면 PDF 페이지 번호를 쓴다.
3. 문서에서 찾을 수 없는 항목은 null 또는 빈 목록으로 두고 missing_fields에 항목명을 적는다.
4. 날짜는 YYYY-MM-DD, 시간이 있으면 YYYY-MM-DD HH:MM(24시간)으로 쓴다. 연도가 생략된 날짜는 문서 발행일 기준으로 해석한다.
5. 지원 제외 대상, 실격·감점 사유, 별표·각주·단서 조항의 유의사항은 빠짐없이 cautions에 넣는다. 요약에서 빠지기 쉽지만 다음 에이전트가 리스크 검토에 쓴다.
6. 이 문서가 누구에게 맞는지, 추천할지는 판단하지 않는다. 그것은 다음 에이전트의 일이다."""

FETCH_PROMPT = """아래 공고의 공고문 내용을 출처 링크와 웹 검색으로 찾아, 항목별로 빠짐없이 정리하라.

- 원문을 통째로 복사하지 말고 항목별로 정리하되, 숫자·금액·날짜·자격 조건·예외/단서 문구는 원문과 똑같이 정확하게 쓴다. 내용을 줄이거나 빼지 않는다.
- 지원 대상·자격, 지원 내용, 일정, 신청 방법, 제출 서류, 신청 제외 대상·실격 사유·유의사항(별표·각주 포함), 문의처를 빠짐없이 포함한다.
- 조항·항목 제목(예: "1. 지원대상", "□ 신청방법", "제3조(신청자격)")은 줄 맨 앞에 그대로 둔다.
- 공고명의 연도와 같은 회차의 공고만 쓴다. 다른 연도 공고 내용을 섞지 않는다.
- 찾지 못한 내용은 지어내지 말고 생략한다.
- 옮겨 적은 공고문은 <notice>와 </notice> 사이에만 쓴다. 검색 과정 설명 등 다른 말은 태그 밖에 둔다.

공고명: {title}
출처 링크: {url}"""


def parse_document(path: str | Path) -> ParsedDocument:
    path = Path(path)
    pages = read_document(path)
    return ParsedDocument(
        source=Source(file_name=path.name, format=path.suffix.lstrip(".").lower(), pages=len(pages)),
        structured_summary=summarize(pages, pdf_path=path),
        raw_chunks=split_chunks(pages),
    )


def parse_notice(case: Case) -> ParsedDocument:
    text, references = fetch_notice(case.notice_title, case.source_url)
    pages = [Page(1, text)]
    return ParsedDocument(
        source=Source(file_name=case.name, format="web", pages=1, url=case.source_url, references=references),
        structured_summary=summarize(pages),
        raw_chunks=split_chunks(pages),
    )


def fetch_notice(title: str, url: str) -> tuple[str, list[str]]:
    """출처 페이지는 첨부파일·JS 렌더링이 많아 직접 긁기 어렵다. Gemini의 URL 읽기 + 구글 검색으로 공고문 내용을 모은다."""
    response = get_client().models.generate_content(
        model=get_model(),
        contents=FETCH_PROMPT.format(title=title, url=url),
        config=types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext()), types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    match = re.search(r"<notice>(.*?)</notice>", response.text or "", re.DOTALL)
    text = (match.group(1) if match else response.text or "").strip()
    if not text:
        reason = response.candidates[0].finish_reason if response.candidates else None
        raise RuntimeError(f"공고 내용을 가져오지 못했습니다 (finish_reason={reason}): {url}")
    return text, _references(response)


def summarize(pages: list[Page], pdf_path: Path | None = None) -> StructuredSummary:
    if any(page.text for page in pages):
        document = "\n\n".join(f"[p.{page.number}]\n{page.text}" for page in pages)
        contents = f"<document>\n{document}\n</document>"
    elif pdf_path:
        # 스캔본 PDF라 텍스트를 못 뽑았으면 PDF를 그대로 넣는다.
        contents = types.Part.from_bytes(data=pdf_path.read_bytes(), mime_type="application/pdf")
    else:
        raise ValueError("요약할 문서 내용이 없습니다.")

    response = get_client().models.generate_content(
        model=get_model(),
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=StructuredSummary,
        ),
    )
    return StructuredSummary.model_validate_json(response.text)


def _references(response: types.GenerateContentResponse) -> list[str]:
    candidate = response.candidates[0]
    urls = []
    if candidate.url_context_metadata:
        urls += [m.retrieved_url for m in candidate.url_context_metadata.url_metadata or [] if m.retrieved_url]
    if candidate.grounding_metadata:
        urls += [c.web.uri for c in candidate.grounding_metadata.grounding_chunks or [] if c.web and c.web.uri]
    return list(dict.fromkeys(urls))
