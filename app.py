"""공고 적격성 검토 Streamlit 화면: Parser → Pro ↔ Critique → Writer.

실행: uv run streamlit run app.py
"""

import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from dataset import Case, load_cases, parse_case
from llm import ROOT, get_api_key, get_model, load_config
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
VERDICT = {
    "GO": ("지원 권장", st.success),
    "HOLD": ("보완 후 지원", st.warning),
    "STOP": ("비추천", st.error),
}


def load_uploaded_cases(uploaded) -> list[Case]:
    if uploaded.name.lower().endswith(".zip"):
        return load_cases(io.BytesIO(uploaded.getvalue()))
    return [parse_case(uploaded.getvalue().decode("utf-8-sig"))]


def parse_pdf(uploaded, no_llm: bool) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(uploaded.name).name
        path.write_bytes(uploaded.getvalue())
        if no_llm:
            return {
                "raw_chunks": [
                    c.model_dump() for c in split_chunks(read_document(path))
                ]
            }
        return parse_document(path).model_dump()


def ideal_writer_answer(ideal_answer: str) -> str:
    """데이터셋 정답에서 Writer 판정 줄만 꺼낸다."""
    lines = [line for line in ideal_answer.splitlines() if line.startswith("[Writer")]
    return lines[0] if lines else ideal_answer


def show_report(report: dict, ideal_answer: str | None) -> None:
    label, box = VERDICT[report["recommendation"]]
    st.caption("검토 결과 · FINAL REPORT")
    box(f"### {label}\n\n**{report['verdict_label']}**\n\n{report['summary']}")
    st.caption(
        f"모델이 제시한 확신도 {report['confidence_score']}% · 실제 선정 확률을 의미하지 않습니다."
    )
    if ideal_answer:
        with st.expander("평가용 정답과 비교"):
            st.caption("이 정답은 에이전트 입력에 포함되지 않습니다.")
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
            if not items:
                st.caption("추가 항목이 없습니다.")


def show_debate(history: list[dict]) -> None:
    for entry in history:
        if entry["agent"] == "Pro":
            with st.chat_message("Pro", avatar="🟢"):
                st.markdown(f"**Pro {entry['turn']}턴** · {entry['overall_stance']}")
                for claim in entry["claims"]:
                    st.markdown(f"- [{claim['category']}] {claim['assertion']}")
                    st.caption(
                        f"사용자: {claim['user_evidence']} · 공고: {claim['doc_citation']}"
                    )
                for rebuttal in entry["rebuttal_to_critic"]:
                    st.markdown(f"- ↩️ {rebuttal}")
        else:
            with st.chat_message("Critique", avatar="🔴"):
                fatal = " · 치명적 결격 있음" if entry["is_fatal"] else ""
                st.markdown(f"**Critique {entry['turn']}턴**{fatal}")
                for risk in entry["risks"]:
                    icon = "🚫" if risk["severity"] == "CRITICAL" else "⚠️"
                    st.markdown(
                        f"- {icon} **{risk['severity']}** {risk['counter_argument']}"
                    )
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
        links = " · ".join(
            f"[{i}]({url})" for i, url in enumerate(source["references"], start=1)
        )
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
    names = (["토론"] if "debate_history" in result else []) + (
        ["공고 요약"] if parsed.get("structured_summary") else []
    )
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


def analysis_key(content: bytes, persona: str, no_llm: bool, case: Case | None) -> str:
    """같은 파일명이라도 내용·프로필·모드가 바뀌면 이전 결과를 표시하지 않는다."""
    settings = json.dumps(
        [persona, no_llm, case.name if case else None, load_config()], sort_keys=True
    )
    return hashlib.sha256(content + settings.encode()).hexdigest()


def analysis_error(error: Exception) -> str:
    """API 응답 원문(키·URL·프로필 등이 포함될 수 있음)을 화면에 노출하지 않는다."""
    code = getattr(error, "code", None)
    if str(code) == "429":
        return "Gemini 호출 한도에 도달했습니다. 한도 초기화 후 다시 시도하거나 배포 관리자가 API 요금제·검색 도구 할당량을 확인해 주세요. PDF는 ‘원문만 확인’으로 계속 읽을 수 있습니다."
    if str(code) in {"401", "403"}:
        return "Gemini 인증 또는 접근 권한을 확인해 주세요. 배포 관리자가 API 키와 모델 사용 권한을 확인해야 합니다."
    if str(code) == "404":
        return "설정된 Gemini 모델을 사용할 수 없습니다. 배포 관리자가 config.yaml의 모델과 API 접근 권한을 확인해 주세요."
    if str(code) in {"500", "502", "503", "504"}:
        return "Gemini 서버가 일시적으로 응답하지 않습니다. 잠시 후 다시 시도해 주세요."
    if not get_api_key():
        return "분석용 API 키가 설정되지 않았습니다. PDF의 ‘원문만 확인’은 키 없이 사용할 수 있습니다."
    return "분석을 완료하지 못했습니다. 파일 형식과 내용을 확인해 주세요. AI 분석이라면 API 키의 한도·모델 사용 권한도 확인이 필요합니다."


def show_empty_state() -> None:
    st.html("""<div class="empty-state">
        <div class="document-mark">文<span>✓</span></div>
        <div class="eyebrow">A CLEARER NEXT STEP</div>
        <h2>긴 공고문 끝에,<br>명확한 다음 한 걸음.</h2>
        <p>지원할 수 있는 이유와 놓치기 쉬운 조건을<br>함께 살펴보고, 준비할 일을 정리해 드려요.</p>
        <div class="result-tags"><span>지원 가능성</span><span>근거와 주의사항</span><span>다음 할 일</span></div>
        <div class="empty-note">왼쪽에서 문서를 선택하면 이곳에 검토 결과가 나타납니다.</div>
    </div>""")
    with st.expander("어떻게 검토하나요?"):
        st.markdown(
            "**01 · 문서 읽기** — 자격, 혜택, 일정을 공고 근거와 함께 정리합니다.\n\n"
            "**02 · 조건 대조** — 사용자 정보에 맞는 근거와 제외·실격 조건을 검토합니다.\n\n"
            "**03 · 결과 정리** — 지원 권장 / 보완 후 지원 / 비추천과 다음 할 일을 제시합니다."
        )


def main() -> None:
    st.set_page_config(
        page_title="공고핏 · 나에게 맞는 공고 찾기", page_icon="📑", layout="wide"
    )
    st.html(f"<style>{(ROOT / 'ui.css').read_text(encoding='utf-8')}</style>")
    st.html("""<div class="brand-bar"><div class="brand"><span class="brand-icon">✓</span>공고핏
        <span class="brand-en">NOTICE FIT</span></div><div class="brand-meta">AI 공문서 검토 워크스페이스</div></div>
        <div class="hero"><div class="eyebrow">LESS READING. MORE CLARITY.</div>
        <h1>이 공고, <span>나에게 맞을까?</span></h1>
        <p>복잡한 지원 조건부터 놓치기 쉬운 예외까지.<br class="mobile-break"> 공고와 내 정보를 함께 확인하세요.</p></div>
        <div class="workflow"><span><b>01</b> 문서 선택</span><i>→</i><span><b>02</b> 내 정보 확인</span>
        <i>→</i><span><b>03</b> 근거 있는 판단</span></div>""")

    left, right = st.columns([1, 1.55], gap="large")
    case = None
    uploaded = None
    persona = ""
    no_llm = False
    content = b""
    file_name = ""
    with left, st.container(key="input_card"):
        st.markdown("### 검토할 공고")
        st.caption("공문서를 올리거나 준비된 데이터셋을 선택하세요.")
        mode = st.segmented_control(
            "입력 방법",
            ["파일 업로드", "샘플 데이터셋"],
            default="파일 업로드",
            label_visibility="collapsed",
        )
        cases = []
        if mode == "샘플 데이터셋":
            try:
                cases = load_cases(ROOT / "데이터셋.zip")
                content = (ROOT / "데이터셋.zip").read_bytes()
            except (OSError, ValueError, zipfile.BadZipFile):
                st.error(
                    "기본 데이터셋을 읽을 수 없습니다. 직접 파일을 업로드해 주세요."
                )
            st.caption(
                "실제 출처 링크에서 공고를 가져옵니다. AI 분석 시 API가 호출됩니다."
            )
        else:
            uploaded = st.file_uploader(
                "공문서 또는 데이터셋",
                type=["pdf", "zip", "txt"],
                help="PDF 최대 20MB · 데이터셋 TXT/ZIP · HWP/HWPX는 PDF로 변환해 주세요.",
            )
            st.caption("PDF · TXT · ZIP / 최대 20MB · HWP는 PDF로 변환")
            if uploaded:
                content = uploaded.getvalue()
                file_name = uploaded.name
                if not file_name.lower().endswith(".pdf"):
                    try:
                        cases = load_uploaded_cases(uploaded)
                    except (ValueError, OSError, EOFError, zipfile.BadZipFile):
                        st.error(
                            "데이터셋을 읽지 못했습니다. 공고명·출처 링크·‘사용자 상황 (Persona):’이 있는 TXT 또는 ZIP인지 확인해 주세요."
                        )
                        content = b""
        if cases:
            case = st.selectbox("케이스 선택", cases, format_func=lambda c: c.name)
            file_name = case.name
            st.markdown(f"**{case.notice_title}**")
            st.link_button("공고 출처 열기 ↗", case.source_url)
        st.divider()
        st.markdown("### 내 정보")
        persona = st.text_area(
            "사용자 정보",
            value=case.persona if case else "",
            key=f"persona:{mode}:{file_name}",
            placeholder="예: 만 24세, 서울 거주 대학생입니다.\n창업에 관심이 있고 사업자 등록은 아직 하지 않았어요.",
            height=155,
            label_visibility="collapsed",
        )
        st.caption(
            "나이, 거주지, 소속 등 자격 판단에 필요한 정보를 적어 주세요. 비우면 공고 요약만 제공합니다."
        )
        if uploaded and uploaded.name.lower().endswith(".pdf"):
            no_llm = st.toggle(
                "원문만 확인 · API 호출 없음",
                help="텍스트를 추출해 페이지·조항별로 확인합니다. AI 판정은 실행하지 않습니다.",
            )
        st.caption(
            "AI 분석 시 문서·사용자 정보가 Gemini로 전송됩니다. 주민번호 등 민감한 정보는 제외해 주세요."
        )
        key_available = bool(get_api_key())
        if not key_available and not no_llm:
            st.warning(
                "API 키가 설정되지 않았습니다. PDF를 올리고 ‘원문만 확인’을 켜면 바로 사용할 수 있어요."
            )
        run = st.button(
            "원문 확인하기" if no_llm else "공고 검토 시작하기 →",
            type="primary",
            width="stretch",
            disabled=not content or (not no_llm and not key_available),
        )
        st.caption("원문 확인은 즉시 · AI 검토는 약 2–4분")

    key = analysis_key(content, persona, no_llm, case)
    with right:
        if run:
            st.session_state.pop("result", None)
            st.session_state.pop("analysis_failure", None)
            st.session_state.result_key = key
            result: dict = {}
            with st.status("공고를 검토하고 있습니다", expanded=True) as status:
                try:
                    if case:
                        status.write("공고 출처를 확인하고 내용을 정리합니다.")
                        result["parsed"] = parse_notice(case).model_dump()
                    else:
                        status.write("문서를 읽고 원문을 확인합니다.")
                        result["parsed"] = parse_pdf(uploaded, no_llm)
                    if persona.strip() and not no_llm:
                        doc = ParsedDocument.model_validate(result["parsed"])
                        result |= judge(doc, persona, on_step=status.write)
                    status.update(
                        label="검토가 완료되었습니다", state="complete", expanded=False
                    )
                except Exception as error:  # noqa: BLE001 - UI 경계에서 원문 오류를 숨기고 안전한 안내로 변환
                    status.update(
                        label="검토를 완료하지 못했습니다",
                        state="error",
                        expanded=False,
                    )
                    st.session_state.analysis_failure = analysis_error(error)
            if result:
                st.session_state.result = result
        if st.session_state.get("result_key") == key:
            if st.session_state.get("analysis_failure"):
                st.error(st.session_state.analysis_failure)
                if st.session_state.get("result"):
                    st.info(
                        "완료된 공고 요약까지 표시합니다. 최종 판정은 생성되지 않았습니다."
                    )
            if st.session_state.get("result"):
                if no_llm:
                    st.subheader("문서 원문")
                    st.caption(
                        "API 호출 없이 추출한 원문입니다. AI 검토 결과는 포함되지 않습니다."
                    )
                show_result(
                    st.session_state.result,
                    file_name,
                    case.ideal_answer if case else None,
                )
            else:
                show_empty_state()
        else:
            show_empty_state()
    st.html(
        '<div class="footer"><span>공고핏 · NOTICE FIT</span><span>판단의 근거는 공고에, 선택의 중심은 나에게.</span></div>'
    )
    with st.expander("분석 설정 및 이용 안내"):
        st.caption(
            f"모델 {get_model()} · 토론 {load_config()['debate']['turns']}턴 · Parser → Pro ↔ Critique → Writer"
        )
        st.caption(
            "키 설정 여부만 확인하며 호출 가능 여부는 분석 시 확인합니다. 무료 키는 검색 도구 사용 또는 호출 한도에 제한이 있을 수 있습니다."
        )
        st.caption(
            "최종 지원 자격과 일정은 반드시 발행 기관의 원문으로 확인하세요. 업로드 내용은 별도 파일로 보관하지 않으며, 결과는 현재 세션에서 확인합니다."
        )


if __name__ == "__main__":
    main()
