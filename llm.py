"""모든 에이전트가 공유하는 LLM 설정.

- 모델: config.yaml의 llm.model (에이전트 전체가 같은 모델 사용)
- API 키: .env의 GEMINI_API_KEY
"""

import os
import threading
from functools import cache
from pathlib import Path
from typing import TypeVar

import yaml
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
T = TypeVar("T", bound=BaseModel)


@cache
def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_model() -> str:
    return load_config()["llm"]["model"]


_client: genai.Client | None = None
_client_key: str | None = None
_client_lock = threading.Lock()


def get_client() -> genai.Client:
    """Client는 키마다 하나만 만들어 계속 쓴다. .env의 키가 바뀌면 새로 만든다(Streamlit 재시작 없이 반영).

    여러 개를 만들면 참조가 끊긴 Client가 정리되면서 연결을 닫아 요청이 실패한다(스레드 동시 호출 포함).
    """
    global _client, _client_key
    with _client_lock:
        load_dotenv(ROOT / ".env", override=True)
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY가 없습니다. .env.example을 복사해 .env를 만들고 키를 넣으세요.")
        if _client is None or api_key != _client_key:
            _client_key = api_key
            # 503(과부하) 같은 서버 쪽 일시 오류만 재시도한다. 429(할당량 초과)는 기다려도 안 풀리므로 바로 실패시킨다.
            retry = types.HttpRetryOptions(attempts=4, http_status_codes=[500, 502, 503, 504])
            _client = genai.Client(api_key=api_key, http_options=types.HttpOptions(retry_options=retry))
        return _client


def generate_json(schema: type[T], contents: str, system_instruction: str) -> T:
    """structured output으로 한 번 호출하고 응답을 schema로 검증해 돌려준다."""
    response = get_client().models.generate_content(
        model=get_model(),
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    return schema.model_validate_json(response.text)
