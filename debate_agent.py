"""Pro & Critique 적대적 토론 (설계: AGENT.md).

Pro(지원 옹호자)와 Critique(리스크 감사관)가 turns번 왕복한다. 기본 2턴: Pro1 → Critique1 → Pro2 → Critique2.
도구 중 원문 검색·달력 계산은 Python으로 미리 실행해 프롬프트에 넣고, 웹 검색만 Gemini Google Search를 쓴다.
"""

import datetime as dt
import json
from collections.abc import Callable
from typing import Literal

import holidays
from google.genai import types
from pydantic import BaseModel, Field

from llm import generate_json, get_client, get_model
from parser_agent.schema import ParsedDocument, RawChunk, ScheduleItem


class Claim(BaseModel):
    category: str = Field(description="필수자격, 가점, 지원내용 등")
    assertion: str
    user_evidence: str = Field(description="근거가 된 사용자 정보")
    doc_citation: str = Field(description="공고 근거. 예: 'p.1 지원 대상·자격'")


class ProOutput(BaseModel):
    turn: int
    overall_stance: str
    claims: list[Claim]
    rebuttal_to_critic: list[str] = Field(description="1턴은 빈 목록. 2턴부터 Critique 지적에 대한 재반박·보완책")


class Risk(BaseModel):
    severity: Literal["CRITICAL", "WARNING"] = Field(description="CRITICAL=바로 탈락하는 결격, WARNING=주의사항")
    target_pro_claim: str
    counter_argument: str
    doc_citation: str
    external_evidence: str | None = Field(description="웹 검색·달력 확인 결과. 없으면 null")


class CritiqueOutput(BaseModel):
    turn: int
    is_fatal: bool = Field(description="CRITICAL 리스크가 하나라도 남아 있으면 true")
    risks: list[Risk]


PRO_PROMPT = """너는 Pro Agent(지원 옹호자)다. 사용자가 이 공고에 왜 지원할 수 있는지(필수 자격 충족), 어떤 가점·이점이 있는지 긍정적 근거를 최대한 찾는다.
- 모든 주장에 사용자 정보 근거(user_evidence)와 공고 근거(doc_citation)를 붙인다. 공고에 없는 내용을 지어내지 않는다.
- 2턴부터는 debate_history의 Critique 지적을 확인하고, exception_clauses의 예외·단서 조항(단, 다만, 예외, 대체 등)을 근거로 방어하거나 보완책을 rebuttal_to_critic에 쓴다. 방어할 수 없는 지적은 솔직히 인정한다."""

CRITIQUE_PROMPT = """너는 Critique Agent(리스크 감사관)다. Pro의 주장을 의심하고, 사용자가 탈락하거나 손해 볼 위험을 공고 근거로 찾는다.
- notice의 cautions와 risk_clauses의 제외 대상·실격 사유·별표·각주, 필수 요건(나이·업력·소득·지역·명의 등)을 사용자 정보와 하나씩 대조한다.
- 모든 지적에 공고 근거(doc_citation)를 붙인다. 근거 없는 추측성 반박은 하지 않는다.
- calendar_facts에서 마감일이 주말·공휴일이거나 web_facts에서 변경·취소가 확인되면 external_evidence에 적는다.
- 사용자가 바로 탈락하는 결격은 CRITICAL, 주의하면 되는 것은 WARNING이다.
- 2턴부터는 Pro의 재반박 중 타당한 것은 받아들여 빼고, 여전히 남는 리스크만 확정한다."""

WEB_PROMPT = """다음 공고에 대해 웹 검색으로 아래를 확인하고, 확인된 사실만 짧은 bullet로 써라. 확인된 것이 없으면 '확인된 변경 없음'이라고만 써라.
- 공고 취소·연장·일정 변경 공지
- 자격 요건 관련 최근 유의사항이나 탈락 사례
공고명: {title}"""

RISK_KEYWORDS = ["제외", "불가", "실격", "미달", "취소", "환수", "별표", "주의", "체납", "명의", "업력", "이상", "이하", "단,", "※"]
EXCEPTION_KEYWORDS = ["단,", "다만", "예외", "인정", "대체", "완납", "가능"]
WEEKDAYS = "월화수목금토일"


def lookup_raw_clause(chunks: list[RawChunk], keywords: list[str], limit: int = 10) -> str:
    """원문 조각 중 키워드가 많이 걸리는 순으로 돌려준다."""
    scored = [(sum(k in c.text for k in keywords), c) for c in chunks]
    hits = sorted((s for s in scored if s[0]), key=lambda s: -s[0])[:limit]
    return "\n\n".join(f"[p.{c.page} {c.section}]\n{c.text[:1500]}" for _, c in hits) or "(해당 조항 없음)"


def get_calendar_fact(schedule: list[ScheduleItem]) -> str:
    """일정 날짜가 주말·공휴일인지 계산한다."""
    kr_holidays = holidays.country_holidays("KR")
    facts = []
    for item in schedule:
        for label, value in (("시작", item.start), ("마감", item.end)):
            try:
                day = dt.date.fromisoformat((value or "")[:10])
            except ValueError:
                continue
            name = kr_holidays.get(day)
            kind = f"공휴일({name})" if name else ("주말" if day.weekday() >= 5 else "평일")
            facts.append(f"- {item.event} {label} {value}: {WEEKDAYS[day.weekday()]}요일, {kind}")
    return "\n".join(facts) or "(날짜 정보 없음)"


def web_search(title: str) -> str:
    response = get_client().models.generate_content(
        model=get_model(),
        contents=WEB_PROMPT.format(title=title),
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    return response.text or "(검색 결과 없음)"


def run_debate(
    doc: ParsedDocument, persona: str, turns: int = 2, on_step: Callable[[str], None] | None = None
) -> list[dict]:
    """토론 기록(debate_history)을 돌려준다. 각 항목은 {"agent": "Pro"|"Critique", ...출력}."""
    step = on_step or (lambda message: None)
    history: list[dict] = []
    exception_clauses = lookup_raw_clause(doc.raw_chunks, EXCEPTION_KEYWORDS)
    critique_tools = {
        "risk_clauses": lookup_raw_clause(doc.raw_chunks, RISK_KEYWORDS),
        "calendar_facts": get_calendar_fact(doc.structured_summary.schedule),
    }

    for turn in range(1, turns + 1):
        step(f"Pro {turn}턴")
        extra = {"exception_clauses": exception_clauses} if turn > 1 else {}
        pro = generate_json(ProOutput, _context(doc, persona, history, turn, extra), PRO_PROMPT)
        pro.turn = turn
        history.append({"agent": "Pro", **pro.model_dump()})

        if "web_facts" not in critique_tools:
            step("Critique: 웹 검색")
            critique_tools["web_facts"] = web_search(doc.structured_summary.title or doc.source.file_name)
        step(f"Critique {turn}턴")
        critique = generate_json(CritiqueOutput, _context(doc, persona, history, turn, critique_tools), CRITIQUE_PROMPT)
        critique.turn = turn
        history.append({"agent": "Critique", **critique.model_dump()})
    return history


def _context(doc: ParsedDocument, persona: str, history: list[dict], turn: int, extra: dict[str, str]) -> str:
    parts = [
        f"<turn>{turn}</turn>",
        f"<notice>\n{doc.structured_summary.model_dump_json()}\n</notice>",
        f"<user>\n{persona}\n</user>",
        *(f"<{name}>\n{value}\n</{name}>" for name, value in extra.items()),
    ]
    if history:
        parts.append(f"<debate_history>\n{json.dumps(history, ensure_ascii=False)}\n</debate_history>")
    return "\n\n".join(parts)
