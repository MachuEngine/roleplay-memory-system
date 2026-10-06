"""공급자를 고정했을 때의 암시적 캐시 동작.

5.7절에서 꼬리(대화 내역)만 바뀐 요청이 캐시에 히트하지 않았다. 원인 후보 중 하나가
라우팅이다 — OpenRouter는 같은 모델에 7개 엔드포인트(Vertex global/eu/us, AI Studio 등)를
두고 있고, 암시적 캐싱은 같은 백엔드로 요청이 모여야 성립한다.

공급자를 하나로 고정하고 같은 실험을 반복한다.

  R1  정적 프리픽스 P + 꼬리 T1 (신규)        → 캐시 생성, 미스 예상
  R2  P + T1 (R1과 완전히 동일)               → 히트 예상
  R3  P + T2 (꼬리만 다름)                    ← 결정적 회차
  R4  P + T3 (꼬리만 다름, 다시)              ← 재확인
  R5  P + T2 (R3 반복)                        → 히트 예상

usage: .venv/bin/python scripts/test_cache_pinned.py [--provider google-ai-studio]
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

# 원가 모델과 같은 보수적 경계: system 780 + UGC 6,000 + L1·L2 900.
STATIC_PREFIX = 780 + 6000 + 900
T1 = ""
T2 = "\n하람: \"저 C열 정리 안 해?\"\n서리: *책장을 넘기며.* \"언젠가.\""
T3 = "\n하람: \"비 온다더니 진짜 오네.\"\n서리: *문을 조금 밀어 열었다.* \"...그러게.\""


def build(tail: str) -> str:
    s = render_system({
        "char_name": FX["char_name"], "user_name": FX["user_name"],
        "char_description": FX["char_description"], "user_description": FX["user_description"],
        "char_keywordbook": FX["char_keywordbook"],
        "chat_history": FX["chat_history"] + tail,
        "option": {"impersonation": False},
    })
    assert not unresolved(s)
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="google-ai-studio")
    a = ap.parse_args()
    try:
        client = OpenRouterClient(provider=a.provider)
    except MissingKey as e:
        raise SystemExit(f"[실행 안 함] {e}")

    runs = [("R1", T1, "신규 (캐시 생성)"), ("R2", T1, "R1과 완전 동일"),
            ("R3", T2, "꼬리만 다름 ← 결정적"), ("R4", T3, "꼬리만 다름 (재확인)"),
            ("R5", T2, "R3 반복")]
    recs, spent = [], 0.0
    print(f"공급자 고정: {a.provider}\n")
    print(f"{'회차':4} {'조건':22} {'입력':>7} {'캐시':>7} {'비율':>6} {'공급자':>10} {'비용($)':>10}")
    for label, tail, note in runs:
        res = client.complete(build(tail), "\"오늘은 좀 오래 있어도 돼?\"",
                              max_tokens=1200, reasoning_max_tokens=128)
        if not res.ok:
            print(f"{label:4} 실패: {res.error[:140]}")
            break
        if isinstance(res.cost_usd_api, float):
            spent += res.cost_usd_api
        cached = res.tokens_cached if isinstance(res.tokens_cached, int) else 0
        print(f"{label:4} {note:22} {res.tokens_in:>7,} {cached:>7,} "
              f"{cached / res.tokens_in if cached else 0:>5.0%} {res.routed_provider:>10} "
              f"{res.cost_usd_api:>10}")
        recs.append({"run": label, "condition": note, "provider_pinned": a.provider,
                     "called_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                     "static_prefix_tokens": STATIC_PREFIX, "result": res.as_record()})
        time.sleep(2)

    RESULTS.mkdir(parents=True, exist_ok=True); LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    (RESULTS / f"cachepin_{stamp}.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    (LOGS / f"cachepin_{stamp}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs), encoding="utf-8")
    print(f"\n청구 합계: ${spent:.4f}")


if __name__ == "__main__":
    main()
