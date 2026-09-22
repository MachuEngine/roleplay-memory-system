"""연속 턴 시뮬레이션에서의 캐시 히트율.

실제 운영은 매 턴 대화 내역이 누적된다. 정적 프리픽스(시스템 프롬프트 + 유저 콘텐츠
+ 메모리)는 그대로이고 꼬리만 자란다. 2.3절이 전제한 "매 턴 정적 프리픽스 할인"이
성립하는지를 이 패턴 그대로 확인한다.

공급자를 고정한다 — 암시적 캐싱은 같은 백엔드로 요청이 모여야 성립한다.

usage: .venv/bin/python scripts/test_cache_turns.py [--turns 8] [--provider google-ai-studio]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import MissingKey, OpenRouterClient  # noqa: E402
from prompt_render import render_system, unresolved  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FX = json.loads((ROOT / "tests" / "fixtures" / "profile_full.json").read_text("utf-8"))
RESULTS, LOGS = ROOT / "tests" / "results", ROOT / "tests" / "logs"
# 원가 모델과 같은 보수적 경계. L1·L2까지 고정하고 L3 recall은 매 턴 바꾼다.
STATIC_PREFIX = 910 + 6000 + 900

USER_TURNS = [
    "\"오늘은 좀 오래 있어도 돼?\"",
    "\"저 C열은 왜 늘 그대로야.\"",
    "\"비 온다더니 진짜 오네.\"",
    "\"이 책 얼마야?\"",
    "\"삼촌 얘기 물어봐도 돼?\"",
    "\"...미안. 안 물을게.\"",
    "\"보리차 하나 더 가져올까.\"",
    "\"다음 주에도 올게.\"",
]


def build(history: str, turn_no: int) -> str:
    memory = FX["char_keywordbook"]
    core = memory.split("<recall>", 1)[0].rstrip()
    dynamic_recall = (
        "<recall>\n"
        f"(검색 결과 {turn_no}) {USER_TURNS[(turn_no - 1) % len(USER_TURNS)]}\n"
        "</recall>"
    )
    s = render_system({
        "char_name": FX["char_name"], "user_name": FX["user_name"],
        "char_description": FX["char_description"], "user_description": FX["user_description"],
        "char_keywordbook": core + "\n" + dynamic_recall, "chat_history": history,
        "option": {"impersonation": False},
    })
    assert not unresolved(s)
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--provider", default="google-ai-studio")
    a = ap.parse_args()
    try:
        client = OpenRouterClient(provider=a.provider)
    except MissingKey as e:
        raise SystemExit(f"[실행 안 함] {e}")

    history = FX["chat_history"]
    recs, spent = [], 0.0
    print(f"공급자 고정: {a.provider} / 정적 프리픽스 {STATIC_PREFIX:,}토큰\n")
    print(f"{'턴':>3} {'입력':>7} {'캐시':>7} {'입력대비':>8} {'프리픽스대비':>11} "
          f"{'출력':>6} {'비용($)':>11}")
    for i in range(1, a.turns + 1):
        msg = USER_TURNS[(i - 1) % len(USER_TURNS)]
        res = client.complete(build(history, i), msg, max_tokens=1200, reasoning_max_tokens=128)
        if not res.ok:
            print(f"{i:>3} 실패: {res.error[:140]}")
            break
        if isinstance(res.cost_usd_api, float):
            spent += res.cost_usd_api
        cached = res.tokens_cached if isinstance(res.tokens_cached, int) else 0
        print(f"{i:>3} {res.tokens_in:>7,} {cached:>7,} "
              f"{cached / res.tokens_in if cached else 0:>7.0%} "
              f"{min(cached, STATIC_PREFIX) / STATIC_PREFIX if cached else 0:>10.0%} "
              f"{res.tokens_out:>6,} {res.cost_usd_api:>11}")
        recs.append({"turn": i, "provider_pinned": a.provider,
                     "called_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                     "static_prefix_tokens": STATIC_PREFIX, "result": res.as_record()})
        # 다음 턴: 이번 유저 발화와 모델 응답을 내역에 누적한다 (운영과 동일)
        history += f"\n하람: {msg}\n서리: {' '.join(res.text.split())[:400]}"
        time.sleep(2)

    hits = sum(1 for r in recs if r["result"]["tokens_cached"])
    print(f"\n히트 {hits}/{len(recs)}턴 | 청구 합계 ${spent:.4f}")
    if recs:
        miss = [r["result"]["cost_usd_api"] for r in recs if not r["result"]["tokens_cached"]]
        hit = [r["result"]["cost_usd_api"] for r in recs if r["result"]["tokens_cached"]]
        if hit and miss:
            print(f"히트 평균 ${sum(hit)/len(hit):.5f} / 미스 평균 ${sum(miss)/len(miss):.5f}")

    RESULTS.mkdir(parents=True, exist_ok=True); LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    (RESULTS / f"cacheturns_{stamp}.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    (LOGS / f"cacheturns_{stamp}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs), encoding="utf-8")


if __name__ == "__main__":
    main()
