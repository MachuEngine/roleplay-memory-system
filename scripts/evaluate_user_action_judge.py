"""사용자 행동 대리 2단계 판정 후보 비교: LLM 판정 대 자세 규칙 (LLM 쪽은 비용 발생).

tests/cases/user_action_judge.json의 사람 라벨과 비교한다. LLM은 운영과 같게 응답 하나에서
나온 문장을 한 번에 묻는다. 정규식만 쓰면 라벨된 문장이 모두 '위반'으로 판정된다.
플레이그라운드는 측정 결과 자세 규칙(demo/judge.py)을 쓴다.

usage: .venv/bin/python scripts/evaluate_user_action_judge.py --model google/gemini-2.5-flash-lite
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "demo")]
from judge import invents_posture  # noqa: E402
from llm_client import MissingKey, OpenRouterClient  # noqa: E402

_VERDICT = re.compile(r"^\s*(\d+)\s*[:.)]\s*(위반|정상)", re.MULTILINE)
JUDGE_INSTRUCTION = """롤플레이 응답 검사기다. 캐릭터 응답에서 사용자를 언급한 문장마다, 캐릭터가 사용자의 행동을 대신 지어냈는지 판정한다.
기본값은 정상이다. 아래 위반 조건에 분명히 해당할 때만 위반으로 판정한다.

위반 (모두 해당해야 함):
1. 문장이 사용자를 주어로, 사용자의 지금 행동·자세·위치·시선·표정·반응·말을 사실로 단정한다.
   또는 사용자가 한 적 없는 과거 발언을 했다고 단정한다.
2. 그 내용이 <user_messages>에 없다. 비슷한 뜻으로 이미 쓴 행동이면 해당하지 않는다.
   예: 메시지에 없는데 "창가에 앉아 있는 하람", "하람이 종이를 넘기는 소리", "하람은 고개를 끄덕였다"

정상:
- 문장의 주어가 캐릭터다. 예: "서리는 그 자리에 서 있었다", "서리는 하람이 있는 쪽을 바라봤다"
- 사용자 메시지에 쓴 행동이나 그 결과 상태를 다시 말한다. 예: 책을 집었다고 쓴 뒤 "하람이 들고 있는 책"
- 사용자가 그 자리에 있다는 사실만 말한다. 예: "하람이 아직 안에 있다"
- 캐릭터의 생각·추측·기대·바람·인상이거나, 모른다고 말한다.
  예: "~하기를 기다렸다", "~할까 봐", "~했을까", "~같았다", "어떤 표정인지 알 수 없었다"
- 사용자의 말이나 메시지를 가리키거나 해석한다. 예: "하람이 던진 질문에", "비웃기라도 하듯 말을 이어갔다"
- 사용자의 평소 습관, 이미 대화에 나온 과거 일, 캐릭터의 대사 안 내용이다.

문장 번호마다 한 줄씩 "번호: 위반" 또는 "번호: 정상"만 출력한다."""


class JudgeError(RuntimeError):
    pass


def judge(client, user_name, char_name, recent_user_messages, sentences) -> list[bool]:
    msgs = "\n".join(f"- {m}" for m in recent_user_messages)
    sents = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, 1))
    user = (f"사용자: {user_name}\n캐릭터: {char_name}\n<user_messages>\n{msgs}\n</user_messages>\n"
            f"마지막 줄이 이번 메시지다.\n<sentences>\n{sents}\n</sentences>")
    res = client.complete(JUDGE_INSTRUCTION, user, max_tokens=300, reasoning_max_tokens=0,
                          temperature=0.0, seed=None)
    if not res.ok:
        raise JudgeError(res.error or "판정 호출 실패")
    found = {int(n): w == "위반" for n, w in _VERDICT.findall(res.text)}
    if set(found) != set(range(1, len(sentences) + 1)):
        raise JudgeError(f"판정 {len(found)}/{len(sentences)}개만 읽힘")
    return [found[i] for i in range(1, len(sentences) + 1)]


def score(rows, key) -> tuple[float, float, int, int, int]:
    tp = sum(r["violation"] and r[key] for r in rows)
    fp = sum(not r["violation"] and r[key] for r in rows)
    fn = sum(r["violation"] and not r[key] for r in rows)
    return (tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0, tp, fp, fn)


CASES = ROOT / "tests" / "cases" / "user_action_judge.json"


class CostRecorder:
    def __init__(self, client) -> None:
        self.client, self.cost = client, 0.0

    def complete(self, system, user, **kw):
        res = self.client.complete(system, user, **kw)
        if isinstance(res.cost_usd_api, float):
            self.cost += res.cost_usd_api
        return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemini-2.5-flash-lite")
    args = ap.parse_args()
    data = json.loads(CASES.read_text("utf-8"))
    try:
        client = CostRecorder(OpenRouterClient(args.model))
    except MissingKey as exc:
        raise SystemExit(str(exc))

    groups: dict[str, list[dict]] = defaultdict(list)
    for item in data["items"]:
        groups[item["source"]].append(item)
    rows, failures = [], 0
    for source, items in groups.items():
        try:
            verdicts = judge(client, data["user_name"], data["char_name"],
                             items[0]["recent_user_messages"], [x["sentence"] for x in items])
        except JudgeError as exc:
            failures += 1
            verdicts = [True] * len(items)          # 판정 실패 시 운영은 정규식 결과(위반)로 둔다
            print(f"판정 실패 {source}: {exc}")
        for item, v in zip(items, verdicts):
            rows.append({**item, "predicted": v,
                         "rule": invents_posture(item["sentence"], data["user_name"],
                                                 item["recent_user_messages"])})

    precision, recall, tp, fp, fn = score(rows, "predicted")
    tn = len(rows) - tp - fp - fn
    pos = sum(r["violation"] for r in rows)
    print(f"정규식만: 정밀도 {pos / len(rows):.2f} · 재현율 1.00 (라벨된 문장 기준)")
    rp, rr, rtp, rfp, rfn = score(rows, "rule")
    print(f"자세 규칙: 정밀도 {rp:.2f} · 재현율 {rr:.2f} (TP {rtp} / FP {rfp} / FN {rfn}) | 비용 없음")
    print(f"{args.model}: 정밀도 {precision:.2f} · 재현율 {recall:.2f} "
          f"(TP {tp} / FP {fp} / FN {fn} / TN {tn}, 판정 실패 {failures}건) | ${client.cost:.4f}")
    for r in rows:
        if r["violation"] != r["predicted"]:
            kind = "놓침" if r["violation"] else "오탐"
            print(f"  LLM {kind} #{r['id']}: {r['sentence'][:70]}")

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "tests" / "results" / f"user_action_judge_{stamp}.json"
    out.write_text(json.dumps({
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "model": args.model, "precision": round(precision, 3), "recall": round(recall, 3),
        "rule_precision": round(rp, 3), "rule_recall": round(rr, 3),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn, "judge_failures": failures,
        "cost_usd": round(client.cost, 6), "rows": rows,
    }, ensure_ascii=False, indent=1), "utf-8")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
