"""채택 보조 모델의 한국어 memory 추출 품질 소규모 평가.

명시 사실, 수정, 추측, 가정, 약속 취소, 호칭, 관계 변경, 장식 묘사,
완료 사건을 각각 한 번 검사한다. 자동 문자열 검사는 회귀 기준선일 뿐 의미 품질의
완전한 판정이 아니므로 응답 전문도 결과 파일에 보존한다.

usage: .venv/bin/python scripts/evaluate_extraction_quality.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import MissingKey, OpenRouterClient  # noqa: E402
from measure_extraction_latency import INSTRUCTION  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "cases" / "extraction_eval.json"
RESULTS = ROOT / "tests" / "results"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemini-2.5-flash-lite")
    ap.add_argument("--budget-usd", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=20260921)
    args = ap.parse_args()
    cases = json.loads(CASES.read_text("utf-8"))["cases"]
    try:
        client = OpenRouterClient(model=args.model)
    except MissingKey as exc:
        raise SystemExit(str(exc))

    rows, spent = [], 0.0
    for case in cases:
        if spent >= args.budget_usd:
            break
        user = ("<existing_memory>\n" + case["existing_memory"]
                + "\n</existing_memory>\n<transcript>\n" + case["transcript"]
                + "\n</transcript>")
        result = client.complete(INSTRUCTION, user, max_tokens=500,
                                 reasoning_max_tokens=0, temperature=0.0,
                                 seed=args.seed)
        if isinstance(result.cost_usd_api, float):
            spent += result.cost_usd_api
        text = result.text if result.ok else ""
        missing = [value for value in case["require_all"] if value not in text]
        forbidden = [value for value in case["forbid"] if value in text]
        profile_match = re.search(r"<profile>(.*?)</profile>", text, re.DOTALL)
        profile = profile_match.group(1) if profile_match else ""
        forbidden_profile = [value for value in case.get("forbid_profile", [])
                             if value in profile]
        content_only = re.sub(r"<[^>]+>", " ", text)
        letters = [char for char in content_only if char.isalpha()]
        korean_ratio = (sum("가" <= char <= "힣" for char in letters) / len(letters)
                        if letters else 1.0)
        language_ok = not case["require_all"] or korean_ratio >= 0.5
        ok = (result.ok and not missing and not forbidden and not forbidden_profile
              and language_ok)
        rows.append({
            "id": case["id"], "ok": ok, "missing": missing,
            "forbidden_found": forbidden, "forbidden_profile_found": forbidden_profile,
            "korean_ratio": round(korean_ratio, 3), "response": text,
            "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
            "cost_usd": result.cost_usd_api, "error": result.error,
        })
        print(f"{'PASS' if ok else 'FAIL'} {case['id']}"
              f" missing={missing or '-'} forbidden={(forbidden + forbidden_profile) or '-'}"
              f" korean={korean_ratio:.2f}")

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out = RESULTS / f"extraction_quality_{stamp}.json"
    payload = {
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "model": args.model, "seed": args.seed, "passed": sum(r["ok"] for r in rows),
        "total": len(rows), "cost_usd": round(spent, 6), "cases": rows,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n{payload['total']}건 중 {payload['passed']}건 통과 | ${spent:.4f}")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
