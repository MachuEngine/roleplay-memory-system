"""후행 앵커 블록(프롬프트 말미 반복 지시) 유무에 따른 출력 형식 비교.

쌍 설계: 같은 케이스 × 같은 seed 를 한 쌍으로 묶어 paired t-test 한다.
케이스 간 분산(픽스처 차이)은 쌍 내부에서 상쇄되고, 남는 것은 샘플링 노이즈다.

usage:
  .venv/bin/python scripts/compare_tail.py tests/logs/tail_ab      # 디렉터리 일괄
  .venv/bin/python scripts/compare_tail.py <with.jsonl> <without.jsonl>
"""
from __future__ import annotations

import json
import math
import re
import statistics as st
import sys
from pathlib import Path

TARGET_LEN = (900, 1200)
TARGET_PARAS = (5, 6)
# paired t 양측 0.05 임계값. df 크면 1.96 으로 수렴한다.
T_CRIT = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31,
          9: 2.26, 10: 2.23, 15: 2.13, 20: 2.09, 30: 2.04, 40: 2.02, 60: 2.00, 120: 1.98}


def t_crit(df: int) -> float:
    for k in sorted(T_CRIT):
        if df <= k:
            return T_CRIT[k]
    return 1.96


def read(path: Path) -> dict[tuple[str, int], str]:
    """(test_id, seed) → 응답 텍스트."""
    out = {}
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        res, req = r["result"], r["request"]
        if isinstance(res, str):
            res = eval(res)
        if isinstance(req, str):
            req = eval(req)
        if res.get("ok"):
            out[(r["test_id"], req["seed"])] = res["text"]
    return out


def korean_ratio(t: str) -> float:
    letters = [c for c in t if c.isalpha()]
    return sum("가" <= c <= "힣" for c in letters) / len(letters) if letters else 0.0


def paras(t: str) -> list[str]:
    return [p for p in t.split("\n") if p.strip()]


def metrics(t: str) -> dict[str, float]:
    ps = paras(t)
    return {
        "chars": len(t),
        "len_ok": float(TARGET_LEN[0] <= len(t) <= TARGET_LEN[1]),
        "ko": korean_ratio(t),
        "ko_pure": float(korean_ratio(t) >= 0.99),
        "paras": len(ps),
        "para_ok": float(TARGET_PARAS[0] <= len(ps) <= TARGET_PARAS[1]),
        "asterisk": len(re.findall(r"\*[^*\n]+\*", t)),
        "quotes": len(re.findall(r"[\"“][^\"”\n]+[\"”]", t)),
        "name_label": len(re.findall(r"(?m)^\s*[가-힣]{1,4}\s*:", t)),
    }


def paired(diffs: list[float]) -> tuple[float, float, float, float, bool]:
    """평균차, 95% CI 하·상한, t, 유의 여부."""
    n = len(diffs)
    m = st.mean(diffs)
    if n < 2 or st.pstdev(diffs) == 0:
        return m, m, m, float("nan"), False
    s = st.stdev(diffs)
    se = s / math.sqrt(n)
    tc = t_crit(n - 1)
    t = m / se if se else float("nan")
    return m, m - tc * se, m + tc * se, t, abs(t) > tc


def main() -> None:
    args = [Path(a) for a in sys.argv[1:]]
    if len(args) == 1 and args[0].is_dir():
        A = {}
        B = {}
        for f in sorted(args[0].glob("with_seed*.jsonl")):
            A.update(read(f))
        for f in sorted(args[0].glob("without_seed*.jsonl")):
            B.update(read(f))
    else:
        A, B = read(args[0]), read(args[1])

    keys = sorted(set(A) & set(B))
    if not keys:
        raise SystemExit("짝지을 (케이스, seed) 쌍이 없다")
    seeds = sorted({k[1] for k in keys})
    cases = sorted({k[0] for k in keys})
    print(f"쌍 {len(keys)}개 — 케이스 {len(cases)}종 × seed {len(seeds)}종 {seeds}\n")

    MA = {k: metrics(A[k]) for k in keys}
    MB = {k: metrics(B[k]) for k in keys}

    print("### 주 지표 — 평균 응답 길이 (연속형, paired t-test)\n")
    d = [MA[k]["chars"] - MB[k]["chars"] for k in keys]
    m, lo, hi, t, sig = paired(d)
    va = st.mean(MA[k]["chars"] for k in keys)
    vb = st.mean(MB[k]["chars"] for k in keys)
    dz = m / st.stdev(d) if len(d) > 1 and st.stdev(d) else float("nan")
    print(f"| 앵커 有 | 앵커 無 | 평균차 | 95% CI | t | dz | 판정 |")
    print("|---|---|---|---|---|---|---|")
    print(f"| {va:.0f}자 | {vb:.0f}자 | {m:+.1f}자 | {lo:+.0f} ~ {hi:+.0f} | "
          f"{t:.2f} | {dz:.2f} | **{'유의' if sig else '유의하지 않음'}** |")

    print("\n### 보조 지표 (기술 통계)\n")
    print("| 지표 | 앵커 有 | 앵커 無 | 차이 |")
    print("|---|---|---|---|")
    for key, label, pct in [("ko", "한국어 비율", False), ("paras", "평균 문단 수", False),
                            ("asterisk", "*행동* 수", False), ("quotes", '"대사" 수', False),
                            ("len_ok", f"{TARGET_LEN[0]}~{TARGET_LEN[1]}자 준수", True),
                            ("para_ok", f"{TARGET_PARAS[0]}~{TARGET_PARAS[1]}문단 준수", True),
                            ("ko_pure", "한국어 ≥0.99", True)]:
        xa = st.mean(MA[k][key] for k in keys)
        xb = st.mean(MB[k][key] for k in keys)
        f = (lambda v: f"{v:.1%}") if pct else (lambda v: f"{v:.2f}")
        print(f"| {label} | {f(xa)} | {f(xb)} | {xb - xa:+.3f} |")

    nl_a = sum(MA[k]["name_label"] for k in keys)
    nl_b = sum(MB[k]["name_label"] for k in keys)
    print(f"| `이름:` 대본 형식 (총계) | {nl_a:.0f}건 | {nl_b:.0f}건 | — |")

    print("\n### 한국어 누수 상한 (rule of three)\n")
    for label, M in [("앵커 有", MA), ("앵커 無", MB)]:
        fails = sum(1 for k in keys if M[k]["ko_pure"] < 1)
        n = len(keys)
        if fails == 0:
            print(f"- {label}: 누수 {fails}/{n} → 95% 상한 **{3 / n:.1%}**")
        else:
            print(f"- {label}: 누수 {fails}/{n} = {fails / n:.1%}")

    print("\n### 케이스별 평균 길이 (seed 평균)\n")
    print("| 케이스 | 앵커 有 | 앵커 無 | 차이 |")
    print("|---|---|---|---|")
    for c in cases:
        ks = [k for k in keys if k[0] == c]
        xa = st.mean(MA[k]["chars"] for k in ks)
        xb = st.mean(MB[k]["chars"] for k in ks)
        print(f"| {c} | {xa:.0f} | {xb:.0f} | {xb - xa:+.0f} |")


if __name__ == "__main__":
    main()
