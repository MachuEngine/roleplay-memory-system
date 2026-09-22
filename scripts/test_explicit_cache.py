"""[대리 측정] 명시적 캐싱이 참조만 하면 항상 히트하는지 확인.

메인 모델(2.5 Pro)은 신규 키로 호출할 수 없어, 호출 가능한
gemini-3.1-flash-lite로 캐시 생성·참조라는 API 메커니즘 자체를 시험한다.
명시적 캐싱은 모델이 아니라 API 리소스 기능이라 모델 종속성이 낮을 것으로
기대하지만, 2.5 Pro의 실제 수치는 아니다.

절차
  1. 정적 콘텐츠(운영 프로필 규모)로 캐시를 생성한다
  2. 같은 캐시를 참조하며 꼬리(대화 내역)를 바꿔가며 5회 호출한다
  3. 매 호출의 cached_content_token_count를 본다 — 전부 캐시 크기와 같아야
     "참조=히트"가 성립한다
  4. 캐시를 삭제한다

usage: .venv/bin/python scripts/test_explicit_cache.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_render import render_system  # noqa: E402
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FX = json.loads((ROOT / "tests/fixtures/profile_full.json").read_text("utf-8"))
MODEL = "gemini-3.1-flash-lite"

TAILS = [
    "",
    "\n하람: \"저 C열 정리 안 해?\"\n서리: \"언젠가.\"",
    "\n하람: \"비 온다더니 진짜 오네.\"\n서리: \"...그러게.\"",
    "\n하람: \"오늘은 일찍 가야 해.\"\n서리: \"그래.\"",
    "\n하람: \"저기 봐, 눈 온다.\"\n서리: \"…진짜네.\"",
]


def main() -> None:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise SystemExit("[실행 안 함] GEMINI_API_KEY 없음")
    c = genai.Client(api_key=key)

    # 2026-09-18 확인: 무료 티어는 크기와 무관하게 명시적 캐시 저장이 차단된다.
    # 최소 크기(1024토큰) 미만 → 400 "too small". 1024 이상 → 429
    # "TotalCachedContentStorageTokensPerModelFreeTier limit=0". 결제 등록(Tier 1)
    # 없이는 명시적 캐싱 자체를 호출할 수 없다.

    static = render_system({
        "char_name": FX["char_name"], "user_name": FX["user_name"],
        "char_description": FX["char_description"], "user_description": FX["user_description"],
        "char_keywordbook": FX["char_keywordbook"], "chat_history": "",
        "option": {"impersonation": False},
    })

    print(f"모델 {MODEL} (직접 API, 대리 측정) — 명시적 캐싱\n")
    cache = c.caches.create(
        model=MODEL,
        config=types.CreateCachedContentConfig(contents=static, ttl="300s"),
    )
    print(f"캐시 생성: {cache.name} / 캐시된 토큰 {cache.usage_metadata.total_token_count}")

    recs = []
    print(f"\n{'회차':6} {'입력토큰':>8} {'캐시토큰':>8} {'비율':>6}")
    try:
        for i, tail in enumerate(TAILS, 1):
            try:
                r = c.models.generate_content(
                    model=MODEL,
                    contents=FX["chat_history"][:200] + tail + "\n\"오늘은 좀 오래 있어도 돼?\"",
                    config=types.GenerateContentConfig(
                        cached_content=cache.name, max_output_tokens=200),
                )
            except Exception as e:
                print(f"R{i}   실패: {str(e)[:150]}")
                continue
            u = r.usage_metadata
            cached = u.cached_content_token_count or 0
            print(f"R{i:5} {u.prompt_token_count:>8,} {cached:>8,} "
                  f"{cached / u.prompt_token_count if cached else 0:>5.0%}")
            recs.append({"run": i, "prompt_tokens": u.prompt_token_count, "cached": cached})
            time.sleep(2)
    finally:
        c.caches.delete(name=cache.name)
        print(f"\n캐시 삭제 완료: {cache.name}")

    (ROOT / "tests/results/explicit_cache_proxy.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    hits = sum(1 for r in recs if r["cached"] > 0)
    print(f"\n히트 {hits}/{len(recs)}회")


if __name__ == "__main__":
    main()
