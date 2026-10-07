"""캐릭터 행동·대사로 생기는 상태 변화 추출 평가 (비용 발생).

tests/cases/state_change_gold.json의 합성 대화마다 새 세션을 만들고, pre_facts를 넣은 뒤
demo/engine.py의 추출을 한 번 실행해 L1을 정답과 비교한다. 메인 모델은 호출하지 않는다.

usage: .venv/bin/python scripts/evaluate_state_changes.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "demo")]
import engine as E  # noqa: E402
from llm_client import MissingKey, OpenRouterClient  # noqa: E402
from memory_sim import Fact  # noqa: E402

GOLD = ROOT / "tests" / "cases" / "state_change_gold.json"


class CostRecorder:
    def __init__(self, client) -> None:
        self.client, self.cost = client, 0.0

    def complete(self, system, user, **kw):
        res = self.client.complete(system, user, **kw)
        if isinstance(res.cost_usd_api, float):
            self.cost += res.cost_usd_api
        return res


def main() -> None:
    cases = json.loads(GOLD.read_text("utf-8"))["cases"]
    sample = json.loads((ROOT / "demo" / "sample_character.json").read_text("utf-8"))
    try:
        client = CostRecorder(OpenRouterClient("google/gemini-2.5-flash-lite"))
    except MissingKey as exc:
        raise SystemExit(str(exc))

    rows, req_total, req_found, forbidden_hits = [], 0, 0, 0
    for case in cases:
        s = E.new_session(E.Character(**sample["character"]), E.Persona(**sample["persona"]))
        for subject, value, turn in case.get("pre_facts", []):
            s.store._store_fact(s.scope, Fact(subject, value, turn, [turn]))
        offset = 1 + max((t for *_, t in case.get("pre_facts", [])), default=-1)
        for _ in range(offset):                       # pre_facts보다 뒤의 턴 번호가 되도록 채운다
            s.store.append_turn(s.scope, "-", "")
        s.store.queue[s.scope] = []
        for speaker, text in case["turns"]:
            s.store.append_turn(s.scope, speaker, text)
        ok = E.run_extraction(s, client)
        l1 = E.memory_view(s)[0]
        found = [r["name"] for r in case["required"] if any(re.search(r["match"], f) for f in l1)]
        missing = [r["name"] for r in case["required"] if r["name"] not in found]
        hits = [f"{r['name']}: {f}" for r in case["forbidden"] for f in l1 if re.search(r["match"], f)]
        req_total += len(case["required"])
        req_found += len(found)
        forbidden_hits += len(hits)
        rows.append({"id": case["id"], "ok": ok, "l1": l1, "found": found, "missing": missing,
                     "forbidden": hits})
        mark = "PASS" if ok and not missing and not hits else "FAIL"
        print(f"{mark} {case['id']:14} 누락 {missing or '-'} 금지 {hits or '-'}\n     L1 {l1}")

    summary = {"required_recall": round(req_found / max(1, req_total), 3), "required": req_total,
               "forbidden_hits": forbidden_hits, "cases": len(cases),
               "cases_passed": sum(not r["missing"] and not r["forbidden"] and r["ok"] for r in rows)}
    print(f"\n요약: {summary} | ${client.cost:.4f}")
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "tests" / "results" / f"state_changes_{stamp}.json"
    out.write_text(json.dumps({
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "summary": summary, "cost_usd": round(client.cost, 6), "rows": rows,
    }, ensure_ascii=False, indent=1), "utf-8")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
