"""원가 집계 독립 검산.

scripts/cost_model.py 를 import 하지 않고 같은 수치를 처음부터 다시 계산한다.
목적은 두 가지다.
  (1) 채팅 입력의 여섯 구성요소가 빠짐없이 집계되는지 — 누락되면 합이 맞지 않는다
  (2) memory system의 보조 비용이 문서가 서술한 연결 순서대로 계산되는지

usage: .venv/bin/python scripts/verify_cost.py
"""
from __future__ import annotations

import sys

# docs/03-assumptions.md 에서 옮겨 적은 값. 여기서만 정의한다.
FX = 1500
P_IN, P_OUT, P_IN_CACHED = 1.25, 10.0, 0.125          # USD / 1M
SYSTEM, SYSTEM_CACHEABLE, USER_CONTENT = 920, 910, 6000
MEMORY_CORE, MEMORY_RECALL, HISTORY, CURRENT_USER = 900, 300, 6000, 100
MEMORY = MEMORY_CORE + MEMORY_RECALL
OUTPUT_AVG, THINKING, OUTPUT_CAP = 800, 128, 1200
TURNS_PER_SESSION, ROTATION = 30, 6
AUX_OUT = 500
AUX_IN_PRICE, AUX_OUT_PRICE = 0.10, 0.40              # Flash-Lite
P_EMBED, EVICTED_PER_RUN, QUERY_TOKENS = 0.20, 5400, 100
PRICE_PER_CHAT, FEE = 60, 0.30

FAILURES: list[str] = []


def krw(tokens: float, usd_per_1m: float) -> float:
    return tokens * usd_per_1m / 1_000_000 * FX


def expect(label: str, got: float, want: float, tol: float = 0.05) -> None:
    ok = abs(got - want) <= tol
    print(f"{'PASS' if ok else 'FAIL'}  {label:<44} 계산 {got:>8.2f} / 문서 {want:>8.2f}")
    if not ok:
        FAILURES.append(label)


print("## 1. 채팅 입력 — 여섯 구성요소 집계 검산\n")
components = {
    "시스템 프롬프트": SYSTEM,
    "유저 콘텐츠": USER_CONTENT,
    "메모리": MEMORY,
    "대화 내역": HISTORY,
    "현재 유저 메시지": CURRENT_USER,
}
for name, v in components.items():
    print(f"   {name:<16} {v:>7,}")
total_in = sum(components.values())
print(f"   {'입력 합계':<16} {total_in:>7,}")
print(f"   {'출력(평균)':<16} {OUTPUT_AVG:>7,}  (한도 {OUTPUT_CAP:,})")
print(f"   {'thinking':<16} {THINKING:>7,}  (output 단가로 과금)\n")
expect("입력 총합", total_in, 14220, tol=0)
stable = SYSTEM_CACHEABLE + USER_CONTENT + MEMORY_CORE
expect("계획상 캐시 가능 프리픽스", stable, 7810, tol=0)
expect("동적 구간(내역+현재 발화)", HISTORY + CURRENT_USER, 6100, tol=0)

cost_in = krw(total_in, P_IN)
cost_out_base = krw(OUTPUT_AVG, P_OUT)
cost_out_think = krw(OUTPUT_AVG + THINKING, P_OUT)
print()
expect("입력 비용(원)", cost_in, 26.66, tol=0.01)
expect("출력 비용(원, thinking 제외)", cost_out_base, 12.0)
expect("턴 원가 — 계획 평균(출력 800)", cost_in + cost_out_base, 38.7)
expect("턴 원가 — thinking 128 포함", cost_in + cost_out_think, 40.6)

# 캐시 히트: L3와 transcript 뒤의 고정문은 항상 정가다. L1·L2 갱신 턴은 prefix가 짧아진다.
static_hit = krw(stable, P_IN_CACHED) + krw(total_in - stable, P_IN)
stable_break = SYSTEM_CACHEABLE + USER_CONTENT
static_break = krw(stable_break, P_IN_CACHED) + krw(total_in - stable_break, P_IN)
mixed_in = ((ROTATION - 1) * static_hit + static_break) / ROTATION
expect("턴 원가 — 캐시 히트(혼합)", mixed_in + cost_out_think, 27.7, tol=0.1)

print("\n## 2. memory system — 보조 비용 연결 순서 검산\n")
runs = TURNS_PER_SESSION // ROTATION
aux_in_paid = ROTATION * (CURRENT_USER + OUTPUT_AVG) + MEMORY + 400
aux_in_free = ROTATION * (CURRENT_USER + OUTPUT_AVG) + MEMORY_CORE + 400
aux_once_paid = krw(aux_in_paid, AUX_IN_PRICE) + krw(AUX_OUT, AUX_OUT_PRICE)
aux_once_free = krw(aux_in_free, AUX_IN_PRICE) + krw(AUX_OUT, AUX_OUT_PRICE)
aux_session = aux_once_paid * runs
aux_paid = aux_session / TURNS_PER_SESSION
aux_free = aux_once_free * runs / TURNS_PER_SESSION
l3_bg = (krw(EVICTED_PER_RUN, P_EMBED) * runs
         + krw(QUERY_TOKENS, P_EMBED) * TURNS_PER_SESSION) / TURNS_PER_SESSION
print(f"   유료 추출 1회 비용        {aux_once_paid:>6.2f}원  (in {aux_in_paid:,} / out {AUX_OUT:,}, Flash-Lite)")
print(f"→  세션당 보조 작업 횟수      {runs:>6}회   ({TURNS_PER_SESSION}턴 ÷ {ROTATION}턴 주기)")
print(f"→  세션당 보조 시스템 비용    {aux_session:>6.2f}원")
print(f"→  유료 턴당 추출 / L3 보조  {aux_paid:>6.2f}원 / {l3_bg:.2f}원")

free_total = SYSTEM + USER_CONTENT + MEMORY_CORE + HISTORY + CURRENT_USER
free_miss_chat = krw(free_total, P_IN) + cost_out_think
free_hit = (krw(stable, P_IN_CACHED) + krw(free_total - stable, P_IN))
free_break = krw(stable_break, P_IN_CACHED) + krw(free_total - stable_break, P_IN)
free_hit_chat = ((ROTATION - 1) * free_hit + free_break) / ROTATION + cost_out_think
paid_miss = cost_in + cost_out_think + aux_paid + l3_bg
paid_25 = 0.75 * (cost_in + cost_out_think) + 0.25 * (mixed_in + cost_out_think) + aux_paid + l3_bg
free_25 = 0.75 * free_miss_chat + 0.25 * free_hit_chat + aux_free
print(f"→  기본 25% / L3 구독 25%    {free_25:>6.2f}원 / {paid_25:.2f}원")
print("   ※ 위 잔여액은 순이익이 아니다. 서버·벡터 저장소·모니터링·CS·환불·"
      "무과금 유저 비용은 포함하지 않았다.\n")
expect("유료 추출 1회 비용", aux_once_paid, 1.35)
expect("세션당 보조 비용", aux_session, 6.8, tol=0.1)
expect("기본 턴당 추출 비용", aux_free, 0.22, tol=0.01)
expect("L3 구독 턴당 추출 비용", aux_paid, 0.23, tol=0.01)
expect("L3 인덱싱·질의", l3_bg, 0.30, tol=0.01)
expect("기본 합계 — 캐시 25%", free_25, 37.0, tol=0.1)
expect("L3 구독 합계 — 캐시 미스", paid_miss, 41.1, tol=0.1)
expect("L3 구독 합계 — 캐시 25%", paid_25, 37.9, tol=0.1)

print("\n## 3. 채팅과 memory system의 공유 상수 일치\n")
expect("메모리 예산 (채팅 배분 = L1+L2+L3)", MEMORY, 300 + 600 + 300, tol=0)
expect("트리거 주기 (내역 6,000 ÷ 턴당 900)", ROTATION, HISTORY // (CURRENT_USER + OUTPUT_AVG), tol=0)
expect("보조 입력 (밀려난 6턴 + 기존 메모리 + 지시)",
       aux_in_paid, ROTATION * (CURRENT_USER + OUTPUT_AVG) + MEMORY + 400, tol=0)

print()
if FAILURES:
    print(f"불일치 {len(FAILURES)}건: {FAILURES}")
    sys.exit(1)
print("모든 검산 일치.")
