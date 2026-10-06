# roleplay-memory-system

롤플레이 채팅 시스템과 `memory system`을 설계하고, 설계에 쓴 수치를 실제로 재서 검증한 기록이다.
설계 문서는 따로 있고, 이 저장소에는 그 문서의 수치를 재현할 수 있는 코드와 측정 결과를 담았다.

## 설계 요약

- 메인 모델 Gemini 2.5 Pro, 입력 14,220토큰 / 평균 출력 800토큰 기준으로 턴 원가를 계산했다
- `memory`를 상태(L1 `profile`) · 사건(L2 `episodes`) · 원문(L3 `recall`) 세 계층으로 나눴다
- 매 턴 바뀌지 않는 구간을 앞에 모아 `cacheable prefix` 7,810토큰을 만들고, L3와 대화 내역은 `dynamic tail`로 보낸다

## 구조

| 경로 | 내용 |
|---|---|
| `output/assets/` | 아키텍처 다이어그램 |
| `prompts/system.hbs` | 롤플레이 시스템 프롬프트 (핸들바 템플릿) |
| `scripts/` | 원가 계산, 토큰 측정, 평가 스크립트 |
| `tests/cases` · `tests/fixtures` | 검증 케이스와 고정 입력 |
| `tests/results` | 측정 결과 로그 |
| `docs/03-assumptions.md` | 가정값 정의 — 모든 수치의 출처 |
| `docs/05-pricing.md` | 모델 가격 확인 기록 (출처·확인일) |

## 주요 스크립트

**원가**

| 스크립트 | 하는 일 |
|---|---|
| `cost_model.py` | 턴 원가, 민감도, 손익분기, 가격 근거를 전부 계산한다 |
| `verify_cost.py` | 같은 상수를 다른 식으로 다시 계산해 독립 검산한다 (`cost_model`을 쓰지 않는다) |

**프롬프트·토큰**

| 스크립트 | 하는 일 |
|---|---|
| `prompt_render.py` | 핸들바 템플릿을 렌더링한다. UGC 값은 XML `escape` 후 조립 |
| `count_tokens.py` | 문자 수 기반 토큰 추정 |
| `measure_prompt_tokens.py` | API `tokenizer`로 실측. `--template`으로 변형 비교 |

**평가**

| 스크립트 | 하는 일 |
|---|---|
| `run_live_tests.py` | 회귀 케이스 실호출 |
| `evaluate_extraction_quality.py` | Flash-Lite 추출 품질 (합성 10건 × seed 3) |
| `evaluate_embedding_retrieval.py` · `evaluate_retrieval.py` | L3 검색 정확도 — `embedding` 대 BM25 |
| `response_guard.py` · `test_response_guard_local.py` | 출력 검사 규칙과 회귀 |
| `memory_sim.py` · `test_memory_local.py` | `memory` 참조 구현과 동작 검증 18건 |
| `measure_extraction_latency.py` | 추출 호출 `latency` 측정 |

**캐시 측정**

`test_cache_turns.py`, `test_cache_size.py`, `probe_direct_cache.py`, `test_openrouter_cache_control.py` — `implicit` / `explicit caching` 동작과 히트율을 확인한다.

## 실행

Python 3.11 이상이 필요하다.

원가 계산과 로컬 검증은 **의존성 설치 없이** 바로 돌아간다.

```bash
python3 scripts/cost_model.py               # 원가표 전체
python3 scripts/verify_cost.py              # 독립 검산
python3 scripts/test_memory_local.py        # memory 참조 구현 18건
python3 scripts/test_response_guard_local.py
```

모델을 호출하는 스크립트는 키가 필요하다.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # API 키 입력
```

OpenRouter를 경유하는 호출은 표준 라이브러리만 쓰고, `google-genai`는 Gemini API를 직접 부르는 스크립트에만 필요하다. 키가 없으면 실행하지 않고 멈춘다.

## 검증 범위

| 방법 | 내용 |
|---|---|
| 실제 API 호출 | 회귀 20건, 연속 8턴 `cache` 측정, `prompt injection` 6회, Flash-Lite 추출 30건, 검색 18질의, `system prompt` 토큰 측정 |
| 다른 모델로 대신 측정 | 추출 `latency` 20회 — 채택 모델의 값이 아니라 초기값을 정하기 위한 참고치 |
| 모델 호출 없음 | `memory` 참조 구현 18건, `response guard` 5건, BM25 검색 18질의, 원가 독립 검산 |

호출은 모두 OpenRouter를 거쳤으므로 운영 환경을 그대로 재현한 결과는 아니다. 통과한 결과도 검증에 사용한 입력에서만 확인된 것이며, 한계는 설계 문서에 따로 정리했다.
