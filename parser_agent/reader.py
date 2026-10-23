"""문서 파일을 페이지별 텍스트와 원문 조각(raw_chunks)으로 읽는다. LLM은 쓰지 않는다."""

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from parser_agent.schema import RawChunk

# 조항 제목. 예: "제3조(지원자격)", "제3조의2"
ARTICLE = re.compile(r"^\s*(제\s*\d+\s*조(?:의\s*\d+)?(?:\s*\([^)]*\))?)(?=\s|$)")
# 그 밖의 항목 제목으로 보는 짧은 줄. 예: "Ⅱ. 사업개요", "1. 지원대상", "□ 신청방법"
HEADING = re.compile(r"^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.|\d{1,2}\.\s|[□■◆◇])")
MAX_HEADING_LEN = 60


@dataclass
class Page:
    number: int
    text: str


def read_document(path: Path) -> list[Page]:
    if path.suffix.lower() == ".pdf":
        return read_pdf(path)
    raise ValueError(f"지원하지 않는 형식입니다: {path.suffix} (현재 .pdf만 지원)")


def read_pdf(path: Path) -> list[Page]:
    """표 바깥 본문은 줄 단위로, 표는 Markdown으로 바꿔 페이지 안 위치(위→아래) 순서대로 합친다."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()
            bboxes = [t.bbox for t in tables]
            body = page.filter(lambda obj, bboxes=bboxes: not _inside_any(obj, bboxes))
            blocks = [(line["top"], line["text"]) for line in body.extract_text_lines()]
            blocks += [(t.bbox[1], _table_to_markdown(t.extract())) for t in tables]
            blocks.sort(key=lambda block: block[0])
            pages.append(Page(number, "\n".join(text for _, text in blocks if text)))
    return pages


def split_chunks(pages: list[Page]) -> list[RawChunk]:
    """제목 줄(조항·항목)이 나오거나 페이지가 바뀌면 끊어서 원문 조각을 만든다."""
    chunks: list[RawChunk] = []
    section = "(제목 없음)"
    for page in pages:
        lines: list[str] = []
        for line in page.text.splitlines():
            if heading := _heading(line):
                _append_chunk(chunks, page.number, section, lines)
                lines = []
                section = heading
            lines.append(line)
        _append_chunk(chunks, page.number, section, lines)
    return chunks


def _append_chunk(chunks: list[RawChunk], page: int, section: str, lines: list[str]) -> None:
    text = "\n".join(lines).strip()
    if text:
        chunks.append(RawChunk(chunk_id=f"c{len(chunks):03d}", page=page, section=section, text=text))


def _heading(line: str) -> str | None:
    """제목 줄이면 섹션 이름을, 아니면 None을 돌려준다."""
    line = line.strip().lstrip("#* ").replace("**", "")  # 웹 공고의 Markdown 제목·굵게 표시 제거
    if match := ARTICLE.match(line):
        return re.sub(r"\s+", "", match.group(1))
    if len(line.strip()) <= MAX_HEADING_LEN and HEADING.match(line):
        return line.strip()
    return None


def _inside_any(obj: dict, bboxes: list[tuple]) -> bool:
    if "x0" not in obj:
        return False
    cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
    return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in bboxes)


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    rows = [[(cell or "").replace("\n", " ").strip() for cell in row] for row in rows if row]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)
