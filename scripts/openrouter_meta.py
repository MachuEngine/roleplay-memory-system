"""OpenRouter가 공개하는 google/gemini-2.5-pro 모델 메타데이터 확인.

인증이 필요 없는 GET이며 과금되지 않는다. 부록 A의 가격 교차 확인 근거를 만든다.

usage: .venv/bin/python scripts/openrouter_meta.py
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path

MODEL = "google/gemini-2.5-pro"
OUT = Path(__file__).resolve().parents[1] / "tests" / "results" / "model_meta.json"


def main() -> None:
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
        data = json.load(r)["data"]
    hit = next((m for m in data if m["id"] == MODEL), None)
    if hit is None:
        raise SystemExit(f"{MODEL} 을 목록에서 찾지 못했습니다.")

    pr = hit["pricing"]
    def per_1m(k: str) -> str:
        if k not in pr:
            return "확인 불가"
        v = float(pr[k]) * 1_000_000
        return f"${v:,.2f}" if v >= 1 else f"${v:,.3f}"
    rows = [
        ("prompt (input)", per_1m("prompt")),
        ("completion (output)", per_1m("completion")),
        ("internal_reasoning (thinking)", per_1m("internal_reasoning")),
        ("input_cache_read", per_1m("input_cache_read")),
        ("input_cache_write", per_1m("input_cache_write")),
    ]
    print(f"{MODEL}  (확인일 {date.today()})\n")
    for k, v in rows:
        print(f"  {k:<32} {v} / 1M tokens")
    over = pr.get("overrides") or []
    for o in over:
        print(f"  ├ {o['min_prompt_tokens']:,} 초과 구간: "
              f"prompt ${float(o['prompt'])*1e6:,.2f} / completion ${float(o['completion'])*1e6:,.2f}")
    print(f"\n  context_length {hit['context_length']:,} / "
          f"max_completion_tokens {hit.get('top_provider', {}).get('max_completion_tokens')}")
    print(f"  supported_parameters: {', '.join(hit['supported_parameters'])}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"checked_on": str(date.today()), "model": hit},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
