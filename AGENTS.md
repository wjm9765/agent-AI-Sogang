# AGENTS.md

이 저장소에서 작업하는 사람과 코딩 에이전트(Claude Code, Codex 등)가 가장 먼저 읽는 프로젝트 개요입니다.

## 프로젝트 개요

공문서(공고문·안내문 등, 주로 `.pdf`, 그 외 `.hwpx`)와 **사용자 정보**를 입력받아, 그 문서가 사용자에게 맞는지 판단하고 **추천 여부와 근거, 관련 정보**를 정리해 주는 멀티 에이전트 시스템입니다.

- **입력**: 공문서 파일 + 사용자 정보(나이, 소속, 관심사 등 프로필)
- **출력**: 추천 / 비추천 판단, 판단 근거, 주의사항(신청 기간, 자격 요건, 일정 변경 등)

## 파이프라인

```
[공문서] ──► Parser ──► 문서 데이터(JSON)
                              │
[사용자 정보] ─────────────────┤
                              ▼
                             Pro ──► 추천 근거
                              │
                              ▼
                          Critique ──► Pro 의견 검토 (Parser 데이터 기준)
                              │
                              ▼
                           Writer ──► 최종 추천 여부 + 근거 정리
```

| 에이전트 | 역할 | 입력 | 출력 | Tool |
|---|---|---|---|---|
| **Parser** | 문서를 읽어 구조화된 데이터로 변환 (Python + LLM) | 문서 파일 | 문서 JSON | 문서 파싱, web search |
| **Pro** | 사용자 정보를 기준으로 추천 근거 추출 | 문서 JSON + 사용자 정보 | 근거 목록 | TBD |
| **Critique** | Pro 의견을 Parser 데이터를 근거로 비판적으로 검토 | 문서 JSON + Pro 결과 | 반박 / 보완 의견 | web search |
| **Writer** | 전체 결과를 사용자가 읽기 좋게 정리 | 전체 결과 | 최종 응답 | TBD |

Web search는 문서 밖의 최신 정보를 확인할 때 씁니다. 예: 일정이 공휴일과 겹쳐 취소·변경됐는지.

## LLM

- **Gemini** 사용. 모든 에이전트가 **같은 모델 하나**를 쓰고, 모델명은 `config.yaml`의 `llm.model`에서만 바꿉니다. 코드에 하드코딩하지 않습니다.
- API 키는 `.env`의 `GEMINI_API_KEY`로 관리하고 **절대 커밋하지 않습니다.** (`.env.example` 참고)
- 현재 모델은 `gemini-3.8-flash`. Parser가 Google 검색 도구를 쓰므로 유료 키가 필요합니다(무료 키는 gemini-3.x 검색이 429).
- 클라이언트와 모델명은 `llm.py`의 `get_client()`, `get_model()`로 가져옵니다.
- 에이전트 하나 = 시스템 프롬프트 + tool(function call) 정의 + 입출력 JSON 스키마.

## 개발 환경

[uv](https://docs.astral.sh/uv/)로 가상환경(`.venv`)과 의존성을 관리합니다. `pip install`이나 `requirements.txt` 대신 `uv`를 씁니다.

```bash
uv sync                    # .venv 생성 + 의존성 설치 (Python 3.12)
cp .env.example .env       # .env에 GEMINI_API_KEY 입력
uv add <패키지>             # 의존성 추가 → pyproject.toml, uv.lock 같이 커밋
uv run python -m parser_agent 데이터셋.zip   # Parser CLI
uv run python pipeline.py 데이터셋.zip --case 1   # 전체 파이프라인 CLI
uv run streamlit run app.py                 # 전체 UI (판정까지)
```

## 테스트 데이터

`데이터셋.zip` 안의 `.txt` 6개. 각 파일 = 공고명 + 출처 링크 + 사용자 상황(Persona) + 이상적인 답(Ideal Answer). 공고문 본문은 없고 출처 링크만 있습니다.

```python
from dataset import load_cases
cases = load_cases("데이터셋.zip")   # case.notice_title, case.source_url, case.persona, case.ideal_answer
```

`ideal_answer`는 평가용 정답이므로 에이전트 입력에 넣지 않습니다.

## 할 일

- [ ] 데이터 구성: 샘플 공문서, 사용자 정보 예시, 기대 출력
- [ ] 에이전트 정의 및 프롬프팅: Parser / Pro / Critique / Writer
- [ ] GitHub 협업 규칙 정리

## 담당

| 파트 | 담당 | 브랜치 | 문서 |
|---|---|---|---|
| Parser | wjm | `feature/parser` | [docs/parser.md](docs/parser.md) |
| Pro | TBD | | |
| Critique | TBD | | |
| Writer | TBD | | |

## 협업 규칙

- `main`이 기준 브랜치입니다. 각자 `feature/<파트>` 브랜치에서 작업하고 `main`으로 PR을 엽니다.
- 에이전트끼리는 JSON으로 데이터를 주고받습니다. 스키마를 바꾸면 해당 `docs/` 문서도 같이 고치고, PR 설명에 변경 내용을 적어 다른 파트 담당자가 알 수 있게 합니다.
- 각 에이전트의 상세 설계(역할, 입출력 스키마, 프롬프트, tool)는 `docs/<에이전트>.md`에 둡니다.
