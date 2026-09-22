# 모델 가격 확인 기록

기억에 의존하지 않고 공식 문서에서 확인 후 기록. 확인일·URL 필수.
확인일: **2026-09-16** / 기준: Gemini Developer API, Standard(유료) 티어, USD per 1M tokens

## 1. 가격표

| 모델 | 용도 | Input | Output | 캐시 Input | 캐시 저장 | 출처 |
|---|---|---|---|---|---|---|
| Gemini 2.5 Pro (≤200k) | 메인 채팅 | **1.25** | **10.00** | **0.125** | 4.50 /1M/hr | [pricing] |
| Gemini 2.5 Pro (>200k) | — | 2.50 | 15.00 | 0.25 | 4.50 /1M/hr | [pricing] |
| Gemini 2.5 Flash | 요약/추출 후보 | **0.30** | **2.50** | 0.03 | 1.00 /1M/hr | [pricing] |
| Gemini 2.5 Flash-Lite | 요약/추출 후보 | **0.10** | **0.40** | 0.01 | 1.00 /1M/hr | [pricing] |
| Gemini Embedding 2 | 검색 | **0.20** | — | — | — | [pricing] |

- 기준으로 주어진 Pro 단가(1.25 / 10.00)는 **≤200k 구간 실제 공식가와 일치**. 본 설계 입력은 ~14k이므로 >200k 구간은 해당 없음.
- 원화 환산(1 USD = 1,500 KRW), 1k 토큰당:
  - Pro input 1.875원 / Pro output 15원 / **Pro 캐시 input 0.1875원**
  - Flash input 0.45원 / output 3.75원
  - Flash-Lite input 0.15원 / output 0.60원
  - Embedding input 0.30원

## 2. 컨텍스트 캐싱

| 항목 | 내용 | 함의 |
|---|---|---|
| 암시적(implicit) 캐싱 | 2.5 이상 모델 **기본 활성화**, 별도 저장비 없음 | 추가 비용 0으로 할인 획득 |
| 최소 토큰 | 2.5 Pro / Flash 모두 **2,048** | 정적 프리픽스(7,900)가 충분히 초과 |
| 히트 조건 | 요청들이 **짧은 시간 내 동일 프리픽스** 공유. 문서 권고: "크고 공통인 내용을 프롬프트 앞에 배치" | **정적에서 동적 순서로 두는 배치가 곧 원가 설계다** (지시문 순서를 정한 1차 근거) |
| 정적 프리픽스 실제 크기 | 7,900 (system 700 + user_content 6,000 + memory 1,200) | 2,048 최소 조건 충족 |
| 할인율 | 캐시 input 단가 0.125 vs 1.25 → **90%** | 초기 분석의 75% 가정을 상향 반영함 |
| 계측 | 응답의 `usage.total_cached_tokens` | 검증 로그로 사용 가능 |
| 명시적(explicit) 캐싱 저장비 | **$4.50 / 1M tokens / hour** | 정적 7,900토큰 상주 시 **약 53원/시간/세션** → 60원 과금 대비 치명적. **명시적 캐싱은 기각, 암시적 캐싱 채택**이 근거 있는 결정 |

## 3. Thinking (2.5 Pro)

| 항목 | 확인 내용 | 출처 |
|---|---|---|
| 비활성화 | **불가**. 2.5 Pro는 thinking 기본 On, 완전 해제 불가 (Flash-Lite는 기본 Off) | [thinking] |
| 과금 | **thinking 토큰은 output 단가로 과금**. "response pricing은 output + thinking 토큰의 합" | [pricing], [thinking] |
| max_output_tokens | thinking + output **합산 상한**. 과도히 낮추면 응답 잘림 → 비용 절감은 budget/level 조정으로 | [thinking] |
| budget 수치 범위 | **출처 불일치**: 128~32,768 (다수 SDK·2차 자료) vs 실사용 오류 메시지 512~24,576. 최신 문서는 low/medium/high 레벨 표기 | 미확정 |

### OpenRouter 경유 시의 thinking 제어 (2026-09-17 확인)

| 항목 | 확인 내용 | 함의 |
|---|---|---|
| 요청 파라미터 | `reasoning: {max_tokens, effort, enabled, exclude}`. `google/gemini-2.5-pro`의 `supported_parameters`에 `reasoning`·`include_reasoning` 포함 | budget 지정 경로가 존재한다 |
| 전달 방식 | OpenRouter가 `reasoning.max_tokens`를 `thinkingBudget`으로 전달 | 우리 설계의 "budget 고정"과 같은 수단 |
| **정확도 한계** | 문서는 Gemini에 대해 Google이 이를 **정확한 토큰 상한이 아니라 내부 thinking 레벨로 매핑**한다고 기술 | **128로 지정해도 정확히 128이 보장되지 않는다.** 가정 `thinking_budget=128`은 "지정 가능한 최소 수준"의 의미로 읽어야 하며, 민감도 표(128/512/8,192)를 병기하는 이유가 더 강해진다 |
| 과금 | reasoning 토큰은 출력 토큰으로 과금 | `internal_reasoning` 단가 $10/1M = completion 단가와 동일 (모델 메타데이터) |
| 응답 필드 | `completion_tokens_details.reasoning_tokens` | 실제 소비량을 사후 확인할 수 있다 |

출처: https://openrouter.ai/docs/use-cases/reasoning-tokens , https://openrouter.ai/docs/use-cases/usage-accounting (2026-09-17 확인)

### 원가 영향 (핵심)
- thinking 기본값이 알려진 대로 8,192라면: 8,192 × 15원/1k = **약 123원/턴** → 60원 과금 대비 **단독으로 적자**
- thinking 최소값(128 가정): 약 1.9원/턴
- → **thinking budget을 명시적으로 최소 고정하는 것이 경제성의 1차 전제**. 원가에 가장 큰 영향을 주는 변수.

### 미확정 항목 — **실측 불가 확인** (아래 4절 참조)
- [x] ~~2.5 Pro `thinkingBudget` 최소값 API 확인~~ → 신규 키로 2.5 계열 호출 불가
- [x] ~~최소 budget 설정 시 `thoughtsTokenCount` 실측~~ → 동일 사유
- [x] ~~암시적 캐시 히트 `total_cached_tokens` 실측~~ → 동일 사유
- → 세 항목 모두 **가정을 밝히고 민감도 분석**으로 처리한다.

## 4. [실측] Gemini 2.5 계열 신규 사용자 접근 차단

**확인일 2026-09-16 / 방법**: 신규 발급 API 키로 `models.list` 및 `count_tokens`·`generateContent` 호출
**재현**: `.venv/bin/python scripts/probe_thinking.py`

신규 무료 티어 키에서는 **서로 다른 두 제약이 동시에** 작동한다. 에러 코드로 구분된다.

| 모델 | 세대 | 등급 | `generateContent` 결과 |
|---|---|---|---|
| gemini-3.1-pro-preview | 3.x | Pro | **429 RESOURCE_EXHAUSTED** (할당량·결제) |
| gemini-3.1-flash-lite | 3.x | Lite | **OK** |
| gemini-2.5-pro | 2.5 | Pro | **404 NOT_FOUND** |
| gemini-2.5-flash | 2.5 | Flash | **404 NOT_FOUND** |
| gemini-2.5-flash-lite | 2.5 | Lite | **404 NOT_FOUND** |

- 세 모델 모두 `models.list`에는 노출되나 호출만 차단된다.
- 404 메시지 원문:
  > "This model models/gemini-2.5-pro is no longer available to new users. Please update your code to use models/gemini-3.1-pro-preview for the latest features and improvements."

### 두 제약의 구분 (변수 분리)
1. **무료 티어 할당량 제한** → `429`. Pro 계열에 적용. `gemini-3.1-pro-preview`에서 재현. 결제 등록(Tier 1) 시 해소되는 성격.
2. **2.5 세대 신규 사용자 차단** → `404`. 등급 무관하게 2.5 계열 전체에 적용.
   - 결정적 근거: **`3.1-flash-lite`는 성공하는데 `2.5-flash-lite`는 404**. 동일 키·동일 티어·동일 등급이고 차이는 모델 세대뿐. 티어 제한이라면 3.1-flash-lite도 차단돼야 하고, 할당량이라면 429여야 한다.

### 함의
1. 이미 2.5 Pro를 쓰고 있는 계정은 계속 쓸 수 있지만, **신규 키로는 같은 환경을 재현할 수 없다** → 팀을 늘리거나 환경을 다시 만들 때 제약이 된다.
2. 이 설계의 2.5 관련 수치는 전부 **공식 가격 문서 기준**이며, API 실측은 위 두 제약으로 불가능하다. thinking budget처럼 확정하지 못한 값은 **가정과 민감도 분석**으로 처리했다.

## 4. 확인 완료 체크리스트
- [x] Pro 컨텍스트 캐싱: 할인율(90%), 저장비($4.50/1M/hr), 암시적 캐싱 최소 토큰(2,048)
- [x] Pro thinking: 비활성화 불가, output 단가 과금
- [x] 200k 초과 구간 가격 → 본 설계 해당 없음
- [x] Flash / Flash-Lite 현행 가격
- [x] 임베딩 가격 (Gemini Embedding 2, $0.20)
- [x] ~~thinkingBudget 최소 수치~~ → API 차단으로 확인 불가, 민감도 분석(128/512/8192)으로 대체

## 출처
- [pricing] https://ai.google.dev/gemini-api/docs/pricing (2026-09-16 확인)
- [thinking] https://ai.google.dev/gemini-api/docs/thinking (2026-09-16 확인)
- [caching] https://ai.google.dev/gemini-api/docs/caching (2026-09-16 확인)
