"""전체 파이프라인: Parser → Pro ↔ Critique → Writer.

사용법:
  uv run python pipeline.py 데이터셋.zip               # 6개 케이스 전부
  uv run python pipeline.py 데이터셋.zip --case 2      # 2번 케이스만
  uv run python pipeline.py 데이터셋.zip --turns 1     # 토론 1턴 (빠르게)
결과: outputs/<케이스>.result.json
"""

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

from dataset import Case, load_case, load_cases
from debate_agent import run_debate
from llm import load_config
from parser_agent.agent import parse_notice
from parser_agent.schema import ParsedDocument
from writer_agent import write_report


def judge(
    doc: ParsedDocument, persona: str, turns: int | None = None, on_step: Callable[[str], None] | None = None
) -> dict:
    """Parser 결과 + 사용자 정보로 토론과 최종 판정을 만든다."""
    step = on_step or (lambda message: None)
    history = run_debate(doc, persona, turns or load_config()["debate"]["turns"], step)
    step("Writer: 최종 판정")
    report = write_report(doc, persona, history)
    return {"debate_history": history, "report": report.model_dump()}


def run_case(case: Case, turns: int | None = None, on_step: Callable[[str], None] | None = None) -> dict:
    step = on_step or (lambda message: None)
    step("Parser: 출처 링크에서 공고 가져와 구조화")
    doc = parse_notice(case)
    return {"case": case.name, "parsed": doc.model_dump(), **judge(doc, case.persona, turns, step)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Parser → Pro ↔ Critique → Writer 전체 실행")
    ap.add_argument("path", type=Path, help="데이터셋 .zip 또는 .txt")
    ap.add_argument("--case", type=int, help="zip에서 N번째 케이스만 (1부터)")
    ap.add_argument("--turns", type=int, help="토론 턴 수 (기본: config.yaml)")
    args = ap.parse_args()

    cases = load_cases(args.path) if args.path.suffix.lower() == ".zip" else [load_case(args.path)]
    if args.case:
        cases = [cases[args.case - 1]]
    output_dir = Path(load_config()["parser"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    for case in cases:
        print(f"▶ {case.name}", flush=True)
        try:
            result = run_case(case, args.turns, lambda message: print(f"  · {message}", flush=True))
        except Exception as e:  # 한 케이스가 실패해도 나머지는 계속
            print(f"  실패: {type(e).__name__}: {e}", flush=True)
            continue
        path = output_dir / f"{_safe_name(case.name)}.result.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        report = result["report"]
        print(f"  판정: {report['recommendation']} {report['verdict_label']}", flush=True)
        print(f"  저장: {path}", flush=True)


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name)


if __name__ == "__main__":
    main()
