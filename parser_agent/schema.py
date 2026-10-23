"""Parser 출력(ParsedDocument) 스키마. Pro, Critique가 이 구조를 그대로 받는다."""

from pydantic import BaseModel, Field


class Source(BaseModel):
    file_name: str
    format: str  # "pdf" 또는 "web"(출처 링크에서 가져온 공고)
    pages: int
    url: str | None = None
    references: list[str] = Field(default_factory=list, description="web: 공고 내용을 가져온 URL")


class RawChunk(BaseModel):
    """원문 조각. Critique가 페이지·조항을 인용할 때 쓴다."""

    chunk_id: str
    page: int
    section: str
    text: str


class Item(BaseModel):
    content: str
    evidence: str = Field(description='근거 원문 인용. 앞에 페이지를 붙인다. 예: "p.2: 신청 기간은 ..."')


class ScheduleItem(BaseModel):
    event: str
    start: str | None = Field(description="YYYY-MM-DD 또는 YYYY-MM-DD HH:MM")
    end: str | None = Field(description="YYYY-MM-DD 또는 YYYY-MM-DD HH:MM")
    evidence: str = Field(description='근거 원문 인용. 앞에 페이지를 붙인다. 예: "p.2: 신청 기간은 ..."')


class StructuredSummary(BaseModel):
    """LLM이 채우는 부분."""

    title: str | None
    issuer: str | None = Field(description="발행 기관")
    issued_date: str | None = Field(description="YYYY-MM-DD")
    summary: str = Field(description="문서 요약, 3문장 이내")
    eligibility: list[Item] = Field(description="지원 자격·대상")
    benefits: list[Item] = Field(description="지원 내용·혜택")
    schedule: list[ScheduleItem] = Field(description="신청 기간, 발표일 등 일정")
    how_to_apply: list[Item] = Field(description="신청 방법·절차")
    required_documents: list[Item] = Field(description="제출 서류")
    cautions: list[Item] = Field(description="지원 제외 대상, 실격·감점 사유, 별표·각주·단서 조항의 유의사항")
    contact: list[Item] = Field(description="문의처")
    missing_fields: list[str] = Field(description="문서에서 찾지 못한 항목명")


class ParsedDocument(BaseModel):
    source: Source
    structured_summary: StructuredSummary
    raw_chunks: list[RawChunk]
