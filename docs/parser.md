# Parser

담당: wjm · 브랜치: `feature/parser` · 상태: 최소 구현 (PDF → ParsedDocument JSON)

## 역할

공문서 파일을 읽어 뒤의 에이전트(Pro, Critique, Writer)가 쓸 수 있는 **구조화된 JSON**(`ParsedDocument`)으로 바꿉니다.

- 문서에 적힌 **사실만** 추출합니다. 추천 여부 판단은 하지 않습니다(Pro, Critique의 일).
- 사용자 정보는 받지 않습니다. 같은 문서는 사용자와 관계없이 같은 결과가 나와야 합니다.
- 모든 추출 항목에 원문 인용(`evidence`)을 붙여 Critique가 Pro 의견을 원문과 대조할 수 있게 합니다.

## 실행

```bash
uv run python -m parser_agent 데이터셋.zip           # 6개 케이스 → outputs/데이터셋 N ....parsed.json
uv run python -m parser_agent "데이터셋 1 ....txt"    # 케이스 하나
uv run python -m parser_agent 공고문.pdf             # PDF → outputs/공고문.parsed.json
uv run python -m parser_agent 공고문.pdf --no-llm    # LLM 없이 raw_chunks만 출력 (파일 읽기 확인용)
uv run streamlit run app.py                          # UI: 업로드 → 케이스 선택 → 분석 (판정까지)
```

코드에서 쓸 때:

```python
from dataset import load_cases
from parser_agent.agent import parse_document, parse_notice

doc = parse_notice(load_cases("데이터셋.zip")[0])   # 데이터셋 케이스 (출처 링크)
doc = parse_document("공고문.pdf")                  # PDF
doc.model_dump()                                    # dict
```

## 데이터셋 입력 (출처 링크)

데이터셋 `.txt`에는 공고문 본문이 없고 출처 링크만 있습니다. 출처 페이지를 직접 긁으면 메뉴만 나오거나(서울시), 본문이 첨부 `.hwpx`에 있거나(중기부), JS로 그려져 비어 있어서(u300) 쓸 수 없습니다. 그래서 `fetch_notice()`가 **Gemini의 URL 읽기 + Google 검색 도구**로 공고문 내용을 모은 뒤, 아래 구조화 단계에 넘깁니다. 내용을 가져온 URL은 `source.references`에 남습니다.

## 처리 흐름

| 단계 | 코드 | 내용 |
|---|---|---|
| 1. 파일 읽기 (LLM 없음) | `parser_agent/reader.py` | `pdfplumber`로 페이지별 본문 줄과 표를 추출. 표는 Markdown으로 바꿔 페이지 안 원래 위치에 끼워 넣음 |
| 2. 원문 조각 | `reader.split_chunks` | 조항·항목 제목 줄(`제3조(지원자격)`, `Ⅱ.`, `1.`, `□` 등)이나 페이지가 바뀔 때마다 끊어 `raw_chunks` 생성 |
| 3. 구조화 (Gemini) | `parser_agent/agent.py` | 페이지 표시(`[p.N]`)를 붙인 본문을 넣고 `StructuredSummary` 스키마로 JSON 출력(structured output) |

- **스캔본 PDF**(텍스트 레이어 없음)는 1단계 결과가 비어 있으므로 PDF 파일을 Gemini에 그대로 넣습니다. 이때 `raw_chunks`는 비어 있습니다(OCR은 추후).
- `.hwpx`, `.hwp`는 아직 지원하지 않습니다.

## 출력: `ParsedDocument`

스키마 정의는 `parser_agent/schema.py`에 있습니다. Pro·Critique README의 요구사항(`structured_summary`, `raw_chunks`에 `page`·`section`·`text`)에 맞췄습니다.

```json
{
  "source": { "file_name": "공고문.pdf", "format": "pdf", "pages": 2 },
  "structured_summary": {
    "title": "2026년 청년 창업 지원사업 모집 공고",
    "issuer": "서울특별시 경제진흥원",
    "issued_date": "2026-10-01",
    "summary": "문서 요약 (3문장 이내)",
    "eligibility":        [{ "content": "만 19~39세 대표자, 업력 3년 이내", "evidence": "p.1: 제2조(지원자격) ..." }],
    "benefits":           [{ "content": "...", "evidence": "p.2: ..." }],
    "schedule":           [{ "event": "신청 접수", "start": "2026-10-06", "end": "2026-10-25 18:00", "evidence": "p.2: ..." }],
    "how_to_apply":       [{ "content": "...", "evidence": "..." }],
    "required_documents": [{ "content": "...", "evidence": "..." }],
    "cautions":           [{ "content": "국세·지방세 체납 기업 제외 (신청일 전 완납 시 예외)", "evidence": "p.1: ..." }],
    "contact":            [{ "content": "...", "evidence": "..." }],
    "missing_fields": ["문서에서 찾지 못한 항목명"]
  },
  "raw_chunks": [
    { "chunk_id": "c002", "page": 1, "section": "제2조(지원자격)", "text": "원문 그대로" }
  ]
}
```

- `structured_summary`: LLM이 채우는 요약. 문서에 없는 값은 `null` 또는 빈 목록, 항목명은 `missing_fields`에 기록.
- `cautions`: 지원 제외 대상, 실격·감점 사유, 별표·각주·단서 조항. 요약에서 빠지기 쉬워 따로 모읍니다(Critique 리스크 검토용).
- `raw_chunks`: LLM을 거치지 않은 원문 그대로의 조각. Pro·Critique의 원문 검색(`lookup_raw_clause`)과 페이지 인용에 씁니다.
- 날짜는 `YYYY-MM-DD`, 시간이 있으면 `YYYY-MM-DD HH:MM`.

## 프롬프트

`parser_agent/agent.py`의 `SYSTEM_PROMPT`를 기준으로 합니다. 요지:

1. 문서에 적힌 사실만 추출, 추측 금지
2. 모든 항목에 `"p.N: 원문"` 형태의 근거 인용
3. 없는 항목은 `null`/빈 목록 + `missing_fields`
4. 날짜 형식 통일, 연도 생략 시 발행일 기준
5. 제외·실격·단서 조항은 빠짐없이 `cautions`에
6. 추천 여부는 판단하지 않음

## Tool

| 이름 | 종류 | 설명 | 상태 |
|---|---|---|---|
| `read_document(path)` | Python 함수 | 파일을 읽어 페이지별 텍스트 + 표 반환. LLM 호출 전에 실행 | 구현 (PDF) |
| `web_search(query)` | LLM tool | 공휴일·일정 변경 등 문서 밖 정보 확인 | 미구현. Critique가 Google Search를 쓰므로 Parser에 필요한지 협의 |

## 미정 사항

- Gemini 모델명 확정 (현재 `gemini-3.8-flash`, 유료 키 필요)
- 첨부 hwpx에만 본문이 있는 공고(데이터셋 3번 등)는 가져온 내용이 빈약함
- 스캔본 PDF의 `raw_chunks` 생성 (OCR 또는 Gemini로 원문 추출)
- `.hwpx`, `.hwp` 지원 여부
- Parser에 web search가 필요한지 (Critique와 역할 겹침)
- 출력 스키마 확정 (Pro, Critique 담당과 협의)
