# Streamlit 배포와 오프라인 검증

## 실행과 검증

```bash
uv sync --locked
uv run python -m compileall -q app.py llm.py dataset.py pipeline.py parser_agent debate_agent.py writer_agent.py
uv run python -m unittest discover -s tests -v
uv run streamlit run app.py
```

테스트는 실제 Gemini·검색 호출 없이 스키마, 데이터셋 6개, 전체 판정 파이프라인(mock), 정답 누출 방지,
업로드 파일명 처리, 입력 변경 시 결과 무효화, 키 우선순위, UI 시작·샘플 선택·429 오류를 확인한다.
통과해도 실제 모델 접근 권한이나 무료 키의 검색·호출 한도를 검증한 것은 아니다.

## Community Cloud

- Repository: `wjm9765/agent-AI-Sogang`
- Branch: `main`
- Main file: `app.py`
- Python: `3.12`
- 의존성은 `uv.lock`과 `pyproject.toml`을 사용한다. 별도 requirements.txt는 만들지 않는다.
- Advanced settings → Secrets에 다음 TOML을 설정한다. 실제 키는 `.env`에서 안전하게 옮기고 Git에 넣지 않는다.

```toml
GEMINI_API_KEY = "실제 키"
```

Cloud Secrets의 루트 키는 환경 변수로 제공된다. `llm.get_api_key()`는 환경 변수를 먼저 읽고,
없을 때만 로컬 `.env`를 읽는다. `.env`와 `.streamlit/secrets.toml`은 Git에서 제외한다.
모든 에이전트의 모델은 기존처럼 `config.yaml`의 `llm.model` 한 곳에서 설정한다.

## UI 동작

- PDF: 사용자 정보를 넣으면 판정, 비우면 공고 요약. ‘원문만 확인’은 키 없이 동작한다.
- 데이터셋 TXT/ZIP: 공고명·출처 링크·사용자 상황이 필요하다. 기본 ZIP 6개도 화면에서 선택할 수 있다.
- 평가용 ideal_answer는 결과의 접힌 비교란에서만 표시하며 에이전트에 전달하지 않는다.
- 파일 내용·사용자 정보·선택 케이스·모드·설정이 바뀌면 이전 결과를 표시하지 않는다.
- API 오류 원문은 표시하지 않는다. 한도·인증·모델·서버 오류별 안내를 표시한다.
- 판정 단계에서 실패하면 이미 완료된 문서 요약을 유지하며 최종 판정이 없음을 알린다.
- 최대 업로드 20MB, ZIP 내부 TXT 총 10MB. HWP/HWPX 직접 업로드는 지원하지 않는다.
- 업로드 PDF는 임시 디렉터리에서 읽은 뒤 지우며 결과는 현재 Streamlit 세션에 둔다.

배포 후 메인 화면, 샘플 케이스 선택, 비밀 값 미노출, PDF 원문 모드를 확인한다.
유료/가용 키로 실제 분석까지 확인하려면 별도의 API 실행 검증이 필요하다.

공식 문서: [배포](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy),
[Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management),
[의존성](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).
