"""사용법:
  uv run python -m parser_agent 데이터셋.zip             # 데이터셋 전체 (케이스마다 JSON 하나)
  uv run python -m parser_agent "데이터셋 1 ....txt"      # 데이터셋 케이스 하나
  uv run python -m parser_agent 공고문.pdf [--no-llm]     # PDF
"""

import argparse
import json
import re
from pathlib import Path

from dataset import load_case, load_cases
from llm import load_config
from parser_agent.agent import parse_document, parse_notice
from parser_agent.reader import read_document, split_chunks
from parser_agent.schema import ParsedDocument


def main() -> None:
    ap = argparse.ArgumentParser(prog="parser_agent", description="공문서를 ParsedDocument JSON으로 변환")
    ap.add_argument("path", type=Path, help=".pdf, 데이터셋 .txt 또는 .zip")
    ap.add_argument("-o", "--output-dir", type=Path, help="기본: config.yaml의 parser.output_dir")
    ap.add_argument("--no-llm", action="store_true", help="PDF만: LLM 없이 raw_chunks만 출력 (파일 읽기 확인용)")
    args = ap.parse_args()
    output_dir = args.output_dir or Path(load_config()["parser"]["output_dir"])
    suffix = args.path.suffix.lower()

    if suffix == ".pdf":
        if args.no_llm:
            chunks = split_chunks(read_document(args.path))
            print(json.dumps([c.model_dump() for c in chunks], ensure_ascii=False, indent=2))
            return
        save(output_dir / f"{args.path.stem}.parsed.json", parse_document(args.path))
    elif suffix in (".zip", ".txt"):
        cases = load_cases(args.path) if suffix == ".zip" else [load_case(args.path)]
        for case in cases:
            print(f"▶ {case.name}", flush=True)
            try:
                save(output_dir / f"{_safe_name(case.name)}.parsed.json", parse_notice(case))
            except Exception as e:  # noqa: BLE001 - 한 케이스가 실패해도 나머지는 계속
                print(f"  실패: {type(e).__name__}: {e}", flush=True)
    else:
        ap.error(f"지원하지 않는 형식입니다: {suffix}")


def save(path: Path, doc: ParsedDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    print(f"  저장: {path}", flush=True)


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name)


if __name__ == "__main__":
    main()
