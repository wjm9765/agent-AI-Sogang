"""공고 적격성 검토 Streamlit 화면: Parser → Pro ↔ Critique → Writer.

실행: uv run streamlit run app.py
"""

import io
import json
import tempfile
from pathlib import Path

import streamlit as st

from dataset import Case, load_cases, parse_case
from llm import get_model, load_config
from parser_agent.agent import parse_document, parse_notice
from parser_agent.reader import read_document, split_chunks
from parser_agent.schema import ParsedDocument
from pipeline import judge

ITEM_SECTIONS = [
    ("eligibility", "지원 자격"),
    ("benefits", "지원 내용"),
    ("how_to_apply", "신청 방법"),
    ("required_documents", "제출 서류"),
    ("contact", "문의처"),
]
VERDICT = {"GO": ("지원 권장", st.success), "HOLD": ("보완 후 지원", st.warning), "STOP": ("비추천", st.error)}


def load_uploaded_cases(uploaded) -> list[Case]:
    if uploaded.name.lower().endswith(".zip"):
        return load_cases(io.BytesIO(uploaded.getvalue()))
    return [parse_case(uploaded.getvalue().decode("utf-8-sig"))]


def parse_pdf(uploaded, no_llm: bool) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / uploaded.name
        path.write_bytes(uploaded.getvalue())
        if no_llm:
            return {"raw_chunks": [c.model_dump() for c in split_chunks(read_document(path))]}
        return parse_document(path).model_dump()


def ideal_writer_answer(ideal_answer: str) -> str:
    """데이터셋 정답에서 Writer 판정 줄만 꺼낸다."""
    lines = [line for line in ideal_answer.splitlines() if line.startswith("[Writer")]
    return lines[0] if lines else ideal_answer


def show_report(report: dict, ideal_answer: str | None) -> None:
    label, box = VERDICT[report["recommendation"]]
    box(f"### {label} · {report['verdict_label']}\n\n{report['summary']}\n\n확신도 {report['confidence_score']}%")
    if ideal_answer:
        st.caption("데이터셋 정답 (Writer)")
        st.markdown(f"> {ideal_writer_answer(ideal_answer)}")

    left, middle, right = st.columns(3)
    for column, title, items in [
        (left, "판정 근거", report["key_reasons"]),
        (middle, "⚠️ 주의사항", report["warnings"]),
        (right, "다음 할 일", report["action_items"]),
    ]:
        with column:
            st.markdown(f"#### {title}")
            for item in items:
                st.markdown(f"- {item}")


def show_debate(history: list[dict]) -> None:
    for entry in history:
        if entry["agent"] == "Pro":
            with st.chat_message("Pro", avatar="🟢"):
                st.markdown(f"**Pro {entry['turn']}턴** · {entry['overall_stance']}")
                for claim in entry["claims"]:
                    st.markdown(f"- [{claim['category']}] {claim['assertion']}")
                    st.caption(f"사용자: {claim['user_evidence']} · 공고: {claim['doc_citation']}")
                for rebuttal in entry["rebuttal_to_critic"]:
                    st.markdown(f"- ↩️ {rebuttal}")
        else:
            with st.chat_message("Critique", avatar="🔴"):
                fatal = " · 치명적 결격 있음" if entry["is_fatal"] else ""
                st.markdown(f"**Critique {entry['turn']}턴**{fatal}")
                for risk in entry["risks"]:
                    icon = "🚫" if risk["severity"] == "CRITICAL" else "⚠️"
                    st.markdown(f"- {icon} **{risk['severity']}** {risk['counter_argument']}")
                    caption = f"반박 대상: {risk['target_pro_claim']} · 공고: {risk['doc_citation']}"
                    if risk["external_evidence"]:
                        caption += f" · 외부: {risk['external_evidence']}"
                    st.caption(caption)


def show_items(items: list[dict]) -> None:
    if not items:
        st.caption("문서에 없음")
    for item in items:
        st.markdown(f"- {item['content']}")
        st.caption(item["evidence"])


def show_summary(source: dict, summary: dict) -> None:
    st.subheader(summary["title"] or source["file_name"])
    if source.get("references"):
        links = " · ".join(f"[{i}]({url})" for i, url in enumerate(source["references"], start=1))
        st.caption(f"공고 내용 출처: {links}")
    c1, c2, c3 = st.columns(3)
    c1.metric("발행 기관", summary["issuer"] or "-")
    c2.metric("발행일", summary["issued_date"] or "-")
    c3.metric("형식", f"{source['format']} · {source['pages']}쪽")
    st.info(summary["summary"])

    st.markdown("#### ⚠️ 유의사항 (제외·실격·단서 조항)")
    if not summary["cautions"]:
        st.caption("문서에 없음")
    for item in summary["cautions"]:
        st.warning(f"{item['content']}\n\n`{item['evidence']}`")

    st.markdown("#### 일정")
    if summary["schedule"]:
        st.dataframe(summary["schedule"], hide_index=True, width="stretch")
    else:
        st.caption("문서에 없음")

    left, right = st.columns(2)
    for i, (key, label) in enumerate(ITEM_SECTIONS):
        with left if i % 2 == 0 else right:
            st.markdown(f"#### {label}")
            show_items(summary[key])

    if summary["missing_fields"]:
        st.caption("문서에서 찾지 못한 항목: " + ", ".join(summary["missing_fields"]))


def show_chunks(chunks: list[dict]) -> None:
    if not chunks:
        st.caption("원문 조각이 없습니다. 스캔본 PDF일 수 있습니다.")
    for chunk in chunks:
        with st.expander(f"p.{chunk['page']} · {chunk['section']}"):
            st.text(chunk["text"])


def show_result(result: dict, file_name: str, ideal_answer: str | None) -> None:
    parsed = result.get("parsed", {})
    if "report" in result:
        show_report(result["report"], ideal_answer)
    tabs = {}
    names = (["토론"] if "debate_history" in result else []) + (["공고 요약"] if parsed.get("structured_summary") else [])
    names += [f"원문 조각 ({len(parsed.get('raw_chunks', []))})", "JSON"]
    tabs = dict(zip(names, st.tabs(names)))
    if "토론" in tabs:
        with tabs["토론"]:
            show_debate(result["debate_history"])
    if "공고 요약" in tabs:
        with tabs["공고 요약"]:
            show_summary(parsed["source"], parsed["structured_summary"])
    with tabs[names[-2]]:
        show_chunks(parsed.get("raw_chunks", []))
    with tabs["JSON"]:
        st.download_button(
            "JSON 다운로드",
            json.dumps(result, ensure_ascii=False, indent=2),
            file_name=f"{Path(file_name).stem}.result.json",
            mime="application/json",
        )
        st.json(result, expanded=False)


st.set_page_config(page_title="공고 적격성 검토", page_icon="⚖️", layout="wide")
st.title("⚖️ 공고 적격성 검토")
st.caption(f"Parser → Pro ↔ Critique → Writer · 모델 `{get_model()}` · 토론 {load_config()['debate']['turns']}턴 (config.yaml)")

uploaded = st.file_uploader("데이터셋(.zip / .txt) 또는 공문서 PDF", type=["zip", "txt", "pdf"])
case = None
persona = ""
no_llm = False

if uploaded and uploaded.name.lower().endswith(".pdf"):
    persona = st.text_area("사용자 정보 (입력하면 판정까지, 비우면 공고 정리만)")
    no_llm = st.toggle("LLM 없이 원문 조각만 보기", help="API 호출 없이 파일 읽기만 확인할 때")
elif uploaded:
    try:
        cases = load_uploaded_cases(uploaded)
    except Exception as e:
        st.error(f"데이터셋을 읽지 못했습니다. {type(e).__name__}: {e}")
        st.stop()
    case = st.selectbox("케이스", cases, format_func=lambda c: c.name)
    persona = case.persona
    st.markdown(f"**공고명** {case.notice_title}  \n**출처 링크** {case.source_url}")
    with st.expander("사용자 상황 (Persona)"):
        st.text(case.persona)

key = f"{uploaded.name}:{case.name}" if case else (uploaded.name if uploaded else None)

if uploaded and st.button("분석", type="primary"):
    st.session_state.pop("result", None)
    st.session_state.result_key = key
    result: dict = {}
    with st.status("분석 중... (케이스당 2~4분)", expanded=True) as status:
        try:
            if case:
                status.write("Parser: 출처 링크에서 공고 가져와 구조화")
                result["parsed"] = parse_notice(case).model_dump()
            else:
                status.write("Parser: PDF 읽기")
                parsed = parse_pdf(uploaded, no_llm)
                result["parsed"] = parsed
            if persona.strip() and not no_llm:
                doc = ParsedDocument.model_validate(result["parsed"])
                result |= judge(doc, persona, on_step=status.write)
            status.update(label="완료", state="complete", expanded=False)
        except Exception as e:
            status.update(label="실패", state="error")
            st.error(f"{type(e).__name__}: {e}")
    if result:
        st.session_state.result = result

if uploaded and "result" in st.session_state and st.session_state.get("result_key") == key:
    show_result(st.session_state.result, case.name if case else uploaded.name, case.ideal_answer if case else None)
