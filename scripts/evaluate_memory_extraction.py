"""플레이그라운드 기억 추출 정확도 평가 (비용 발생).

실측 대화 기록(tests/results/demo_smoke_*.json)의 사용자 발화와 캐릭터 응답을 그대로
다시 쌓으면서 demo/engine.py의 추출을 같은 시점(6회마다 + 종료 정리)에 실행하고,
tests/cases/memory_gold.json의 정답으로 L1을 채점한다. 같은 버전을 --repeats번 돌려
실행 간 편차도 함께 본다. 메인 모델은 호출하지 않는다.

usage: .venv/bin/python scripts/evaluate_memory_extraction.py --repeats 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "demo")]
import engine as E  # noqa: E402
from extraction import STATE_PREFIXES  # noqa: E402
from llm_client import MissingKey, OpenRouterClient  # noqa: E402

GOLD = ROOT / "tests" / "cases" / "memory_gold.json"


class CostRecorder:
    def __init__(self, client) -> None:
        self.client, self.model, self.cost = client, client.model, 0.0

    def complete(self, system, user, **kw):
        res = self.client.complete(system, user, **kw)
        if isinstance(res.cost_usd_api, float):
            self.cost += res.cost_usd_api
        return res


def score(facts: list[str], spec: dict, turn: int) -> dict:
    """L1 항목('항목: 값')을 정답과 비교한다. turn까지 나온 사실만 요구한다.

    이 정답은 사용자 사실만 다루므로 상태 변화 항목(소유·호칭·관계·약속상태)은 채점에서 뺀다.
    상태 변화는 scripts/evaluate_state_changes.py가 따로 잰다.
    """
    facts = [f for f in facts if not f.startswith(STATE_PREFIXES)]
    found, wrong, missing = [], [], []
    for req in spec["required"]:
        if req["from_turn"] > turn:
            continue
        hits = [f for f in facts if re.search(req["match"], f.split(":", 1)[0] + f)]
        good = [f for f in hits if re.search(req["value"], f)]
        (found if good else missing).append(req["name"])
        wrong += [f for f in hits if not re.search(req["value"], f)
                  and re.search(req["match"], f.split(":", 1)[0])]
    forbidden = [f"{rule['name']}: {f}" for rule in spec["forbidden"] for f in facts
                 if re.search(rule["match"], f)
                 and not (rule.get("unless") and re.search(rule["unless"], f))]
    return {"turn": turn, "found": found, "missing": missing, "wrong": wrong,
            "forbidden": forbidden, "l1": facts}


def replay(log: list[dict], spec: dict, client) -> list[dict]:
    sample = json.loads((ROOT / spec["sample_file"]).read_text("utf-8"))
    s = E.new_session(E.Character(**sample["character"]), E.Persona(**sample["persona"]))
    checks = []
    for r in log:
        s.store.append_turn(s.scope, spec["user"], r["user"])
        s.store.append_turn(s.scope, spec["char"], r["reply"])
        if E.extraction_due(s):
            ok = E.run_extraction(s, client)
            checks.append({**score(E.memory_view(s)[0], spec, r["turn"]), "ok": ok})
    if s.store.pending_turns(s.scope):                       # 세션 종료 정리(flush)
        ok = E.run_extraction(s, client)
        checks.append({**score(E.memory_view(s)[0], spec, log[-1]["turn"]), "ok": ok, "final": True})
    return checks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--label", default="", help="결과 파일 이름에 붙일 버전 이름")
    args = ap.parse_args()
    gold = json.loads(GOLD.read_text("utf-8"))
    try:
        client = CostRecorder(OpenRouterClient("google/gemini-2.5-flash-lite"))
    except MissingKey as exc:
        raise SystemExit(str(exc))

    runs = []
    for name, spec in gold["samples"].items():
        for log_path in spec["logs"]:
            log = json.loads((ROOT / log_path).read_text("utf-8"))["log"]
            for rep in range(args.repeats):
                checks = replay(log, spec, client)
                runs.append({"sample": name, "log": log_path, "repeat": rep, "checks": checks})
                final = checks[-1]
                print(f"{name} {Path(log_path).stem[-15:]} #{rep + 1}: "
                      f"최종 {len(final['found'])}/{len(final['found']) + len(final['missing'])} "
                      f"| 틀린 값 {len(final['wrong'])} | 금지 {len(final['forbidden'])}"
                      + (f" | 누락 {final['missing']}" if final["missing"] else "")
                      + (f" | {final['wrong'][:1] + final['forbidden'][:1]}"
                         if final["wrong"] or final["forbidden"] else ""))

    all_checks = [c for r in runs for c in r["checks"]]
    finals = [r["checks"][-1] for r in runs]
    req_total = sum(len(c["found"]) + len(c["missing"]) for c in all_checks)
    summary = {
        "checkpoint_recall": round(sum(len(c["found"]) for c in all_checks) / max(1, req_total), 3),
        "final_recall": round(sum(len(c["found"]) for c in finals)
                              / max(1, sum(len(c["found"]) + len(c["missing"]) for c in finals)), 3),
        "wrong_values": sum(len(c["wrong"]) for c in all_checks),
        "forbidden_items": sum(len(c["forbidden"]) for c in all_checks),
        "runs_with_any_error": sum(any(c["wrong"] or c["forbidden"] for c in r["checks"]) for r in runs),
        "runs": len(runs), "extraction_failures": sum(not c["ok"] for c in all_checks),
    }
    print(f"\n요약: {summary} | ${client.cost:.4f}")
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "tests" / "results" / f"memory_extraction_{args.label + '_' if args.label else ''}{stamp}.json"
    out.write_text(json.dumps({
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "model": client.model, "repeats": args.repeats, "summary": summary,
        "cost_usd": round(client.cost, 6), "runs": runs,
    }, ensure_ascii=False, indent=1), "utf-8")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
