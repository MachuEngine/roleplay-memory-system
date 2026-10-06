"""보조 모델(Flash-Lite) 추출 호출의 실제 완료 시간 측정.

3.3절의 `overlap` 상한과 `degraded path` 전환 조건이 이 값에 의존한다.
운영 규모와 같은 입력(밀려날 6턴 5,400 + 기존 memory 1,200 + 지시 400 = 약 7,000토큰)으로
호출하고 벽시계 시간을 잰다.

usage: .venv/bin/python scripts/measure_extraction_latency.py --n 20
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import MissingKey, OpenRouterClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "results" / "extraction_latency.json"

INSTRUCTION = """한국어 롤플레이 대화에서 장기 기억만 추출한다.
다음 두 블록만 정확히 출력한다:
<profile>
- 호칭: 선배
</profile>
<episodes>
- 시집을 찾아 건넸고 사용자가 받았다
</episodes>
태그 안의 항목명과 값은 모두 한국어로 쓴다. 중첩 XML 태그를 만들지 않는다.
위 내용은 형식 예시일 뿐이며 입력에 없는 선배·시집 정보를 복사하지 않는다.
저장할 내용이 없으면 해당 블록을 비워 둔다.
profile: 확정된 관계, 호칭, 말투, 약속, 사용자가 직접 밝힌 사실의 현재값만 저장한다.
episodes: 이후 상태를 바꾼 사건과 최종 결과만 저장한다.
원문 보존(L3)은 이 호출의 일이 아니다. 대상 구간은 전량 색인되므로 발췌를 고르지 않는다.
사용자가 직접 말했거나 장면에서 실제로 확정된 정보만 저장한다. 부정되거나 확인되지 않은
추측, 가정, 취소된 계획을 활성 약속으로 만든 내용, 장식 묘사, 캐릭터 설정에 이미 있는 사실은
두 블록 모두에서 제외한다. 충돌 시 사용자 명시 > 추론, 최신 > 과거, profile > episodes 순이다."""


USER_LINES = [
    "그래서 어떻게 됐어?", "……그 얘기는 하고 싶지 않아.", "오늘은 좀 일찍 닫네.",
    "이거 네가 골라준 거 맞지?", "비 그치면 같이 나가자.", "왜 자꾸 딴 데 봐.",
]


def build_payload(fixtures: dict, turns_n: int = 6) -> tuple[str, str]:
    """운영 규모와 같은 추출 입력을 만든다.

    밀려나는 6턴(사용자 100 + AI 800 ≈ 900토큰)을 실제 모델 응답으로 구성한다.
    합성 문자열을 반복하면 토큰 분포가 실제와 달라지므로, 회귀 로그의 실응답을 쓴다.
    """
    import json as _json
    logs = sorted((ROOT / "tests" / "logs").glob("live_*.jsonl"))
    replies: list[str] = []
    for f in reversed(logs):
        for line in f.read_text("utf-8").splitlines():
            r = _json.loads(line)
            res = r["result"]
            if isinstance(res, str):
                res = eval(res)
            if res.get("ok") and len(res["text"]) > 700:
                replies.append(res["text"])
        if len(replies) >= turns_n:
            break
    if len(replies) < turns_n:
        raise SystemExit("실응답을 충분히 찾지 못했다. 회귀 테스트를 먼저 실행하라")

    turns = []
    for i in range(turns_n):
        turns.append(f"user: {USER_LINES[i % len(USER_LINES)]}")
        turns.append(f"assistant: {replies[i]}")
    mem = fixtures["memories"]["consistent"]
    user = ("<existing_memory>\n" + mem + "\n</existing_memory>\n\n"
            "<transcript>\n" + "\n\n".join(turns) + "\n</transcript>")
    return INSTRUCTION, user


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--model", default="google/gemini-3.1-flash-lite")
    ap.add_argument("--budget-usd", type=float, default=0.30)
    ap.add_argument("--turns", type=int, default=6, help="추출 대상 턴 수")
    a = ap.parse_args()

    fixtures = json.loads((ROOT / "tests" / "fixtures" / "fixtures.json").read_text("utf-8"))
    system, user = build_payload(fixtures, a.turns)

    try:
        client = OpenRouterClient(model=a.model)
    except MissingKey as e:
        raise SystemExit(str(e))

    rows, spent = [], 0.0
    for i in range(1, a.n + 1):
        if spent >= a.budget_usd:
            print(f"예산 상한 도달, {i - 1}회에서 중단")
            break
        t0 = time.perf_counter()
        res = client.complete(system, user, max_tokens=800, temperature=1.0, seed=None)
        dt = time.perf_counter() - t0
        if not res.ok:
            print(f"  {i:>2}. 실패 {res.error}")
            continue
        if isinstance(res.cost_usd_api, float):
            spent += res.cost_usd_api
        rows.append({"i": i, "seconds": round(dt, 3),
                     "tokens_in": res.tokens_in, "tokens_out": res.tokens_out})
        print(f"  {i:>2}. {dt:6.2f}s  in {res.tokens_in} out {res.tokens_out}")

    if not rows:
        raise SystemExit("성공한 호출이 없다")
    xs = sorted(r["seconds"] for r in rows)
    p = lambda q: xs[min(len(xs) - 1, int(len(xs) * q))]  # noqa: E731
    summary = {
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "model": a.model, "n": len(xs), "turns": a.turns,
        "tokens_in_avg": round(st.mean(r["tokens_in"] for r in rows)),
        "p50": p(0.50), "p95": p(0.95), "min": xs[0], "max": xs[-1],
        "mean": round(st.mean(xs), 2), "cost_usd": round(spent, 4), "runs": rows,
    }
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n| n | 평균 입력 | p50 | p95 | 최대 |")
    print("|---|---|---|---|---|")
    print(f"| {summary['n']} | {summary['tokens_in_avg']:,} | {summary['p50']:.2f}s | "
          f"{summary['p95']:.2f}s | {summary['max']:.2f}s |")
    print(f"\n저장: {OUT}  (청구 ${spent:.4f})")


if __name__ == "__main__":
    main()
