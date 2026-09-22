"""OpenRouter를 통한 Gemini 2.5 Pro 실호출 테스트 실행기.

- 입력은 tests/fixtures/fixtures.json + tests/cases/live_cases.json 에서만 읽는다(재현 가능).
- 자동 판정 가능한 항목만 규칙 기반으로 검사하고, 나머지는 수동 평가 대상으로 남긴다.
- 누적 비용이 예산을 넘기기 전에 중단한다. 자동 반복 호출은 하지 않는다.

usage:
  .venv/bin/python scripts/run_live_tests.py --dry-run          # 키 없이 렌더링만 확인
  .venv/bin/python scripts/run_live_tests.py --smoke            # 2건만 호출
  .venv/bin/python scripts/run_live_tests.py                    # 전체 14건 1회 실행
  .venv/bin/python scripts/run_live_tests.py --only MEM-02      # 특정 케이스 재실행
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import UNKNOWN, ChatResult, MissingKey, OpenRouterClient  # noqa: E402
from prompt_render import render_system, unresolved  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "fixtures.json").read_text("utf-8"))
SUITE = json.loads((ROOT / "tests" / "cases" / "live_cases.json").read_text("utf-8"))
LOGS = ROOT / "tests" / "logs"
RESULTS = ROOT / "tests" / "results"

# 설계 기준 단가 (docs/03-assumptions.md A절). 실제 청구액과 구분해서 기록한다.
ASSIGN_IN_USD_PER_1M, ASSIGN_OUT_USD_PER_1M, FX = 1.25, 10.0, 1500
SMOKE_IDS = ["RP-01", "INJ-01"]


TEMPLATE_OVERRIDE: Path | None = None


def build_inputs(case: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    ch = FIXTURES["chars"][case["char"]]
    ctx = {
        **ch,
        "char_keywordbook": FIXTURES["memories"][case["memory"]],
        "chat_history": FIXTURES["histories"][case["history"]],
        "option": {"impersonation": bool(case["impersonation"])},
    }
    system = render_system(ctx, TEMPLATE_OVERRIDE) if TEMPLATE_OVERRIDE else render_system(ctx)
    left = unresolved(system)
    if left:
        raise SystemExit(f"{case['id']}: 미치환 플레이스홀더 {left}")
    return system, case["user_message"], ctx


# ---------------------------------------------------------------- 규칙 기반 판정
def korean_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum("가" <= c <= "힣" for c in letters) / len(letters)


def run_checks(case: dict[str, Any], res: ChatResult) -> list[dict[str, Any]]:
    """자동 판정 결과. 판정할 수 없는 항목은 아예 만들지 않는다."""
    c, t, out = case["checks"], res.text, []

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append({"check": name, "pass": ok, "detail": detail})

    low = t.lower()
    for s in c.get("forbid_substrings", []):
        add(f"금지 문자열 없음: {s!r}", s.lower() not in low,
            "" if s.lower() not in low else f"응답에 {s!r} 등장")
    for p in c.get("forbid_regex", []):
        m = re.search(p, t)
        add(f"금지 패턴 없음: /{p}/", m is None, "" if not m else f"매치: {m.group(0)[:60]!r}")
    for p in c.get("require_regex", []):
        m = re.search(p, t)
        add(f"필수 패턴 존재: /{p}/", m is not None)
    for spec in c.get("max_matches", []):
        n = len(re.findall(spec["pattern"], t))
        add(f"패턴 {spec['pattern']} 등장 ≤ {spec['max']}회", n <= spec["max"], f"실측 {n}회")
    if (lo := c.get("korean_ratio_min")) is not None:
        r = korean_ratio(t)
        add(f"한국어 비율 ≥ {lo}", r >= lo, f"실측 {r:.2f}")
    if (allowed := c.get("finish_reason_in")) is not None:
        add(f"종료 사유 ∈ {allowed}", res.finish_reason in allowed, f"실제 {res.finish_reason}")
    if (rng := c.get("char_len_range")) is not None:
        n = len(t)
        add(f"응답 길이 {rng[0]}~{rng[1]}자", rng[0] <= n <= rng[1], f"실측 {n}자")
    return out


def assignment_cost_krw(res: ChatResult) -> float | str:
    """기준 단가로 재계산한 원화 비용. thinking은 출력 토큰에 포함되어 과금된다."""
    if not isinstance(res.tokens_in, int) or not isinstance(res.tokens_out, int):
        return UNKNOWN
    usd = (res.tokens_in * ASSIGN_IN_USD_PER_1M + res.tokens_out * ASSIGN_OUT_USD_PER_1M) / 1_000_000
    return round(usd * FX, 3)


def evidence(text: str, limit: int = 240) -> str:
    """수동 평가자가 볼 근거 발췌(앞부분)."""
    s = " ".join(text.split())
    return s[:limit] + ("…" if len(s) > limit else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="호출 없이 렌더링·토큰 추정만")
    ap.add_argument("--smoke", action="store_true", help=f"{SMOKE_IDS} 만 호출")
    ap.add_argument("--only", nargs="*", help="특정 케이스 ID만 실행")
    ap.add_argument("--budget-usd", type=float, default=2.0, help="누적 비용 상한")
    ap.add_argument("--model", default="google/gemini-2.5-pro")
    ap.add_argument("--reasoning-max-tokens", type=int, default=128)
    ap.add_argument("--template", default=None, help="후보 템플릿 경로 (기본 prompts/system.hbs)")
    ap.add_argument("--seed", type=int, default=None,
                    help="샘플링 seed. 기본은 live_cases.json의 defaults.seed. "
                         "동일 seed는 동일 응답을 반환하므로 반복 측정 시 반드시 바꿔야 한다")
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else SUITE["defaults"]["seed"]
    global TEMPLATE_OVERRIDE
    if args.template:
        TEMPLATE_OVERRIDE = Path(args.template).resolve()

    cases = SUITE["cases"]
    if args.smoke:
        cases = [c for c in cases if c["id"] in SMOKE_IDS]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    if not cases:
        raise SystemExit("실행할 케이스가 없습니다.")

    if args.dry_run:
        for case in cases:
            system, user, _ = build_inputs(case)
            print(f"{case['id']:8} system {len(system):5}자 / user {len(user):4}자 "
                  f"/ 사칭 {'ON ' if case['impersonation'] else 'OFF'} / {case['goal']}")
        print(f"\n렌더링 {len(cases)}건 정상. 호출하지 않았습니다.")
        return

    try:
        client = OpenRouterClient(model=args.model)
    except MissingKey as e:
        print(f"[실행 안 함] {e}", file=sys.stderr)
        print("연동 코드와 테스트 데이터는 준비되어 있습니다. "
              "--dry-run 으로 렌더링만 확인할 수 있습니다.", file=sys.stderr)
        raise SystemExit(2)

    LOGS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    # 병렬 seed 반복도 서로의 증빙 파일을 덮어쓰지 않도록 microsecond까지 포함한다.
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S_%f")
    log_path = LOGS / f"live_{stamp}.jsonl"
    records: list[dict[str, Any]] = []
    spent = 0.0

    with log_path.open("w", encoding="utf-8") as log:
        for case in cases:
            if spent >= args.budget_usd:
                print(f"[중단] 누적 ${spent:.4f} ≥ 예산 ${args.budget_usd}. 남은 케이스는 실행하지 않습니다.")
                break
            system, user, _ = build_inputs(case)
            print(f"→ {case['id']} 호출 …", flush=True)
            res = client.complete(system, user,
                                  max_tokens=SUITE["defaults"]["max_tokens"],
                                  reasoning_max_tokens=args.reasoning_max_tokens,
                                  temperature=SUITE["defaults"]["temperature"],
                                  seed=seed)
            if isinstance(res.cost_usd_api, float):
                spent += res.cost_usd_api
            checks = run_checks(case, res) if res.ok else []
            rec = {
                "test_id": case["id"],
                "goal": case["goal"],
                "called_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "impersonation": case["impersonation"],
                "fixtures": {k: case[k] for k in ("char", "memory", "history")},
                "prompt_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest()[:16],
                "template_sha256": hashlib.sha256(
                    (TEMPLATE_OVERRIDE or ROOT / "prompts" / "system.hbs").read_bytes()).hexdigest()[:16],
                "template_path": str((TEMPLATE_OVERRIDE or ROOT / "prompts" / "system.hbs").relative_to(ROOT)),
                "request": {"model": args.model,
                            "max_tokens": SUITE["defaults"]["max_tokens"],
                            "reasoning_max_tokens": args.reasoning_max_tokens,
                            "seed": seed,
                            "system_chars": len(system), "user_chars": len(user)},
                "result": res.as_record(),
                "cost_krw_assignment_rate": assignment_cost_krw(res),
                "auto_checks": checks,
                "auto_pass": all(c["pass"] for c in checks) if checks else None,
                "manual_criteria": case["manual"],
                "evidence": evidence(res.text),
            }
            records.append(rec)
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.flush()
            status = "OK" if res.ok else f"FAIL {res.error[:80]}"
            nfail = sum(not c["pass"] for c in checks)
            print(f"   {status} | in {res.tokens_in} out {res.tokens_out} "
                  f"think {res.tokens_reasoning} cached {res.tokens_cached} "
                  f"| {res.finish_reason} | ${res.cost_usd_api} | 자동검사 실패 {nfail}건")
            time.sleep(1)

    (RESULTS / f"live_{stamp}.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n로그: {log_path}\n요약 입력: {RESULTS / f'live_{stamp}.json'}")
    print(f"OpenRouter 청구 합계: ${spent:.4f} (예산 ${args.budget_usd})")


if __name__ == "__main__":
    main()
