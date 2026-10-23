# Writer 구현

`writer_agent.write_report(doc, persona, debate_history)`가 Parser 문서, 사용자 정보, 토론 기록을
`llm.generate_json()`에 전달해 `WriterOutput`을 생성한다. 모든 에이전트의 모델은 `config.yaml`을 따른다.

| 필드 | 타입 | 의미 |
|---|---|---|
| recommendation | GO / HOLD / STOP | 지원 권장 / 보완 후 지원 / 비추천 |
| verdict_label | string | 판정 한 줄 |
| confidence_score | integer, 0–100 | 모델이 제시한 확신도. 실제 선정 확률이 아님 |
| summary | string | 최종 판단과 근거 요약 |
| key_reasons | string[] | 공고 근거를 포함한 핵심 이유 |
| warnings | string[] | 기한·서류·필수 자격 등 주의사항 |
| action_items | string[] | 다음에 준비할 일 |

`confidence_score`는 Pydantic의 `ge=0`, `le=100`으로 범위를 검증한다.
평가용 데이터셋의 `ideal_answer`는 입력에 포함하지 않는다.
상세 프롬프트 초안은 기존 [설계 문서](../writer_agent_documentation.md)를 참고한다.
