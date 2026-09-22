"""실호출 로그 → 설계 문서용 요약 표.

tests/results/live_*.json 중 가장 최근 파일을 읽어 두 개의 마크다운 조각을 만든다.
  tests/results/summary_body.md    본문(9절)에 넣을 요약
  tests/results/summary_appendix.md 부록에 넣을 호출 기록·케이스별 판정

로그가 없으면 그 사실을 그대로 출력한다. 수치를 지어내지 않는다.

usage: .venv/bin/python scripts/summarize_tests.py
"""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "tests" / "results"
UNKNOWN = "확인 불가"


def fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".") if v < 1 else f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def main() -> None:
    files = sorted(RESULTS.glob("live_*.json"))
    if not files:
        print("실행 로그가 없습니다. scripts/run_live_tests.py 를 먼저 실행하세요.")
        sys.exit(2)

    # 여러 번 실행했다면 케이스별로 가장 최근 결과를 채택한다.
    # 총 호출 수와 총 비용은 재시험분을 포함한 전체 기준으로 센다.
    template_sha = hashlib.sha256(
        (ROOT / "prompts" / "system.hbs").read_bytes()
    ).hexdigest()[:16]
    all_recs: list[dict] = []
    for f in files:
        all_recs.extend(
            row for row in json.loads(f.read_text("utf-8"))
            if row.get("template_sha256") == template_sha
        )
    if not all_recs:
        print("현재 프롬프트 버전의 실행 로그가 없습니다.")
        sys.exit(2)
    latest: dict[str, dict] = {}
    for r in all_recs:
        latest[r["test_id"]] = r
    order = [c["id"] for c in json.loads(
        (ROOT / "tests" / "cases" / "live_cases.json").read_text("utf-8"))["cases"]]
    recs = [latest[i] for i in order if i in latest]
    RESULTS.mkdir(exist_ok=True)

    total_calls = len(all_recs)
    total_cost = sum(r["result"]["cost_usd_api"] for r in all_recs
                     if isinstance(r["result"]["cost_usd_api"], float))
    retried = sorted({r["test_id"] for r in all_recs
                      if sum(x["test_id"] == r["test_id"] for x in all_recs) > 1})

    called = len(recs)
    errored = [r for r in recs if not r["result"]["ok"]]
    auto = [r for r in recs if r["auto_pass"] is not None]
    passed = [r for r in auto if r["auto_pass"]]
    cost = sum(r["result"]["cost_usd_api"] for r in recs
               if isinstance(r["result"]["cost_usd_api"], float))
    providers = sorted({r["result"]["routed_provider"] for r in recs})
    served = sorted({r["result"]["served_model"] for r in recs})

    body = [
        f"- 케이스 {called}종. 총 호출 {total_calls}건(재시험 포함). "
        f"요청 모델 `{recs[0]['request']['model']}`.",
        f"- 실제 라우팅 공급자: {', '.join(providers)} / 응답 `model` 필드: {', '.join(served)}",
        f"- 규칙 기반 자동 판정(최신 결과 기준): {len(passed)}/{len(auto)}건 통과",
        f"- API 오류: {len(errored)}건",
        f"- 재시험한 케이스: {', '.join(retried) if retried else '없음'}",
        f"- OpenRouter 청구 합계: ${total_cost:.4f}",
    ]
    versions = {r.get("template_sha256", "확인 불가") for r in recs}
    if len(versions) > 1:
        print(f"경고: 채택된 결과가 서로 다른 프롬프트 버전이다 — {sorted(versions)}",
              file=sys.stderr)
    body.append(f"- 프롬프트 템플릿 버전(sha256 앞 16자리): {', '.join(sorted(versions))}")
    (RESULTS / "summary_body.md").write_text("\n".join(body) + "\n", encoding="utf-8")

    head = ("| 테스트 | 호출 시각 | 공급자 | 입력 | 출력 | thinking | 캐시 | 종료 | 지연 | "
            "실제 비용(USD) | 기준 단가 환산(원) | 자동판정 |")
    rows = [head, "|" + "---|" * 12]
    for r in recs:
        res = r["result"]
        nf = sum(not c["pass"] for c in r["auto_checks"])
        rows.append("| " + " | ".join([
            r["test_id"], r["called_at"][11:19], fmt(res["routed_provider"]),
            fmt(res["tokens_in"]), fmt(res["tokens_out"]), fmt(res["tokens_reasoning"]),
            fmt(res["tokens_cached"]), fmt(res["finish_reason"]), f"{res['latency_s']}s",
            fmt(res["cost_usd_api"]), fmt(r["cost_krw_assignment_rate"]),
            "통과" if nf == 0 else f"실패 {nf}건",
        ]) + " |")

    detail = ["", "### 케이스별 자동 검사 실패 항목", ""]
    any_fail = False
    for r in recs:
        fails = [c for c in r["auto_checks"] if not c["pass"]]
        if fails:
            any_fail = True
            detail.append(f"**{r['test_id']}**")
            detail += [f"- {c['check']} — {c['detail']}" for c in fails]
    if not any_fail:
        detail.append("없음.")

    manual = ["", "### 수동 평가 대상 (자동 판정 불가)", "",
              "| 테스트 | 평가 기준 | 응답 근거(발췌) |", "|---|---|---|"]
    for r in recs:
        manual.append(f"| {r['test_id']} | " + "<br>".join(r["manual_criteria"]) +
                      " | " + r["evidence"].replace("|", "/") + " |")

    (RESULTS / "summary_appendix.md").write_text(
        "\n".join(rows + detail + manual) + "\n", encoding="utf-8")
    print(f"생성: {RESULTS/'summary_body.md'}, {RESULTS/'summary_appendix.md'}")
    print("\n".join(body))


if __name__ == "__main__":
    main()
