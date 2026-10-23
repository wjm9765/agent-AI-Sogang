"""회귀 검사. 모든 Gemini·검색 호출은 mock 처리하며 실제 키를 사용하지 않는다."""

import io
import os
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

import app
import llm
from dataset import load_cases, parse_case
from debate_agent import CritiqueOutput, ProOutput, get_calendar_fact
from parser_agent.agent import _references
from parser_agent.reader import Page, split_chunks
from parser_agent.schema import ParsedDocument, Source, StructuredSummary
from pipeline import judge, run_case
from writer_agent import WriterOutput

ROOT = Path(__file__).resolve().parents[1]


def document():
    return ParsedDocument(
        source=Source(file_name="test.pdf", format="pdf", pages=1),
        structured_summary=StructuredSummary(
            title="테스트 공고",
            issuer=None,
            issued_date=None,
            summary="테스트",
            eligibility=[],
            benefits=[],
            schedule=[],
            how_to_apply=[],
            required_documents=[],
            cautions=[],
            contact=[],
            missing_fields=[],
        ),
        raw_chunks=split_chunks([Page(1, "1. 지원대상\n대학생\n2. 신청방법\n온라인")]),
    )


def report():
    return WriterOutput(
        recommendation="HOLD",
        verdict_label="추가 확인",
        confidence_score=70,
        summary="추가 확인이 필요합니다.",
        key_reasons=["요건 확인"],
        warnings=["마감일 확인"],
        action_items=["서류 준비"],
    )


class OfflineTests(unittest.TestCase):
    def test_real_pdf_can_be_read_without_api(self):
        # 외부 도구 없이 만든 작은 PDF로 실제 pdfplumber 경로를 확인한다.
        stream = b"BT /F1 12 Tf 40 750 Td (1. Application) Tj 0 -20 Td (University students) Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream",
        ]
        data = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for number, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
        xref = len(data)
        data += b"xref\n0 6\n0000000000 65535 f \n"
        for offset in offsets[1:]:
            data += f"{offset:010d} 00000 n \n".encode()
        data += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
        upload = SimpleNamespace(name="notice.pdf", getvalue=lambda: bytes(data))
        with patch(
            "google.genai.Client", side_effect=AssertionError("Network forbidden")
        ):
            parsed = app.parse_pdf(upload, True)
        self.assertIn("University students", parsed["raw_chunks"][0]["text"])

    def test_builtin_cases_and_invalid_inputs(self):
        cases = load_cases(ROOT / "데이터셋.zip")
        self.assertEqual(len(cases), 6)
        for case in cases:
            self.assertTrue(
                case.notice_title
                and case.source_url
                and case.persona
                and case.ideal_answer
            )
        for text in [
            "",
            "내용",
            "제목\n공고명: 공고\n출처 링크: 없음\n사용자 상황 (Persona):\n대학생",
        ]:
            with self.assertRaises(ValueError):
                parse_case(text)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w"):
            pass
        data.seek(0)
        with self.assertRaisesRegex(ValueError, "TXT|txt"):
            load_cases(data)

    def test_env_precedes_dotenv_and_no_real_client_is_created(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "cloud-test-key"}),
            patch(
                "llm.dotenv_values", return_value={"GEMINI_API_KEY": "local-test-key"}
            ),
        ):
            self.assertEqual(llm.get_api_key(), "cloud-test-key")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "llm.dotenv_values", return_value={"GEMINI_API_KEY": "local-test-key"}
            ),
        ):
            self.assertEqual(llm.get_api_key(), "local-test-key")
        with (
            patch("llm.get_api_key", return_value=None),
            patch("llm.genai.Client") as client,
        ):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                llm.get_client()
            client.assert_not_called()

    def test_result_identity_tracks_content_profile_mode_and_case(self):
        key = app.analysis_key(b"one", "persona", False, None)
        self.assertNotEqual(key, app.analysis_key(b"two", "persona", False, None))
        self.assertNotEqual(key, app.analysis_key(b"one", "new persona", False, None))
        self.assertNotEqual(key, app.analysis_key(b"one", "persona", True, None))

    def test_raw_pdf_upload_uses_safe_filename_and_no_llm(self):
        upload = SimpleNamespace(name="../test.pdf", getvalue=lambda: b"pdf-fixture")

        def read(path):
            self.assertEqual(path.name, "test.pdf")
            self.assertEqual(path.read_bytes(), b"pdf-fixture")
            return [Page(1, "1. 지원대상\n대학생")]

        with (
            patch("app.read_document", side_effect=read),
            patch("app.parse_document") as parse,
        ):
            result = app.parse_pdf(upload, True)
        self.assertEqual(result["raw_chunks"][0]["section"], "1. 지원대상")
        parse.assert_not_called()

    def test_pipeline_runs_all_stages_without_leaking_ideal_answer(self):
        case = load_cases(ROOT / "데이터셋.zip")[0]
        case.ideal_answer = "PRIVATE_IDEAL_ANSWER_SENTINEL"
        captured = []

        def generate(schema, contents, instruction):
            captured.append(contents)
            if schema is ProOutput:
                return ProOutput(
                    turn=1, overall_stance="확인", claims=[], rebuttal_to_critic=[]
                )
            if schema is CritiqueOutput:
                return CritiqueOutput(turn=1, is_fatal=False, risks=[])
            return report()

        with (
            patch("pipeline.parse_notice", return_value=document()),
            patch("debate_agent.generate_json", side_effect=generate),
            patch("debate_agent.web_search", return_value="확인된 변경 없음"),
            patch("writer_agent.generate_json", side_effect=generate),
        ):
            result = run_case(case, turns=2)
        self.assertEqual(
            [h["agent"] for h in result["debate_history"]],
            ["Pro", "Critique", "Pro", "Critique"],
        )
        self.assertEqual(result["report"]["recommendation"], "HOLD")
        self.assertEqual(len(captured), 5)
        self.assertNotIn(case.ideal_answer, "\n".join(captured))
        with self.assertRaises(ValueError):
            judge(document(), "persona", turns=0)

    def test_empty_metadata_and_confidence_validation(self):
        self.assertEqual(_references(SimpleNamespace(candidates=[])), [])
        values = report().model_dump()
        values["confidence_score"] = 101
        with self.assertRaises(ValidationError):
            WriterOutput.model_validate(values)
        self.assertEqual(get_calendar_fact([]), "(날짜 정보 없음)")

    def test_error_messages_never_include_raw_exception(self):
        for code in [429, 403, 404, 503, None]:
            error = RuntimeError("SECRET_API_KEY_AND_PROFILE")
            error.code = code
            with patch("app.get_api_key", return_value="test"):
                message = app.analysis_error(error)
            self.assertNotIn("SECRET_API_KEY_AND_PROFILE", message)
        self.assertIn("한도", app.analysis_error(SimpleNamespace(code=429)))

    def test_app_boot_and_browse_samples_make_no_calls(self):
        with (
            patch(
                "google.genai.Client", side_effect=AssertionError("Network forbidden")
            ),
            patch("llm.get_api_key", return_value=None),
        ):
            at = AppTest.from_file(str(ROOT / "app.py")).run(timeout=15)
            self.assertEqual(len(at.exception), 0)
            self.assertTrue(at.button[0].disabled)
            at.segmented_control[0].set_value("샘플 데이터셋").run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(len(at.selectbox[0].options), 6)
            self.assertTrue(at.text_area[0].value)
            at.selectbox[0].select_index(1).run()
            self.assertEqual(len(at.exception), 0)
            self.assertTrue(at.button[0].disabled)

    def test_app_quota_failure_is_readable(self):
        error = RuntimeError("SECRET_API_KEY")
        error.code = 429
        with (
            patch("llm.get_api_key", return_value="offline-test"),
            patch("parser_agent.agent.parse_notice", side_effect=error),
        ):
            at = AppTest.from_file(str(ROOT / "app.py")).run(timeout=15)
            at.segmented_control[0].set_value("샘플 데이터셋").run()
            at.button[0].click().run()
            self.assertEqual(len(at.exception), 0)
            self.assertTrue(any("호출 한도" in e.value for e in at.error))
            self.assertFalse(any("SECRET_API_KEY" in e.value for e in at.error))

    def test_app_renders_completed_report_and_invalidates_changed_persona(self):
        with (
            patch("llm.get_api_key", return_value="offline-test"),
            patch("parser_agent.agent.parse_notice", return_value=document()),
            patch(
                "pipeline.judge",
                return_value={"debate_history": [], "report": report().model_dump()},
            ),
        ):
            at = AppTest.from_file(str(ROOT / "app.py")).run(timeout=15)
            at.segmented_control[0].set_value("샘플 데이터셋").run()
            at.button[0].click().run()
            self.assertEqual(len(at.exception), 0)
            self.assertTrue(any("보완 후 지원" in e.value for e in at.warning))
            at.text_area[0].set_value("새로운 사용자 정보").run()
            self.assertEqual(len(at.exception), 0)
            self.assertFalse(any("보완 후 지원" in e.value for e in at.warning))

    def test_app_preserves_summary_when_judgment_fails(self):
        error = RuntimeError("SECRET_API_KEY")
        error.code = 429
        with (
            patch("llm.get_api_key", return_value="offline-test"),
            patch("parser_agent.agent.parse_notice", return_value=document()),
            patch("pipeline.judge", side_effect=error),
        ):
            at = AppTest.from_file(str(ROOT / "app.py")).run(timeout=15)
            at.segmented_control[0].set_value("샘플 데이터셋").run()
            at.button[0].click().run()
            self.assertEqual(len(at.exception), 0)
            self.assertTrue(
                any("최종 판정은 생성되지 않았습니다" in e.value for e in at.info)
            )
            self.assertTrue(any("테스트 공고" in e.value for e in at.subheader))


if __name__ == "__main__":
    unittest.main()
