"""Writer: Parser 데이터 + 사용자 정보 + Pro·Critique 토론을 종합해 최종 판정을 쓴다 (설계: writer_agent_documentation.md)."""

import json
from typing import Literal

from pydantic import BaseModel, Field

from llm import generate_json
from parser_agent.schema import ParsedDocument


class WriterOutput(BaseModel):
    recommendation: Literal["GO", "HOLD", "STOP"] = Field(
        description="GO=지원 권장, HOLD=보완 후 지원(보류), STOP=비추천·신청 불가"
    )
    verdict_label: str = Field(description="판정 한 줄. 예: '[신청 불가 / 수공기간 미달]'")
    confidence_score: int = Field(description="판정 확신도 0~100")
    summary: str = Field(description="두괄식 2~4문장. 첫 문장에 최종 판정을 선언")
    key_reasons: list[str] = Field(description="판정 핵심 근거. 공고 근거 포함")
    warnings: list[str] = Field(description="놓치면 탈락·불이익인 기한·서류·자격 요건")
    action_items: list[str] = Field(description="지원 시 준비할 것, 또는 다음 공고를 위해 갖출 요건")


WRITER_PROMPT = """너는 Writer Agent(최종 판정관이자 집필가)다. Parser의 공고 데이터, 사용자 정보, Pro·Critique 토론 기록을 종합해 사용자가 지원할지 바로 결정할 수 있게 최종 판정을 쓴다.

1. 두괄식·개조식: summary 첫 문장에서 최종 판정(지원 권장 / 보류 / 비추천)을 선언하고, 근거와 주의사항은 짧은 항목으로 쓴다.
2. 균형: Pro의 근거와 Critique의 리스크를 함께 저울질해 객관적으로 결론 낸다. 무조건 추천하지 않는다.
3. Safety First: 토론 마지막 Critique에 CRITICAL 리스크가 남아 있으면 STOP, 신청 전에 사용자가 고칠 수 있는 문제뿐이면 HOLD다. 기한·필수 서류·자격 요건처럼 놓치면 탈락하는 요소는 warnings에 둔다.
4. 실행 가능한 지침: 지원한다면 준비할 서류, 포기한다면 다음 공고를 위해 갖출 요건을 action_items에 쓴다.
5. 공고 데이터와 토론에 없는 사실은 지어내지 않는다."""


def write_report(doc: ParsedDocument, persona: str, debate_history: list[dict]) -> WriterOutput:
    contents = "\n\n".join([
        f"<notice>\n{doc.structured_summary.model_dump_json()}\n</notice>",
        f"<user>\n{persona}\n</user>",
        f"<debate_history>\n{json.dumps(debate_history, ensure_ascii=False)}\n</debate_history>",
    ])
    return generate_json(WriterOutput, contents, WRITER_PROMPT)
