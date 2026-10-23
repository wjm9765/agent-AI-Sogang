# Parser

담당: wjm · 브랜치: `feature/parser` · 상태: 개요 초안

## 역할

공문서 파일을 읽어 뒤의 에이전트(Pro, Critique, Writer)가 쓸 수 있는 **구조화된 JSON**으로 바꿉니다.

- 문서에 적힌 **사실만** 추출합니다. 추천 여부 판단은 하지 않습니다(Pro, Critique의 일).
- 사용자 정보는 받지 않습니다. 같은 문서는 사용자와 관계없이 같은 결과가 나와야 합니다.
- 모든 추출 항목에 원문 인용(`evidence`)을 붙여 Critique가 Pro 의견을 원문과 대조할 수 있게 합니다.

## 처리 흐름

1. **파일 읽기** (Python, LLM 없음)
   - **`.pdf` (주 형식)**: `pdfplumber` 등으로 페이지별 본문 텍스트와 표를 추출합니다. 페이지 번호를 같이 남겨 `evidence`에 쓸 수 있게 합니다.
   - **스캔본 PDF**(텍스트 레이어 없음)는 추출 결과가 비어 있습니다. 이 경우 PDF 파일을 Gemini에 직접 입력하거나(Gemini는 PDF 입력 지원) OCR로 대체합니다.
   - **`.hwpx`**: ZIP 파일입니다. 압축을 풀고 `Contents/section*.xml`에서 본문 텍스트(`<hp:t>`)와 표(`<hp:tbl>` → `<hp:tr>` → `<hp:tc>`)를 추출합니다.
   - 표는 신청 자격·일정이 자주 들어가므로 텍스트로 뭉개지 말고 행/열 구조를 유지합니다(예: Markdown 표로 변환).
   - `.hwp`(바이너리) 지원 여부는 추후 결정합니다.
2. **구조화** (Gemini)
   - 1단계 결과를 프롬프트에 넣고, 아래 출력 스키마에 맞춘 JSON을 받습니다(structured output).
3. **외부 확인** (선택, web search)
   - 일정이 공휴일과 겹치는지, 공고가 변경·취소됐는지 등을 확인합니다.
   - 결과는 `external_checks`에만 기록하고 원문에서 뽑은 값을 덮어쓰지 않습니다.

## 입력 / 출력

**입력**: 문서 파일 경로

**출력** (초안, Pro·Critique 담당과 협의해서 확정):

```json
{
  "source": { "file_name": "공고문.pdf", "format": "pdf", "pages": 4 },
  "title": "공고 제목",
  "issuer": "발행 기관",
  "issued_date": "YYYY-MM-DD",
  "summary": "문서 요약 (3문장 이내)",
  "eligibility": [
    { "condition": "만 19~34세 청년", "evidence": "원문 인용" }
  ],
  "benefits": [
    { "item": "월 50만원 지원", "evidence": "원문 인용" }
  ],
  "schedule": [
    { "event": "신청 기간", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "evidence": "원문 인용" }
  ],
  "how_to_apply": { "method": "온라인 신청", "evidence": "원문 인용" },
  "required_documents": [
    { "item": "주민등록등본", "evidence": "원문 인용" }
  ],
  "contact": { "value": "담당 부서 / 전화번호", "evidence": "원문 인용" },
  "external_checks": [
    { "query": "검색어", "finding": "확인한 내용", "source_url": "https://..." }
  ],
  "missing_fields": ["문서에서 찾지 못한 항목명"]
}
```

- 문서에 없는 값은 추측하지 않고 `null`로 두고 `missing_fields`에 적습니다.
- 날짜는 `YYYY-MM-DD`, 시간은 `HH:MM`(24시간)으로 통일합니다.
- PDF는 `evidence` 원문 인용 앞에 페이지를 붙입니다(예: `"p.2: 신청 기간은 ..."`).

## Tool

| 이름 | 종류 | 설명 | 구현 |
|---|---|---|---|
| `read_document(path)` | Python 함수 | 파일을 읽어 페이지별 텍스트 + 표 반환. LLM 호출 전에 Parser가 직접 실행 | PDF: `pdfplumber` 등 / hwpx: `zipfile`, `xml` |
| `web_search(query)` | LLM tool (function call) | 문서 밖 최신 정보 확인 | Gemini 내장 Google Search 또는 외부 검색 API (미정) |

## 프롬프트 (초안)

```
너는 한국 공문서(공고문, 안내문 등)를 읽고 구조화된 데이터로 정리하는 Parser 에이전트다.

규칙:
1. 문서에 적힌 사실만 추출한다. 추측하거나 일반 상식으로 빈칸을 채우지 않는다.
2. 모든 항목에 근거가 되는 원문 문장을 evidence로 그대로 인용한다. 페이지 정보가 있으면 "p.2: 원문" 형태로 앞에 붙인다.
3. 문서에서 찾을 수 없는 항목은 null로 두고 missing_fields에 항목명을 적는다.
4. 날짜는 YYYY-MM-DD, 시간은 HH:MM(24시간)으로 통일한다. 연도가 생략된 날짜는 문서 발행일 기준으로 해석한다.
5. 이 문서가 누구에게 맞는지, 추천할지는 판단하지 않는다. 그것은 다음 에이전트의 일이다.
6. 일정이 공휴일과 겹치거나 변경·취소 가능성이 보이면 web_search로 확인하고, 결과는 external_checks에만 기록한다. 원문에서 뽑은 값을 검색 결과로 덮어쓰지 않는다.
7. 주어진 JSON 스키마에 맞춰서만 출력한다.

<document>
{document_text}
</document>
```

## 미정 사항

- Gemini 모델명
- PDF 처리 방식 (Python으로 텍스트·표 추출 후 전달 vs PDF를 Gemini에 직접 전달)
- 스캔본 PDF 대응 (Gemini 직접 입력 vs OCR)
- `.hwp`(바이너리)도 지원할지
- web search 방식 (Gemini 내장 Google Search vs 별도 검색 API)
- 대상 문서 종류 (공고문만? 일반 공문 전체?)
- 출력 스키마 확정 (Pro, Critique 담당과 협의)
