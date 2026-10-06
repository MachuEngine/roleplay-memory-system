"""OpenRouter의 cache_control(명시적 캐싱)이 실제로 되는지, 히트가 결정적인지 시험.

Gemini 네이티브 명시적 캐싱(caches.create)은 무료 티어에서 저장 한도 0으로
막혀 있었다(scripts/test_explicit_cache.py, 2026-09-18). OpenRouter는 자체
cache_control 방식을 제공하며 Google 계정의 결제 티어와 무관할 수 있다 —
실제로 그런지 호출해서 확인한다.

usage: .venv/bin/python scripts/test_openrouter_cache_control.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_render import render_system  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FX = json.loads((ROOT / "tests/fixtures/profile_full.json").read_text("utf-8"))
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

TAILS = ["", "\n하람: \"저 C열 정리 안 해?\"\n서리: \"언젠가.\"",
         "\n하람: \"비 온다더니 진짜 오네.\"\n서리: \"...그러게.\"",
         "\n하람: \"오늘은 일찍 가야 해.\"\n서리: \"그래.\"",
         "\n하람: \"저기 봐, 눈 온다.\"\n서리: \"…진짜네.\""]


def call(static: str, tail: str, key: str) -> dict:
    body = {
        "model": "google/gemini-2.5-pro",
        "messages": [
            {"role": "system", "content": [
                {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
            ]},
            {"role": "user", "content": [
                {"type": "text", "text": (tail + "\n\"오늘은 좀 오래 있어도 돼?\"")},
            ]},
        ],
        "max_tokens": 200,
        "reasoning": {"max_tokens": 128},
        "seed": 7,
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": {"http_status": e.code, "body": e.read().decode("utf-8", "replace")[:300]}}


def main() -> None:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("[실행 안 함] OPENROUTER_API_KEY 없음")

    static = render_system({
        "char_name": FX["char_name"], "user_name": FX["user_name"],
        "char_description": FX["char_description"], "user_description": FX["user_description"],
        "char_keywordbook": FX["char_keywordbook"], "chat_history": "",
        "option": {"impersonation": False},
    })

    print("OpenRouter cache_control(명시적) 시험 — google/gemini-2.5-pro\n")
    print(f"{'회차':6} {'입력':>8} {'캐시읽기':>8} {'캐시쓰기':>8} {'비율':>6} {'결과'}")
    recs = []
    for i, tail in enumerate(TAILS, 1):
        data = call(static, tail, key)
        if "error" in data:
            print(f"R{i:5} 오류: {data['error']}")
            recs.append({"run": i, "error": data["error"]})
            time.sleep(2)
            continue
        usage = data.get("usage", {})
        pdet = usage.get("prompt_tokens_details", {})
        cached = pdet.get("cached_tokens", 0) or 0
        written = pdet.get("cache_write_tokens", 0) or 0
        prompt = usage.get("prompt_tokens", 0)
        cost = usage.get("cost")
        cdet = usage.get("cost_details") or {}
        print(f"R{i:5} {prompt:>8,} {cached:>8,} {written:>8,} "
              f"{cached / prompt if cached else 0:>5.0%}  cost=${cost}  {cdet}")
        recs.append({"run": i, "prompt_tokens": prompt, "cached_tokens": cached,
                     "cache_write_tokens": written, "cost": cost, "cost_details": cdet,
                     "usage_raw": usage})
        time.sleep(2)

    (ROOT / "tests/results/openrouter_cache_control.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [r for r in recs if "error" not in r]
    hits = sum(1 for r in ok if r["cached_tokens"] > 0)
    print(f"\n성공 {len(ok)}/{len(recs)} | 히트 {hits}/{len(ok) if ok else 0}")


if __name__ == "__main__":
    main()
