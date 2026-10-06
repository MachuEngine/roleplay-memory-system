"""플레이그라운드 엔진 실호출 스모크 테스트 (비용 발생).

샘플 캐릭터(서리 / 하람)로 정해 둔 8턴을 demo/engine.py에 그대로 흘려 보낸다.
6회째 뒤에 L1·L2 추출이 실행되므로, 7~8턴에서 생일·약속을 다시 묻는다.
턴마다 guard 결과와 응답 전문, 추출 결과, 비용을 기록한다. 판정은 사람이 응답을 읽고 한다.

usage: .venv/bin/python scripts/run_demo_smoke.py --budget-usd 0.5
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))
import engine as E  # noqa: E402
from limits import DailyCounter  # noqa: E402
from llm_client import MissingKey, OpenRouterClient  # noqa: E402

SCRIPT = [
    '*문을 밀고 들어오며 우산을 접었다.* "비 엄청 와. 오늘 좀 있다 가도 돼?"',
    '"다음 주 목요일이 내 생일이야. 그냥 말해 본 거야."',
    '"이번 달 말에 마감 끝나면 같이 바다 보러 가자. 약속한 거다?"',
    '*보리차를 꺼내다 말고 카운터에 놓인 책을 집었다.* "이거 뭐야? 처음 보는 책인데."',
    '"너는 왜 맨날 창가에 앉아? 거기서 뭐 봐?"',
    '*대답 대신 서리를 빤히 쳐다봤다.*',
    '"아까 내가 다음 주에 무슨 날이라고 했는지 기억나?"',
    '"우리 약속한 거 잊은 거 아니지?"',
]


class CostRecorder:
    """추출 호출 비용도 합계에 넣기 위해 클라이언트를 감싼다."""

    def __init__(self, client) -> None:
        self.client, self.model, self.cost = client, client.model, 0.0

    def complete(self, system, user, **kw):
        res = self.client.complete(system, user, **kw)
        if isinstance(res.cost_usd_api, float):
            self.cost += res.cost_usd_api
        return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-usd", type=float, default=0.5)
    args = ap.parse_args()
    try:
        main_client = OpenRouterClient("google/gemini-2.5-pro")
        extract_client = CostRecorder(OpenRouterClient("google/gemini-2.5-flash-lite"))
    except MissingKey as exc:
        raise SystemExit(str(exc))

    sample = json.loads((ROOT / "demo" / "sample_character.json").read_text("utf-8"))
    session = E.new_session(E.Character(**sample["character"]), E.Persona(**sample["persona"]))
    daily = DailyCounter(len(SCRIPT))
    spent, turns, extractions = 0.0, [], []

    def cost(calls) -> float:
        return sum(c["cost_usd"] for c in calls if isinstance(c["cost_usd"], float))

    for i, message in enumerate(SCRIPT, 1):
        if spent + extract_client.cost >= args.budget_usd:
            print(f"예산 ${args.budget_usd} 도달 — {i}턴부터 중단")
            break
        text, record = E.respond(session, message, main_client, daily)
        spent += cost(record.calls)
        turns.append({"turn": i, "user": message, "reply": text, **asdict(record)})
        print(f"[{i}] {record.status} | 위반 {record.errors} | ${spent + extract_client.cost:.4f}")
        if E.extraction_due(session):
            ok = E.run_extraction(session, extract_client)
            facts, eps = E.memory_view(session)
            extractions.append({"after_turn": i, "ok": ok, "profile": facts, "episodes": eps})
            print(f"    추출 {'성공' if ok else '실패'} | L1 {facts} | L2 {eps}")

    statuses = [t["status"] for t in turns]
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "tests" / "results" / f"demo_smoke_{stamp}.json"
    payload = {
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "main_model": main_client.model, "extract_model": extract_client.model,
        "turns": len(turns), "status_counts": {s: statuses.count(s) for s in set(statuses)},
        "main_cost_usd": round(spent, 6), "extract_cost_usd": round(extract_client.cost, 6),
        "extractions": extractions, "log": turns,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n{payload['status_counts']} | 메인 ${spent:.4f} + 추출 ${extract_client.cost:.4f}")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
