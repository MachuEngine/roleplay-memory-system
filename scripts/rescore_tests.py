"""저장된 응답을 다시 채점한다. API를 호출하지 않는다.

판정 규칙을 고쳤을 때 비용 없이 기존 로그에 적용하기 위한 도구다.
tests/results/live_*.json 을 제자리에서 갱신한다.

usage: .venv/bin/python scripts/rescore_tests.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live_tests as R  # noqa: E402
from llm_client import ChatResult  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "tests" / "results"


def main() -> None:
    cases = {c["id"]: c for c in R.SUITE["cases"]}
    files = sorted(RESULTS.glob("live_*.json"))
    if not files:
        raise SystemExit("채점할 결과 파일이 없습니다.")
    for f in files:
        recs = json.loads(f.read_text("utf-8"))
        for r in recs:
            case = cases.get(r["test_id"])
            if case is None or not r["result"]["ok"]:
                continue
            res = ChatResult(**{k: v for k, v in r["result"].items()})
            r["auto_checks"] = R.run_checks(case, res)
            r["auto_pass"] = all(c["pass"] for c in r["auto_checks"])
            r["manual_criteria"] = case["manual"]
        f.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
        fails = [(r["test_id"], [c["check"] for c in r["auto_checks"] if not c["pass"]])
                 for r in recs if r["auto_pass"] is False]
        print(f"{f.name}: {len(recs)}건 재채점, 실패 {len(fails)}건")
        for tid, cs in fails:
            print(f"  {tid}: {cs}")


if __name__ == "__main__":
    main()
