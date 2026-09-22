"""원가 시나리오 계산. 모든 입력값은 docs/03-assumptions.md 와 동일해야 한다.

usage: .venv/bin/python scripts/cost_model.py  → 마크다운 표 출력
"""
from dataclasses import dataclass, replace

# --- 주어진 운영 조건 (A절) ---
FX = 1500
P_IN, P_OUT = 1.25, 10.0
PRICE = 60
TOKENS_USER_CONTENT = 6000
TOKENS_HISTORY = 6000        # 직전까지의 대화 (현재 턴 유저 발화 제외)
CURRENT_USER = 100           # 이번 턴 유저 발화 — user 역할 메시지로 별도 전달

# --- 확정 가정 (B절) ---
P_IN_CACHED = 0.125          # 공식가, 90% 할인
P_CACHE_STORAGE = 4.50       # 명시적 캐시 저장비 /1M tokens/hour
SYSTEM_CACHEABLE = 910       # 현재판 문자 기반 추정. 동적 memory payload 앞 고정 prefix
MEMORY_CORE = 900            # L1 300 + L2 600, 6턴마다 갱신
RECALL_TOKENS_IN_MEMORY = 300  # L3는 매 턴 바뀌어 안정 프리픽스에서 제외
TURNS_PER_HOUR = 20          # 채택값. 3분에 한 턴 — 민감도 병기
THINKING = 128               # 최소 고정
OUTPUT_AVG = 800
MEMORY = 1200
SYSTEM = 920                 # 사칭 ON 문자 추정 919를 보수적으로 반올림 (API 실측 902)
TURNS_PER_SESSION = 30
HISTORY_ROTATION_TURNS = 6   # 6000 / 900, 보수적 내림
MEMORY_RUNS_PER_SESSION = TURNS_PER_SESSION // HISTORY_ROTATION_TURNS
AUX_OUT = 500
FEE = 0.30

# 보조 모델 후보 (docs/05-pricing.md)
AUX_MODELS = {
    "Flash-Lite": (0.10, 0.40),
    "Flash": (0.30, 2.50),
}


def krw(tokens: int, usd_per_1m: float) -> float:
    return tokens / 1e6 * usd_per_1m * FX


@dataclass
class Budget:
    system: int = SYSTEM
    user_content: int = TOKENS_USER_CONTENT
    memory_core: int = MEMORY_CORE
    recall: int = RECALL_TOKENS_IN_MEMORY
    history: int = TOKENS_HISTORY

    @property
    def static(self) -> int:
        """내용이 자주 바뀌지 않는 양. 물리적 cacheable prefix와는 다르다."""
        return self.system + self.user_content + self.memory_core + self.recall

    @property
    def dynamic(self) -> int:
        return self.history + CURRENT_USER

    @property
    def stable(self) -> int:
        """L3 앞에 연속 배치되어 실제 캐시에 올릴 수 있는 구간."""
        return SYSTEM_CACHEABLE + self.user_content + self.memory_core

    @property
    def stable_without_memory(self) -> int:
        """L1·L2 갱신 턴에도 유지되는 프리픽스."""
        return SYSTEM_CACHEABLE + self.user_content

    @property
    def total(self) -> int:
        return self.static + self.dynamic


def turn_cost(b: Budget, out: int, thinking: int, cached: bool) -> float:
    if cached:
        cost_in = krw(b.stable, P_IN_CACHED) + krw(b.total - b.stable, P_IN)
    else:
        cost_in = krw(b.total, P_IN)
    return cost_in + krw(out + thinking, P_OUT)


def turn_cost_explicit(b: Budget, out: int, thinking: int,
                       turns_per_hour: int = TURNS_PER_HOUR) -> float:
    """명시적 캐싱: 안정 프리픽스를 캐시에 올리고 매 턴 참조한다.

    저장비는 시간당 과금이라 그 시간 안에 오간 턴 수로 나눠 부담한다.
    히트가 보장되므로 암시적처럼 확률로 섞지 않는다.
    """
    storage = krw(b.stable, P_CACHE_STORAGE) / turns_per_hour
    cost_in = (krw(b.stable, P_IN_CACHED)
               + krw(b.total - b.stable, P_IN))
    return cost_in + krw(out + thinking, P_OUT) + storage


def turn_cost_blended(b: Budget, out: int, thinking: int) -> float:
    """메모리 갱신 턴은 프리픽스가 깨져 memory 이후가 캐시되지 않는다.

    6턴 중 5턴: cacheable system + user_content + L1·L2 캐시
    6턴 중 1턴: cacheable system + user_content만 캐시 (L1·L2 정가)
    L3와 transcript 뒤 반복 지시는 항상 정가다.
    """
    n = HISTORY_ROTATION_TURNS
    full = krw(b.stable, P_IN_CACHED) + krw(b.total - b.stable, P_IN)
    broken = (
        krw(b.stable_without_memory, P_IN_CACHED)
        + krw(b.total - b.stable_without_memory, P_IN)
    )
    cost_in = (full * (n - 1) + broken) / n
    return cost_in + krw(out + thinking, P_OUT)


def aux_input(memory_tokens: int) -> int:
    return HISTORY_ROTATION_TURNS * (CURRENT_USER + OUTPUT_AVG) + memory_tokens + 400


def aux_cost_per_run(p_in: float, p_out: float, memory_tokens: int = MEMORY) -> float:
    return krw(aux_input(memory_tokens), p_in) + krw(AUX_OUT, p_out)


# --- L3 리콜(유료 기능) 추가 원가 ---
P_EMBED = 0.20
RECALL_TOKENS = 300          # 주입 슬롯
QUERY_TOKENS = 100           # 매 턴 질의 임베딩
EVICTED_PER_RUN = 5400       # 인덱싱 대상
SESSIONS_PER_MONTH = 20      # 유료 유저 사용량 가정


def l3_cost_per_session(cached: bool) -> float:
    indexing = krw(EVICTED_PER_RUN, P_EMBED) * MEMORY_RUNS_PER_SESSION
    query = krw(QUERY_TOKENS, P_EMBED) * TURNS_PER_SESSION
    injection = krw(RECALL_TOKENS, P_IN_CACHED if cached else P_IN) * TURNS_PER_SESSION
    return indexing + query + injection


def l3_background_per_turn() -> float:
    """주입 토큰은 메인 Budget.recall에 이미 포함되므로 인덱싱·질의만 더한다."""
    indexing = krw(EVICTED_PER_RUN, P_EMBED) * MEMORY_RUNS_PER_SESSION
    query = krw(QUERY_TOKENS, P_EMBED) * TURNS_PER_SESSION
    return (indexing + query) / TURNS_PER_SESSION


def main() -> None:
    paid = Budget()
    free = Budget(recall=0)
    print(f"기준 입력 총합 {paid.total:,} 토큰 (L1·L2·L3 포함)")
    print(f"메모리 트리거: {HISTORY_ROTATION_TURNS}턴 주기, 세션당 {MEMORY_RUNS_PER_SESSION}회\n")

    print("## 1. 대표 계획값과 출력 상한\n")
    print("입력 $1.25/1M, 출력 $10/1M, 1 USD = 1,500 KRW. 출력 800은 설계 가정이고 1,200은 기본 한도다.\n")
    base = turn_cost(paid, OUTPUT_AVG, 0, False)
    base_in, base_out = krw(paid.total, P_IN), krw(OUTPUT_AVG, P_OUT)
    print("| 입력(원) | 출력(원) | 턴 원가(원) | 마진(60) | 수수료 30% 후 |")
    print("|---|---|---|---|---|")
    print(
        f"| {base_in:.1f} | {base_out:.1f} | **{base:.1f}** | "
        f"{PRICE - base:.1f} | {PRICE * (1 - FEE) - base:.1f} |"
    )
    cap = turn_cost(paid, 1200, THINKING, False)
    print(f"\n- 출력 1,200 + thinking {THINKING}의 cache miss 모델 원가는 "
          f"**{cap:.1f}원**, 수수료 후 손익은 **{PRICE * (1 - FEE) - cap:.1f}원**이다. "
          f"과금 출력은 {krw(1200 + THINKING, P_OUT):.1f}원이며, 보조 시스템 비용은 포함하지 않은 값이다.")

    print("\n## 2. 확장 — 운영 변수 반영 (외부 가격 출처: docs/05-pricing.md)\n")
    print("| 시나리오 | 턴 원가(원) | 마진(60) | 수수료 후 | 기본 대비 |")
    print("|---|---|---|---|---|")
    rows = [
        (f"기본 + thinking {THINKING}", turn_cost(paid, OUTPUT_AVG, THINKING, False)),
        ("위 + 암시적 캐시 히트(혼합)", turn_cost_blended(paid, OUTPUT_AVG, THINKING)),
    ]
    for label, cost in rows:
        print(
            f"| {label} | **{cost:.1f}** | {PRICE - cost:.1f} | "
            f"{PRICE * (1 - FEE) - cost:.1f} | {cost - base:+.1f} |"
        )
    print("\n- thinking은 2.5 Pro에서 비활성화 불가하며 output 단가로 과금된다(공식 문서).")
    print("- 캐시 히트(혼합) = 메모리 갱신 턴의 프리픽스 무효화를 반영한 6턴 평균.")

    print("\n## 민감도 — thinking budget (출력 800 고정)\n")
    print("| thinking | 캐시 미스 | 캐시 히트 |")
    print("|---|---|---|")
    for th in (128, 512, 8192):
        miss = turn_cost(paid, OUTPUT_AVG, th, False)
        hit = turn_cost(paid, OUTPUT_AVG, th, True)
        print(f"| {th:,} | {miss:.1f} | {hit:.1f} |")

    print("\n## 민감도 — 출력 길이 (thinking 128 고정)\n")
    print("| 출력 | 캐시 미스 | 캐시 히트 |")
    print("|---|---|---|")
    for out in (500, 600, 800, 1200):
        miss = turn_cost(paid, out, THINKING, False)
        hit = turn_cost(paid, out, THINKING, True)
        print(f"| {out:,} | {miss:.1f} | {hit:.1f} |")

    # 캐시 미스에서 손익분기가 되는 유저 콘텐츠 크기
    _aux = (aux_cost_per_run(*AUX_MODELS["Flash-Lite"]) * MEMORY_RUNS_PER_SESSION
            / TURNS_PER_SESSION + l3_background_per_turn())
    net = PRICE * (1 - FEE)
    lo, hi = 1, 40000
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if turn_cost(replace(paid, user_content=mid), OUTPUT_AVG, THINKING, False) + _aux < net:
            lo = mid
        else:
            hi = mid
    print(f"\n## 손익분기 유저 콘텐츠 (캐시 미스, 수수료 {FEE:.0%} 후 실수령 {net:.0f}원)\n")
    print(f"- **{lo:,}토큰** — 기준값 {TOKENS_USER_CONTENT:,} 대비 +{(lo/TOKENS_USER_CONTENT-1)*100:.0f}%")
    print(f"- 이보다 긴 디스크립션은 캐시 미스 시 구조적 적자")

    print("\n## 민감도 — 유저 콘텐츠 크기 (원가 분산)\n")
    print("| 유저 콘텐츠 | 입력 총합 | 캐시 미스 | 캐시 히트 |")
    print("|---|---|---|---|")
    for uc in (2000, 6000, 12000, 20000):
        bv = Budget(user_content=uc)
        print(
            f"| {uc:,} | {bv.total:,} | "
            f"{turn_cost(bv, OUTPUT_AVG, THINKING, False):.1f} | "
            f"{turn_cost(bv, OUTPUT_AVG, THINKING, True):.1f} |"
        )

    print("\n## 출력 길이가 메모리 트리거에 미치는 연쇄 영향\n")
    print("| 평균 출력 | 턴당 토큰 | 내역 회전 | 세션 트리거 | 보조 턴당 |")
    print("|---|---|---|---|---|")
    run_cost = aux_cost_per_run(*AUX_MODELS["Flash-Lite"])
    for out in (400, 600, 800, 1200):
        per_turn = 100 + out
        rotation = int(TOKENS_HISTORY // per_turn)
        runs = TURNS_PER_SESSION // rotation
        print(f"| {out:,} | {per_turn:,} | {rotation}턴 | {runs}회 | "
              f"{run_cost * runs / TURNS_PER_SESSION:.2f}원 |")
    print("\n- 출력 길이는 원가뿐 아니라 **대화 내역 회전 주기**를 바꾸고,")
    print("  그것이 메모리 트리거 주기와 보조 시스템 비용까지 연쇄로 바꾼다.")

    print("\n## 보조 시스템 비용\n")
    print("| 모델 | 1회(원) | 세션(원) | 턴당(원) |")
    print("|---|---|---|---|")
    for name, (p_in, p_out) in AUX_MODELS.items():
        run = aux_cost_per_run(p_in, p_out)
        session = run * MEMORY_RUNS_PER_SESSION
        print(f"| {name} | {run:.2f} | {session:.1f} | {session / TURNS_PER_SESSION:.2f} |")

    print("\n## 통합 경제성 — 기본과 L3 구독 경계 분리\n")
    aux_free = (aux_cost_per_run(*AUX_MODELS["Flash-Lite"], MEMORY_CORE)
                * MEMORY_RUNS_PER_SESSION / TURNS_PER_SESSION)
    aux_paid = (aux_cost_per_run(*AUX_MODELS["Flash-Lite"], MEMORY)
                * MEMORY_RUNS_PER_SESSION / TURNS_PER_SESSION)
    l3_bg = l3_background_per_turn()
    print("| 구분 | 캐시 | 채팅 | 추출 | L3 인덱싱·질의 | 합계 | 수수료 후 |")
    print("|---|---|---|---|---|---|---|")
    for product, budget, aux, extra in [
        ("기본", free, aux_free, 0.0),
        ("L3 구독", paid, aux_paid, l3_bg),
    ]:
        miss = turn_cost(budget, OUTPUT_AVG, THINKING, False)
        hit = turn_cost_blended(budget, OUTPUT_AVG, THINKING)
        for cache, chat in [("miss", miss), ("25%", 0.25 * hit + 0.75 * miss)]:
            total = chat + aux + extra
            print(f"| {product} | {cache} | {chat:.1f} | {aux:.2f} | {extra:.2f} | "
                  f"**{total:.1f}** | {PRICE * (1 - FEE) - total:.1f} |")

    print("\n## 본문에 인용된 파생 수치 (재현용)\n")
    pro_run = aux_cost_per_run(P_IN, P_OUT)
    flash_run = aux_cost_per_run(*AUX_MODELS["Flash"])
    lite_run = aux_cost_per_run(*AUX_MODELS["Flash-Lite"])
    per = MEMORY_RUNS_PER_SESSION / TURNS_PER_SESSION
    print("| 항목 | 값 | 산식 |")
    print("|---|---|---|")
    print(f"| 보조를 Pro로 했을 때 1회 | {pro_run:.1f}원 | in {aux_input(MEMORY):,}×$1.25 + out {AUX_OUT}×$10 |")
    print(f"| 같은 조건 턴당 | {pro_run * per:.2f}원 | 1회 × {MEMORY_RUNS_PER_SESSION}회 ÷ {TURNS_PER_SESSION}턴 |")
    print(f"| Pro→Flash-Lite 절감 | {(pro_run - lite_run) * per:.2f}원 | 턴당 차이 |")
    print(f"| L1만 Flash로 상향 시 증가 | {(flash_run - lite_run) * per / 2:.2f}원 | 추출 절반이 L1이라 가정 |")
    print(f"| 시스템 프롬프트 절반 감축 | {krw(SYSTEM // 2, P_IN):.2f}원 | {SYSTEM // 2}토큰 × 미스 단가 |")
    print(f"| 리콜 300토큰 주입(30턴) | {krw(RECALL_TOKENS, P_IN_CACHED) * TURNS_PER_SESSION:.1f}원(히트) / "
          f"{krw(RECALL_TOKENS, P_IN) * TURNS_PER_SESSION:.1f}원(미스) | |")
    print(f"| 밀려난 구간 인덱싱(5회) | {krw(EVICTED_PER_RUN, P_EMBED) * MEMORY_RUNS_PER_SESSION:.1f}원 | {EVICTED_PER_RUN:,}토큰 × {MEMORY_RUNS_PER_SESSION}회 × ${P_EMBED}/1M |")
    print(f"| 질의 임베딩(30턴) | {krw(QUERY_TOKENS, P_EMBED) * TURNS_PER_SESSION:.1f}원 | |")
    print(f"| L3 세션 부가 합계 | {krw(EVICTED_PER_RUN, P_EMBED) * MEMORY_RUNS_PER_SESSION + krw(QUERY_TOKENS, P_EMBED) * TURNS_PER_SESSION:.1f}원 | 인덱싱 + 질의 임베딩 |")

    print("\n## 배치 크기와 추출 비용\n")
    print("| 방식 | 세션 비용 | 참조 공백 |")
    print("|---|---|---|")
    batch = aux_cost_per_run(*AUX_MODELS["Flash-Lite"]) * MEMORY_RUNS_PER_SESSION
    fixed = 400 + MEMORY          # 추출 지시 + 기존 메모리
    per_in = (100 + OUTPUT_AVG) + fixed
    p_in, p_out = AUX_MODELS["Flash-Lite"]
    every = (krw(per_in, p_in) + krw(AUX_OUT, p_out)) * TURNS_PER_SESSION
    print(f"| 6턴 배치 (채택) | {batch:.1f}원 | 최대 {HISTORY_ROTATION_TURNS}턴 |")
    print(f"| 매 턴 | {every:.1f}원 ({every / batch:.1f}배) | 없음 |")
    print("\n- 매 턴 방식은 고정 비용(추출 지시 400 + 기존 메모리)을 매번 부담한다.")

    print("\n## 캐싱 전략 비교 (L3 구독 기준)\n")
    print(f"안정 프리픽스 {paid.stable:,}토큰 (L3 {RECALL_TOKENS_IN_MEMORY}과 후행 고정문은 제외)\n")
    print("| 전략 | 턴 원가 | 마진(60) | 수수료 후 | 히트 보장 |")
    print("|---|---|---|---|---|")
    paid_overhead = aux_paid + l3_bg
    miss_t = turn_cost(paid, OUTPUT_AVG, THINKING, False) + paid_overhead
    # 암시적 히트는 메모리 갱신 턴(6턴 중 1턴)의 프리픽스 깨짐을 반영한 혼합값을 쓴다
    imp25 = 0.25 * (turn_cost_blended(paid, OUTPUT_AVG, THINKING) + paid_overhead) + 0.75 * miss_t
    print(f"| 암시적 — 실측 히트율 25% **(기본값·계획 기준)** | {imp25:.1f}원 | {PRICE-imp25:.1f}원 | "
          f"{PRICE*(1-FEE)-imp25:.1f}원 | 아니오 |")
    for tph in (10, TURNS_PER_HOUR, 30):
        ex = turn_cost_explicit(paid, OUTPUT_AVG, THINKING, tph) + paid_overhead
        mark = " (승격 시 기준)" if tph == TURNS_PER_HOUR else ""
        print(f"| 명시적 — 시간당 {tph}턴{mark} | {ex:.1f}원 | {PRICE-ex:.1f}원 | "
              f"{PRICE*(1-FEE)-ex:.1f}원 | 예 |")
    print(f"\n- 명시적 저장비: {krw(paid.stable, P_CACHE_STORAGE):.1f}원/시간을 시간당 턴 수로 분담")
    print(f"- 손익분기 회전율: 시간당 약 {krw(paid.stable, P_CACHE_STORAGE) / (imp25 - (turn_cost_explicit(paid, OUTPUT_AVG, THINKING, 10**6) + paid_overhead)):.1f}턴 이상이면 암시적보다 유리")
    print("- 전략: 암시적을 기본으로 두고, 손익분기를 넘는 세션만 명시적으로 승격 (2.3절)")

    print("\n## 캐시 히트율별 기대 원가\n")
    miss = turn_cost(paid, OUTPUT_AVG, THINKING, False)
    hit = turn_cost_blended(paid, OUTPUT_AVG, THINKING)
    print("| 히트율 | 턴 원가 | 마진(60) | 수수료 후 |")
    print("|---|---|---|---|")
    for h in (0, 0.25, 0.3, 0.5, 0.7, 0.9, 1.0):
        cst = h * hit + (1 - h) * miss + paid_overhead
        print(f"| {h * 100:.0f}% | {cst:.1f}원 | {PRICE - cst:.1f}원 | "
              f"{PRICE * (1 - FEE) - cst:.1f}원 |")
    print("\n- 위 표는 추출 0.23원과 L3 인덱싱·질의 0.30원을 포함한다.")
    print("- 히트율은 응답의 usage.cached_content_token_count로 계측한다.")

    print("\n## L3 리콜 (유료 기능) 추가 원가 및 가격 근거\n")
    lo, hi = l3_cost_per_session(True), l3_cost_per_session(False)
    print(f"세션당 추가 원가: {lo:.1f}원(캐시 히트) ~ {hi:.1f}원(미스)")
    print(f"월 {SESSIONS_PER_MONTH}세션 기준: {lo * SESSIONS_PER_MONTH:.0f} ~ "
          f"{hi * SESSIONS_PER_MONTH:.0f}원\n")
    print("| 가격(월) | 수수료 후 | 원가율(보수적) |")
    print("|---|---|---|")
    for price in (2900, 4900):
        net = price * (1 - FEE)
        print(f"| {price:,}원 | {net:,.0f}원 | {hi * SESSIONS_PER_MONTH / net * 100:.0f}% |")
    print("\n### 사용량별 원가율 (4,900원 기준)\n")
    print("| 월 사용량 | L3 추가 원가 | 원가율 |")
    print("|---|---|---|")
    net49 = 4900 * (1 - FEE)
    for m in (10, 20, 40, 60):
        print(f"| {m}세션 | {lo * m:.0f}~{hi * m:,.0f}원 | {hi * m / net49 * 100:.0f}% |")

    print("\n### 결제 채널별 턴 마진\n")
    miss_t = turn_cost(paid, OUTPUT_AVG, THINKING, False) + paid_overhead
    hit_t = turn_cost_blended(paid, OUTPUT_AVG, THINKING) + paid_overhead
    print("| 채널 | 실수령 | 미스 마진 | 히트 마진 |")
    print("|---|---|---|---|")
    for name, fee in (("앱스토어 30%", 0.30), ("웹 결제 5%", 0.05)):
        net = PRICE * (1 - fee)
        print(f"| {name} | {net:.1f}원 | {net - miss_t:.1f}원 | {net - hit_t:.1f}원 |")

    # 본문 2.3절 수익성 개선안: 계획 기준(암시적 hit 25%, L3 구독)과 같은 경계로 비교
    out600_rotation = TOKENS_HISTORY // (CURRENT_USER + 600)
    out600_regular_runs, out600_residual = divmod(TURNS_PER_SESSION, out600_rotation)
    out600_total_aux = 0.0
    for batch_turns in ([out600_rotation] * out600_regular_runs
                        + ([out600_residual] if out600_residual else [])):
        batch_input = batch_turns * (CURRENT_USER + 600) + MEMORY + 400
        out600_total_aux += (krw(batch_input, AUX_MODELS["Flash-Lite"][0])
                             + krw(AUX_OUT, AUX_MODELS["Flash-Lite"][1]))
    out600_overhead = out600_total_aux / TURNS_PER_SESSION + l3_bg
    out600_miss = turn_cost(paid, 600, THINKING, False)
    out600_hit = turn_cost_blended(paid, 600, THINKING)
    out600_cost25 = 0.75 * out600_miss + 0.25 * out600_hit + out600_overhead
    print("\n### 수익성 개선안의 수수료 후 잔여액\n")
    print(f"- 웹 결제 5%, 출력 800, 암시적 hit 25%: "
          f"{PRICE * 0.95 - imp25:.1f}원")
    print(f"- 앱스토어 30%, 출력 600, 암시적 hit 25%: "
          f"{PRICE * (1 - FEE) - out600_cost25:.1f}원")

    chat_month = PRICE * TURNS_PER_SESSION * SESSIONS_PER_MONTH
    print(f"\n비교: 월 {SESSIONS_PER_MONTH}세션 × {TURNS_PER_SESSION}턴 채팅 과금 "
          f"{chat_month:,.0f}원 대비 4,900원 = {4900 / chat_month * 100:.1f}%")


if __name__ == "__main__":
    main()
