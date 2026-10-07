# CLAUDE.md

롤플레이 채팅 시스템과 `memory system` 설계 수치를 재현·검증하는 저장소다. 서비스 코드가 아니라 설계 문서의 근거(원가 계산, 측정 스크립트, 결과 로그)를 담는다. 구조와 스크립트 목록은 `README.md`를 본다.

## 검증

```bash
bash scripts/check_local.sh   # 구문 검사 + 원가 계산·검산 + memory 18건 + response guard 5건 + 플레이그라운드 엔진 51건
```

- 키 없이 1초 안에 끝나고 파일을 쓰지 않는다. 작업을 마치기 전에 반드시 통과시킨다.
- `scripts/*.py`, `prompts/*`, `docs/*.md`, `tests/cases/*`, `tests/fixtures/*`, `demo/*`를 수정하면 PostToolUse hook(`.claude/hooks/check_after_edit.sh`)이 자동으로 실행한다. 실패하면 출력이 그대로 돌아오므로 원인을 고친 뒤 진행한다.
- 인터프리터는 `.venv/bin/python` → `python3.11` → `python3` 순으로 고른다. 시스템 `python3`는 3.9라서 README의 3.11 조건과 다르다.

## 규칙

- **수치의 출처는 `docs/03-assumptions.md` 하나다.** 가정값을 바꾸면 이 파일을 먼저 고치고, `scripts/cost_model.py`와 `scripts/verify_cost.py`의 상수를 함께 맞춘다. `verify_cost.py`는 `cost_model`을 import하지 않는 독립 검산이므로 이 구조를 유지한다.
- 가격을 바꾸면 `docs/05-pricing.md`에 출처와 확인일을 남긴다.
- 측정하지 않은 수치를 문서나 코드에 쓰지 않는다. 값이 없으면 `llm_client.UNKNOWN`("확인 불가")처럼 비워 둔다.
- `tests/results/`는 과거 측정 기록이다. 기존 파일을 고치거나 지우지 말고, 새 측정은 타임스탬프가 붙은 새 파일로 남긴다.
- 외부 의존성을 추가하지 않는다. OpenRouter 호출과 로컬 검증은 표준 라이브러리만 쓰고, `google-genai`는 Gemini API를 직접 부르는 스크립트에만, `gradio`는 `demo/app.py`에만 쓴다.

## 실호출 스크립트

모델 API를 호출하는 스크립트는 비용이 들고 결과가 매번 달라진다. **사용자가 요청하지 않으면 실행하지 않는다.** 실행 전에 대상 케이스 수, seed, `--budget-usd` 상한을 먼저 알린다.

- 해당 스크립트: `run_live_tests.py`, `evaluate_extraction_quality.py`, `evaluate_embedding_retrieval.py`, `measure_*.py`, `probe_*.py`, `test_cache_*.py`, `test_explicit_cache.py`, `test_injection_profile.py`, `test_length_variants.py`, `test_openrouter_cache_control.py`, `run_demo_smoke.py`, `evaluate_user_action_judge.py`, `evaluate_memory_extraction.py`, `scripts/run_*.sh`
- 확인 창(`ask` 규칙)은 쓰지 않는다. 사용자가 작업을 맡기며 실행을 허락한 경우에만 예산 상한을 걸고 실행한다.
- `scripts/run_*.sh`는 저장소 루트의 `.env`를 읽고 `.venv/bin/python`으로 실행한다.

## 플레이그라운드 (`demo/`)

- `prompts/system.hbs`는 측정 기록(토큰 902/879)과 연결된 원본이라 고치지 않는다. 플레이그라운드 프롬프트는 `demo/prompts/system.hbs`에서 바꾼다.
- 대화 처리는 `demo/engine.py`, 화면은 `demo/app.py`가 맡는다. 엔진은 gradio 없이 테스트할 수 있게 유지한다.
- 키 없이 화면을 볼 때는 `DEMO_FAKE_LLM=1 .venv/bin/python demo/app.py`를 쓴다.
- 배포는 Render 무료 웹 서비스(`render.yaml`)다. GitHub `main`에 push하면 자동으로 다시 배포되므로, push는 외부 공개 작업으로 보고 실행 직전에 사용자 확인을 받는다.
- `OPENROUTER_API_KEY`는 Render 대시보드에만 둔다. `render.yaml`에 값을 쓰지 않는다(`sync: false`).

## 보안

- `.env`를 읽거나 출력하지 않는다(`deny` 규칙). 키 이름은 `.env.example`에서 확인한다.
- API 키를 로그, 예외 메시지, 결과 파일에 남기지 않는다. 호출 결과는 `llm_client.scrub()`을 거친다.
- 루트의 PDF(`/*.pdf`)는 로컬 참고 문서라 git에 올리지 않는다.
