"""운영 프로필에서 암시적 캐시 히트와 출력 길이를 측정한다.

5장 실호출(입력 772~1,504토큰)은 암시적 캐싱 최소 조건 2,048에 미달해 히트가 0이었다.
여기서는 설계가 전제한 입력(약 14,000토큰)으로 호출해 두 가지를 본다.

  (1) 정적 프리픽스가 실제로 캐시되는가 — 2.3절 "배치 순서가 곧 원가 설계"의 근거
  (2) 컨텍스트가 10배 커지면 출력이 길어지는가 — 5.5절 실패 2의 미지수

호출 순서
  A. 대화 내역 원본
  B. 대화 내역 뒤에 2턴 추가 (정적 프리픽스는 동일, 꼬리만 다름) ← 실제 운영과 같은 조건
  C. B와 완전히 동일 (상한 확인)

usage: .venv/bin/python scripts/test_cache_profile.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import UNKNOWN, MissingKey, OpenRouterClient  # noqa: E402
from prompt_render import render_system, unresolved  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FX = json.loads((ROOT / "tests" / "fixtures" / "profile_full.json").read_text("utf-8"))
LOGS, RESULTS = ROOT / "tests" / "logs", ROOT / "tests" / "results"

# 원가 모델과 같은 보수적 경계: 동적 구간 전 system 780 + UGC 6,000 + L1·L2 900.
# 이 fixture의 keywordbook은 호출 간 고정이므로 provider 동작 측정용이지 L3 변동 검증은 아니다.
STATIC_PREFIX_TOKENS = 780 + 6000 + 900
USER_MESSAGE = "\"오늘은 좀 오래 있어도 돼?\""
EXTRA = ("\n하람: \"저 C열 정리 안 해?\"\n"
         "서리: *책장을 넘기며.* \"언젠가.\"")


def build(history: str, impersonation: bool = False) -> str:
    ctx = {k: FX[k] for k in ("char_name", "user_name", "char_description", "user_description",
                              "char_keywordbook")}
    ctx["chat_history"] = history
    ctx["option"] = {"impersonation": impersonation}
    s = render_system(ctx)
    assert not unresolved(s), unresolved(s)
    return s


def main() -> None:
    try:
        client = OpenRouterClient()
    except MissingKey as e:
        raise SystemExit(f"[실행 안 함] {e}")

    h0 = FX["chat_history"]
    runs = [("A", h0, "원본"), ("B", h0 + EXTRA, "2턴 추가"), ("C", h0 + EXTRA, "2턴 추가(B와 동일)"),
            ("D", h0, "원본(A와 동일)"), ("E", h0 + EXTRA + "\n하람: \"갈까.\"", "3턴 추가(신규)")]
    LOGS.mkdir(parents=True, exist_ok=True); RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    recs, spent = [], 0.0

    print(f"{'회차':4} {'입력':>7} {'캐시':>7} {'캐시비율':>7} {'출력':>6} {'think':>6} "
          f"{'글자':>6} {'종료':>6} {'비용($)':>10}")
    for label, hist, variant in runs:
        system = build(hist)
        res = client.complete(system, USER_MESSAGE, max_tokens=1200, reasoning_max_tokens=128)
        if not res.ok:
            print(f"{label:4} 실패: {res.error[:120]}")
            break
        if isinstance(res.cost_usd_api, float):
            spent += res.cost_usd_api
        cached = res.tokens_cached if isinstance(res.tokens_cached, int) else 0
        rate = cached / res.tokens_in if cached else 0.0
        print(f"{label:4} {res.tokens_in:>7,} {cached:>7,} {rate:>6.0%} "
              f"{res.tokens_out:>6,} {res.tokens_reasoning:>6} {len(res.text):>6,} "
              f"{res.finish_reason:>6} {res.cost_usd_api:>10}")
        recs.append({
            "run": label,
            "called_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "history_variant": variant,
            "result": res.as_record(),
            "static_prefix_tokens": STATIC_PREFIX_TOKENS,
            "cached_ratio_vs_input": round(rate, 4),
            "response_chars": len(res.text),
        })
        time.sleep(2)

    path = RESULTS / f"cache_{stamp}.json"
    path.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    (LOGS / f"cache_{stamp}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs), encoding="utf-8")
    print(f"\n청구 합계: ${spent:.4f}\n저장: {path}")


if __name__ == "__main__":
    main()
