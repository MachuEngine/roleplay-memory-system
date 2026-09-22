# 가정값 (Single Source of Truth)

모든 수치는 이 파일에서만 정의한다. 본문·스크립트는 여기를 참조.
가격 출처·확인일은 `docs/05-pricing.md`.

> **실측 범위**: Gemini 2.5 Pro는 OpenRouter 경유로 검증했다. 신규 Google API 키에서는 2.5 계열이 `404`여서 직접 API 캐시는 검증하지 못했다. 현재판 시스템 프롬프트는 API tokenizer 기준 **902토큰(사칭 ON) / 879(OFF)**이며 원가 계획값은 보수적으로 920토큰을 사용한다.
>
> **외부 가격 참조 범위**: 원가 기본값은 주어진 조건만으로 산출한다. 외부 출처는 ① 보조 모델·임베딩 단가 ② 캐시 단가·thinking 과금 방식(본 설계가 선택한 확장) 두 군데로 한정한다.

## A. 주어진 운영 조건 (변경 불가)
| 키 | 값 | 출처 |
|---|---|---|
| main_model | Gemini 2.5 Pro | 주어진 조건 |
| model_input_token_limit | 1,048,576 | [공식 모델 문서](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro) 2026-09-20 확인. 제품이 정한 6,000과 구분하기 위해 명시 |
| model_output_token_limit | 65,536 | 같은 출처. 기본 출력 한도 1,200과 구분 |
| price_in_usd_per_1m | 1.25 | 주어진 조건 (공식가와 일치, ≤200k 구간) |
| price_out_usd_per_1m | 10.0 | 주어진 조건 (공식가와 일치, ≤200k 구간) |
| fx_krw_per_usd | 1500 | 주어진 조건 |
| price_per_chat_krw | 60 | 주어진 조건 |
| tokens_user_content | 6000 | 주어진 조건 |
| tokens_chat_history | 6000 | 주어진 조건 — "평균 약 6,000토큰". **모델 한도가 아니라 제품이 관리하는 `chat_history` budget이다** |
| tokens_output_cap | 1200 | 주어진 조건 |

## B. 설계 가정 및 시나리오
| 키 | 확정값 | 상태 | 근거 |
|---|---|---|---|
| thinking_budget | **128** | 확정 | 2.5 Pro는 thinking 비활성화 불가, output 단가로 과금. 기본값 방치 시 thinking만 약 123원/턴으로 60원 과금 붕괴 → 최소 고정이 경제성 전제. 민감도 병기 |
| thinking_sensitivity | {128, 512, 8192} | 확정 | 최소값 출처 불일치(128 vs 512) + 기본값(8192) 대비 영향 제시. **2026-09-17 추가 확인**: OpenRouter 문서상 Gemini는 thinkingBudget을 정확한 상한이 아니라 내부 레벨로 매핑하므로 128 지정이 128을 보장하지 않는다 → 민감도 병기가 필수 |
| tokens_output_avg | **800** | 확정 | 한도 1,200 이하. 회전 주기 계산(유저 100 + AI 800)과 일치 |
| output_sensitivity | {500, 600, 800, 1200} | 확정 | 출력 길이가 원가 지배 변수. 600은 최종판 실호출 평균 543에 가까운 구간. 1,200 + thinking 128의 cache miss 모델 원가는 46.6원으로 수수료 후 적자 |
| output_length_rule | **8~10문단 + 행동 문단 3~5문장** | 실측 채택 | 초안의 "5~6문단 + 900~1,200자"는 산술적으로 동시 만족 불가(행동 문단 167자·대사 문단 37자 실측 → 6문단 = 612자). 문단 목표를 8~10으로 올리자 세 조건 동시 준수 3% → 42%, 문단 준수 16% → 80%(90건 A/B) |
| tokens_memory_budget | **1200** | 크기 가설 | 900은 3계층 동시 제공이 어렵고, 2,400은 실측 히트율 기준 L3 구독 계획 마진의 44%를 추가로 사용 |
| memory_split | 프로필 300 / 에피소드 600 / 검색 300 | 크기 가설 | 프로필 10~15개 상태, 에피소드 약 10개, 원문 2~3개를 가정. 400/500/300·300/450/450은 운영 표본으로 비교 |
| tokens_per_turn_total | **900** | 초기 모델링 값 | 유저 100 + AI 800. 유저 100은 실제 분포가 없어 50·100·200 민감도로 처리 |
| memory_batch_turns | **6** | 초기 가설 | 6,000 ÷ 900 = 6.67의 내림. `chat_history` budget 안에 들어가는 최대 `batch`여서 예산 초과 보유 비용 없이 불변 조건을 지킬 수 있다. **기술적 상한이 아니며** 턴당 토큰 가정이 바뀌면 함께 바뀐다 |
| turns_per_session | **30** | 초기 모델링 값 | 10·30·60턴 시나리오의 가운데 값. 실제 평균이라는 주장이 아니라 비용 비교 단위 |
| memory_runs_per_session | **5** | 파생 | 30 ÷ 6. 기준 시나리오(정확히 30턴, 6턴 단위 종료)에서는 `flush` 추가 비용이 0이다. `flush` 비용식은 **0.54 + 0.135n 원**이며, residual 0~5 균등 가정의 무조건부 기대값은 **0.79원/세션**. 세션 길이 분포가 없어 **원가 모델 미반영**이다 |
| extraction_latency_proxy | **p50 3.79초 / 표본 최댓값 4.75초** | proxy 실측 | `google/gemini-3.1-flash-lite`를 OpenRouter로 20회 호출(`scripts/measure_extraction_latency.py`, 입력 6,457토큰, 2026-09-20). 채택 모델 Gemini 2.5 Flash-Lite의 latency 검증이 아니다. 출력 평균 643토큰, 20회 중 11회가 800토큰 상한 부근 |
| extraction_quality_observed | **30/30** | 소규모 실측 | Gemini 2.5 Flash-Lite로 합성 한국어 10건을 seed 3개에서 실행. 최종 schema는 모두 통과했고 한국어 비율 1.0. 실제 장기 대화의 반복 병합 품질은 미검증 |
| turn_interval_baseline | **103~171초** | 계산 시나리오 | 900~1,200자를 650자/분으로 읽는 83~111초 + 회신 작성 20~60초. 실제 사용자 분포나 p50이 아니다 |
| turn_interval_stress | **20초** | stress 시나리오 | 응답을 훑고 짧게 답하는 빠른 사용자를 가정한 값. 통계적 하한이 아니다 |
| memory_idle_timeout | **10분** | baseline 시나리오 | 사용자 로그가 없어 p95에서 도출하지 않았다. 운영에서 5·10·20분의 불필요한 `flush`와 memory freshness를 비교해 결정 |
| overlap_max_tokens | **900 (1턴)** | 초기 가설 | proxy상 20초 시나리오에서도 다음 턴 전 완료 가능성이 높지만 모델·provider가 다르므로 보장이 아니다. 발동 시 1.69원/턴이며, 운영의 예산 초과율·초과 토큰 p95로 조정 |
| fallback_retrieval_rate | **{0, 1, 5, 10%}** | 민감도 | 측정된 발동률이 없어 단일 기댓값을 두지 않는다. 300토큰 주입은 발동 시 0.56원/턴, 1·5·10%면 평균 0.006·0.028·0.056원/턴 |
| noop_update_rate | **0%** | 보수적 가정 | `no-op` 갱신이 있으면 `cache invalidation`이 줄지만, 원가는 높게 잡는 쪽을 유지한다 |
| durable_log_write | **매 턴 즉시** | 확정 | 원문 저장과 `memory` 추출은 다른 작업이다. 모든 턴을 `extraction_status=pending`으로 즉시 저장한다 |
| memory_trigger | **`pending` 토큰 임계(5,400) 또는 최대 6턴 `batch`, 그리고 `idle`/종료 `flush`** | 확정 | 불변 조건: commit 전에는 원문 전체 또는 fallback retrieval 중 하나로 메인 모델에 제공한다. 구독자는 L1·L2 commit 후에도 `indexing_status!=indexed` 원문을 fallback 대상으로 유지한다 |
| commit_boundary | **L1·L2 + extraction_cursor + outbox event = 1 DB transaction** | 확정 | L3 `indexing`은 outbox를 비동기 소비하며 별도 `indexing_status`. 주 DB와 vector store를 단일 transaction으로 묶을 수 있다고 가정하지 않는다. L3 실패는 L1·L2를 막지 않는다 |
| overlap_unit_price | **uncached $1.25/1M** | 확정 | `overlap`·fallback retrieval은 `cacheable prefix` 뒤의 dynamic tail이라 cached 단가를 받지 못한다. 900토큰 = 1.69원/턴, 300토큰 = 0.56원/턴 |
| cache_strategy | **암시적 캐싱 기본 / 고빈도 세션만 명시적 전환** | 확정 | 25%는 이전 배치 8턴에서 관측한 잠정 계획값이다. 명시적 손익분기는 L3 구독 기준 시간당 5.3턴. explicit 경로에서는 L1·L2까지 cached resource에 넣고 L3·대화 내역은 새 contents로 보낸다 |
| explicit_cache_free_tier | **차단 (limit=0)** | 실측 | `caches.create` 시도, 크기·모델 무관하게 `TotalCachedContentStorageTokensPerModelFreeTier limit=0` (`scripts/test_explicit_cache.py`, 2026-09-18) |
| explicit_cache_via_openrouter | **읽기 히트 4/4, 입력 90% 절감** | 실측 | OpenRouter `cache_control`, 실제 대상 모델(`google/gemini-2.5-pro`). R1은 캐시 쓰기(프리미엄 $0.375/1M), R2~R5가 읽기 히트. 관측 비용이 공개 단가(읽기 0.125/쓰기 0.375/비캐시 1.25)로 오차 0.00% 재현됨. **시간당 저장비 항목 없음** — Google 네이티브($4.50/1M/hr)와 과금 구조가 다름 (`scripts/test_openrouter_cache_control.py`, 2026-09-18) |
| cache_price_in_usd_per_1m | **0.125** | 확정 | 공식가 (90% 할인) |
| cache_scenarios | 히트 / 미스 2안 병기 | 확정 | 암시적 캐시는 히트가 보장되지 않음 |
| cache_hit_rate_observed | **25%** | 현재판 실측 / 잠정 계획값 | 현재 L3 동적 경계·공급자 고정·연속 8턴에서 2턴 히트 (`scripts/test_cache_turns.py`, 2026-09-21). 표본이 작고 OpenRouter 경유이므로 운영 계측 전까지 잠정값 |
| aux_tokens_in_per_run | **기본 6,700 / L3 구독 7,000** | 파생 | 대상 6턴 5,400 + 기존 메모리 900/1,200 + 추출 지시 400. 선제 추출이라 대상 구간은 실행 시점에 아직 윈도 안에 있다 |
| memory_basic_budget | **900** | 확정 | 기본 사용자는 L1+L2만 사용하고 L3 리콜 300은 구독 기능으로 분리 |
| cacheable_system_tokens | **910** | 계획값 | 현재판 문자 기반 동적 memory 앞 prefix 추정 909를 반올림 |
| cacheable_prefix_paid | **7,810** | 파생 | 시스템 910 + 사용자 콘텐츠 6,000 + L1·L2 900. L3 300은 매 턴 변동 |
| cache_break_on_memory_update | 6턴 중 1턴 (기준 시나리오) | 파생 | 연속 30턴·full batch 5회·모두 material update일 때 5/30. **상한이 아니다.** 실제 = L1·L2가 실제로 바뀐 batch·flush 횟수 ÷ 전체 턴 수. no-op면 낮아지고 짧은 세션·잦은 flush면 높아진다 |
| aux_tokens_out_per_run | **500** | 초기 모델링 값 | 전체 1,200토큰의 변경분만 출력한다는 가정. proxy 모델은 평균 643토큰이고 20회 중 11회가 800토큰 상한 부근이었으나 채택 모델이 아니므로 300·500·643·800 민감도로 처리 |
| payment_fee_rate | 0.30 | 확정 | 앱스토어 수수료 가정. 원가표에 병기 |
| prompt_language | **영어 지시 + 한국어 예시** | 확정 | 아래 C절. 말미 한국어 앵커는 측정 후 제거 |
| safety_policy | 미공개 → 플레이스홀더 | 가정 | 회사 정책 미상 |
| tokens_system_prompt | **920** | 현재판 계획값 | 문자 추정 ON 919 / OFF 886, API 실측 ON 902 / OFF 879 (`scripts/count_tokens.py`, `scripts/measure_prompt_tokens.py`, 2026-09-21). 보수적으로 십 단위 올림 |
| aux_model_summary | **Gemini 2.5 Flash-Lite** | 확정 | in 0.10 / out 0.40. 정형 출력·비동기·원가. Pro 대비 턴당 3.44원 → 0.23원 |
| aux_model_embedding | **Gemini Embedding 2** | 소규모 검증 채택 | $0.20. 같은 한국어 합성 검증셋 18질의에서 Recall@3·MRR 1.0으로 BM25의 0.917을 넘음. 검증셋 내부 threshold 0.5453은 운영값으로 확정하지 않음 |
| sessions_per_month | **20** | 가격 시나리오 | 주 5회 수준을 가정한 기준안. 5·20·60세션 민감도 병기 |
| recall_inject_tokens | **300** | 확정 | L3 주입 슬롯 (= memory_split의 검색 몫) |
| query_embed_tokens | **100** | 초기 모델링 값 | 현재 발화 중심 검색을 가정. 50·100·200토큰 민감도로 처리 |
| retry_rate | **0.10** | 민감도 전용 | 실측 근거 없음. 계획 원가에서는 제외하고 0·10·30% 비교 |
| l2_item_cap | **10** | 크기 가설 | L2 예산 600 ÷ 항목당 약 60토큰. 실제 항목 길이로 조정 |
| history_boundary | 이번 턴 유저 발화 **제외** | 확정 | 실제 API 조립상 현재 발화는 `user` 역할 메시지로 별도 전달되므로 6,000에 포함시키면 이중 계상. 현재 발화 100토큰 별도 계상 |

## C. 프롬프트 언어 정책

**선택**: 지시문은 영어, 한국어는 `<style>` 예시에만 사용.

| 구간 | 언어 |
|---|---|
| 역할·인젝션 방어·사칭 모드 분기·출력 형식 지시 | 영어 |
| few-shot 출력 예시 (내용 중립, 최소 1개) | 한국어 |
| 우선순위 규칙 (설정 블록 > 예시) | 영어 |

**근거**: 지시와 데이터의 경계는 언어가 아니라 Rules·역할·XML 구조가 만든다. 영어 지시는 같은 의미의 한국어 지시보다 토큰이 적었고, 한국어 `<style>`은 말투·어미·호칭을 직접 시연한다. 직전판 90쌍 A/B에서 한국어 앵커를 제거해도 한국어 비율이 1.00이었고 길이·문단·출력 토큰 차이가 없었으므로 앵커를 제거했다.

**고려한 대안·기각 사유**
- *전체 한국어*: 위험 0이나 지시문 토큰 약 2배. "토큰 효율성" 명시 요건에 소극적
- *전체 영어*: 말투 제어 표현 손실, 출력 언어 누수 위험
- *문장 단위 혼합*: 코드스위칭 유발(한국어 대사에 영어 단어 혼입) → 롤플레이 몰입 파괴. 블록 단위로 분리해 회피

**리스크와 대응**
- few-shot 내용 오염(예시 캐릭터 톤이 실제 응답에 전이) → 예시는 내용 중립·형식 위주, "설정 블록 > 예시" 우선순위 명시
- few-shot 토큰 비용(150~300)이 영어 전환 절감분(~300)을 상쇄 → **토큰 효율성이 아니라 형식·말투 준수율 확보를 근거로 서술**. 정적 구간이라 캐시 프리픽스에 포함
- 한국어 인젝션에 영어 방어문이 일반화되는지 **검증 불가** → 가정으로 명시

## D. 토큰 배분

| 영역 | 토큰 | 성격 | 역할 |
|---|---|---|---|
| 시스템 프롬프트 | 920 | 정적 내용 / 캐시 계획값 910 | 역할·방어·형식·모드 분기 |
| 유저 콘텐츠 (`char`/`user_description`) | 6,000 | 준정적 | 캐릭터·유저 페르소나 (주어진 값) |
| 메모리 (`char_keywordbook`) | 1,200 | L1·L2는 6턴마다 / L3는 매 턴 | L1 300 / L2 600 / L3 300 |
| 대화 내역 (`chat_history`) | 6,000 | 동적 (매 턴) | 직전까지의 턴 (주어진 값) |
| 이번 턴 유저 발화 | 100 | 동적 | `user` 역할 메시지로 별도 전달 |
| **입력 합계** | **14,220** | | |
| 출력 | 800 (한도 1,200, 한국어 약 900~1,200자) | | |
| thinking | 128 | output 단가 과금 | |

배치 순서 = 지시 → 설정 → 스타일 → L1·L2 → L3 → 대화 내역. 의미적 우선순위는 별도로 선언한다. 암시적 캐싱이 프리픽스 일치 기반이므로 **동적 L3 뒤에는 캐시할 정적 블록을 두지 않는다.**

## E. 파생값 (`scripts/cost_model.py`가 산출)

기준 시나리오 (thinking 128 / 출력 800 / 암시적 캐시 히트율 25%):

| 사용자 유형 | 캐시 | 채팅 | 추출 | L3 인덱싱·질의 | 합계 | 수수료 30% 후 |
|---|---|---|---|---|---|---|
| 기본 | 미스 | 40.0원 | 0.22원 | — | **40.2원** | **1.8원** |
| 기본 | 25% | 36.8원 | 0.22원 | — | **37.0원** | **5.0원** |
| L3 구독 | 미스 | 40.6원 | 0.23원 | 0.30원 | **41.1원** | **0.9원** |
| L3 구독 | 25% | 37.4원 | 0.23원 | 0.30원 | **37.9원** | **4.1원** |

- 히트 구간 원가는 6턴 중 1턴의 L1·L2 갱신으로 prefix가 짧아지는 것을 반영한다.
- L3 주입 300토큰은 채팅 입력에 이미 포함한다. 0.30원은 인덱싱 8.1원과 질의 임베딩 0.9원을 30턴에 배분한 값이다.
- thinking 민감도: 128 → 40.6 / 512 → 46.3 / **8,192 → 161.5** (L3 포함 채팅 cache miss 기준)
- 출력 민감도: 600 → 37.6 / 800 → 40.6 / 1,200 → 46.6 (L3 포함 채팅 cache miss 기준)

계산식
- tokens_input_total = system + user_content + memory + history + current_user
- margin = price_per_chat × (1 − fee) − chat_cost − extraction_cost − l3_background_cost
- **확정 수치는 `scripts/cost_model.py` 출력을 따른다.**
