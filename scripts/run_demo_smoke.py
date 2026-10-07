"""플레이그라운드 엔진 실호출 스모크 테스트 (비용 발생).

샘플 캐릭터(서리 / 하람)로 정해 둔 대화를 demo/engine.py에 그대로 흘려 보낸다.
--turns 8(기본)은 짧은 확인용이고, --turns 20은 세션 상한까지 진행한다.
20턴 시나리오
  - 2~3턴에 생일·약속을 말하고, 그 대화가 대화 창에서 밀려난 16·18턴에 다시 묻는다
    → 기억(L1·L2)만으로 회상하는지 본다
  - 6·7·11·14턴은 사용자 행동을 지어내기 쉬운 상황이다
  - 9·10턴은 캐릭터 금기(가족, 책방 매각)를 건드린다
  - 12~13턴은 말했다가 취소한 사실(이사)이 기억에 확정 사실로 남는지 본다
턴마다 guard 결과, 의심 문장, 응답 전문, 대화 창 범위, 주입된 기억, 비용을 기록한다.
판정은 사람이 응답을 읽고 한다.

usage: .venv/bin/python scripts/run_demo_smoke.py --turns 20 --budget-usd 0.7
"""
from __future__ import annotations

import argparse
import json
import re
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
    '*보리차를 한 모금 마셨다.* "오늘 진짜 손님 없네."',
    '"삼촌 얘기 좀 해 줄 수 있어? 이 책방 원래 삼촌 거였다며."',
    '"그럼 너네 가족은? 연락은 해?"',
    '"솔직히 물어볼게. 이 책방 팔 생각은 없어?"',
    '*가방에서 마감 원고를 꺼내 카운터 끝에 펼쳤다.* "여기서 일 좀 해도 돼?"',
    '"나 사실 다음 달에 회사 근처로 이사 갈지도 몰라."',
    '"농담이야. 아직 정한 건 하나도 없어."',
    '*펜을 내려놓고 창밖을 봤다.* "비 그쳤다."',
    '"오늘 너랑 이렇게 오래 얘기한 거 처음인 것 같아."',
    '"내 생일 언제라고 했지? 기억 못 하면 서운할 거야."',
    '"그날 뭐 해 줄 거야?"',
    '"바다 가자고 한 거, 언제 가기로 했었지?"',
    '"이사 얘기는 신경 쓰지 마. 진짜 정한 거 없어."',
    '*원고를 가방에 챙겨 넣었다.* "나 이제 갈게. 다음에 또 올게."',
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
    ap.add_argument("--turns", type=int, default=8, help="샘플 시나리오 앞에서부터 몇 턴을 쓸지")
    ap.add_argument("--sample", default="seori", choices=["seori", "haeden"],
                    help="seori: demo/sample_character.json, haeden: tests/fixtures/sample_haeden.json")
    args = ap.parse_args()
    try:
        main_client = OpenRouterClient("google/gemini-2.5-pro")
        extract_client = CostRecorder(OpenRouterClient("google/gemini-2.5-flash-lite"))
    except MissingKey as exc:
        raise SystemExit(str(exc))

    if args.sample == "haeden":
        sample = json.loads((ROOT / "tests" / "fixtures" / "sample_haeden.json").read_text("utf-8"))
        base_script = sample["script"]
    else:
        sample = json.loads((ROOT / "demo" / "sample_character.json").read_text("utf-8"))
        base_script = SCRIPT
    session = E.new_session(E.Character(**sample["character"]), E.Persona(**sample["persona"]))
    script = base_script[:args.turns]
    daily = DailyCounter(len(script))
    spent, turns, extractions = 0.0, [], []

    def cost(calls) -> float:
        return sum(c["cost_usd"] for c in calls if isinstance(c["cost_usd"], float))

    for i, message in enumerate(script, 1):
        if spent + extract_client.cost >= args.budget_usd:
            print(f"예산 ${args.budget_usd} 도달 — {i}턴부터 중단")
            break
        history = E.chat_history(session)          # respond()가 이번 호출에 쓰는 것과 같다
        keywordbook = session.store.build_keywordbook(session.scope, paid=False)
        first_user = next((n for n, m in enumerate(script, 1) if m in history), None)
        text, record = E.respond(session, message, main_client, daily)
        spent += cost(record.calls)
        turns.append({"turn": i, "user": message, "reply": text,
                      "history_tokens_est": E.est_tokens(history),
                      "history_first_user_turn": first_user,
                      "keywordbook": keywordbook, **asdict(record)})
        print(f"[{i}] {record.status} | 창 시작 {first_user}턴 | 재생성 {record.errors}"
              f" | 의심 {len(record.warnings)} | ${spent + extract_client.cost:.4f}")
        if E.extraction_due(session):
            ok = E.run_extraction(session, extract_client)
            facts, eps = E.memory_view(session)
            extractions.append({"after_turn": i, "ok": ok, "profile": facts, "episodes": eps})
            print(f"    추출 {'성공' if ok else '실패'} | L1 {facts} | L2 {eps}")

    statuses = [t["status"] for t in turns]
    shown = [t for t in turns if t["status"] not in ("차단", "호출 실패")]
    narr = lambda s: " ".join(re.findall(r"\*([^*]+)\*", s))  # noqa: E731
    speech = lambda s: " ".join(re.findall(r'"([^"]+)"', s))  # noqa: E731
    metrics = {
        "avg_chars": round(sum(len(t["reply"]) for t in shown) / max(1, len(shown))),
        "over_1200_chars": sum(len(t["reply"]) > 1200 for t in shown),
        "narration_first_second_person": sum(
            bool(re.search(r"(^|\s)(나는|내가|네가|너는)\s", narr(t["reply"]))) for t in shown),
        "warning_turns": sum(bool(t["warnings"]) for t in shown),
        "regenerated_for_posture": sum(any(e.startswith(E.POSTURE_PREFIX) for errs in t["errors"]
                                           for e in errs) for t in turns),
        "speech_polite_turns": sum(bool(re.search(r"(요[.?!~…]|니다|세요)", speech(t["reply"])))
                                   for t in shown),
    }
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "tests" / "results" / f"demo_smoke_{stamp}.json"
    payload = {
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "main_model": main_client.model, "extract_model": extract_client.model,
        "sample": args.sample, "turns": len(turns),
        "status_counts": {s: statuses.count(s) for s in set(statuses)}, "metrics": metrics,
        "main_cost_usd": round(spent, 6), "extract_cost_usd": round(extract_client.cost, 6),
        "extractions": extractions, "log": turns,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n{payload['status_counts']} | {metrics}")
    print(f"메인 ${spent:.4f} + 추출 ${extract_client.cost:.4f}")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
