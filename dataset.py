"""테스트 데이터셋(데이터셋.zip 안의 .txt) 읽기.

각 .txt = 공고명 + 출처 링크 + 사용자 상황(Persona) + 이상적인 답(Ideal Answer).
공고문 본문은 들어 있지 않으므로 Parser가 출처 링크에서 가져온다.
"""

import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

PERSONA_HEADER = "사용자 상황 (Persona):"
ANSWER_HEADER = "이상적인 답 (Ideal Answer):"


@dataclass
class Case:
    name: str  # 예: "데이터셋 1: 청년 주거 복지 지원"
    notice_title: str  # 공고명
    source_url: str
    persona: str  # 사용자 정보. Pro·Critique 입력
    ideal_answer: str  # 평가용 정답. 에이전트 입력에 넣지 않는다.


def parse_case(text: str) -> Case:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not text.strip() or PERSONA_HEADER not in text:
        raise ValueError(f"데이터셋 형식 오류: '{PERSONA_HEADER}'와 공고명·출처 링크가 필요합니다.")
    head, _, rest = text.partition(PERSONA_HEADER)
    persona, _, answer = rest.partition(ANSWER_HEADER)
    url = re.search(r"https?://[^)\s]+", _field(head, "출처 링크"))
    if not url:
        raise ValueError("데이터셋 형식 오류: 출처 링크에 http(s) URL이 필요합니다.")
    if not persona.strip():
        raise ValueError("데이터셋 형식 오류: 사용자 상황이 비어 있습니다.")
    return Case(
        name=head.strip().splitlines()[0].strip(),
        notice_title=_field(head, "공고명"),
        source_url=url.group(0),
        persona=_squeeze(persona),
        ideal_answer=_squeeze(answer),
    )


def load_case(path: str | Path) -> Case:
    return parse_case(Path(path).read_text(encoding="utf-8-sig"))


def load_cases(zip_path: str | Path | IO[bytes]) -> list[Case]:
    """zip 안의 .txt를 파일명 순서대로 읽는다. 경로나 업로드된 파일 객체 모두 받는다."""
    cases = []
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if _zip_name(i).endswith(".txt") and "__MACOSX" not in _zip_name(i)]
        if not infos:
            raise ValueError("ZIP 안에 데이터셋 .txt 파일이 없습니다.")
        if sum(i.file_size for i in infos) > 10 * 1024 * 1024:
            raise ValueError("압축 해제한 데이터셋은 총 10MB 이하여야 합니다.")
        for info in sorted(infos, key=_zip_name):
            cases.append(parse_case(zf.read(info).decode("utf-8-sig")))
    return cases


def _field(text: str, label: str) -> str:
    match = re.search(rf"^{label}\s*:\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"데이터셋 형식 오류: '{label}:' 줄이 없습니다.")
    return match.group(1).strip()


def _squeeze(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())


def _zip_name(info: zipfile.ZipInfo) -> str:
    # macOS에서 만든 zip은 UTF-8 플래그 없이 한글 파일명을 넣어서 cp437로 잘못 읽힌다.
    name = info.filename if info.flag_bits & 0x800 else info.filename.encode("cp437").decode("utf-8", "replace")
    return unicodedata.normalize("NFC", name)
