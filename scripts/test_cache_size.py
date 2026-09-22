"""캐시 토큰 수가 입력 크기에 비례하는지 확인.

5.7절에서 히트 시 캐시 토큰이 입력 크기(16,821~18,656)와 무관하게 항상 16,349~16,351이었다.
입력을 절반 수준으로 줄여 같은 요청을 두 번 보내면 두 가설이 갈린다.
  (a) 캐시 토큰 ≈ 입력 − 꼬리 일부  → 입력에 비례해 줄어든다
  (b) 첫 캐시 항목 크기가 고정된다     → 여전히 16,3xx 이거나 전혀 다른 값

usage: .venv/bin/python scripts/test_cache_size.py
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import MissingKey, OpenRouterClient
from prompt_render import render_system
ROOT = Path(__file__).resolve().parents[1]
FX = json.loads((ROOT / "tests/fixtures/profile_full.json").read_text("utf-8"))

def build(hist):
    return render_system({**{k: FX[k] for k in ("char_name","user_name","char_description","user_description","char_keywordbook")},
                          "chat_history": hist, "option": {"impersonation": False}})

def main():
    try: c = OpenRouterClient(provider="google-ai-studio")
    except MissingKey as e: raise SystemExit(f"[실행 안 함] {e}")
    half = FX["chat_history"][: len(FX["chat_history"]) // 2]
    recs = []
    print(f"{'회차':10} {'입력':>7} {'캐시':>7} {'비율':>6} {'입력−캐시':>9}")
    for label, hist in (("half-1", half), ("half-2", half), ("half-3", half + "\n하람: \"음.\"")):
        r = c.complete(build(hist), "\"오늘은 좀 오래 있어도 돼?\"", max_tokens=1200, reasoning_max_tokens=128)
        if not r.ok: print(label, "실패", r.error[:100]); break
        cached = r.tokens_cached or 0
        print(f"{label:10} {r.tokens_in:>7,} {cached:>7,} {cached/r.tokens_in if cached else 0:>5.0%} {r.tokens_in-cached if cached else '-':>9}")
        recs.append({"run": label, "result": r.as_record()}); time.sleep(2)
    (ROOT/"tests/results/cachesize.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    print("비용:", round(sum(x["result"]["cost_usd_api"] for x in recs), 4))
main()
