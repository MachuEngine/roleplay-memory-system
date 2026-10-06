"""시스템 프롬프트 토큰 수 실측.

scripts/count_tokens.py 는 문자 기반 추정이다. 여기서는 실제 모델의 토크나이저가
센 값을 읽는다. 슬롯(char_description 등)을 비워 렌더한 프롬프트로 호출하고
응답의 usage.prompt_tokens 를 본다.

prompt_tokens 에는 채팅 템플릿 오버헤드와 유저 메시지가 포함되므로,
빈 시스템 프롬프트로 한 번 더 호출해 그 차이를 빼서 기준선을 제거한다.

usage: .venv/bin/python scripts/measure_prompt_tokens.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_tokens import estimate, render as render_est  # noqa: E402
from llm_client import MissingKey, OpenRouterClient  # noqa: E402
from prompt_render import render_system  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "results" / "prompt_tokens.json"  # --template 지정 시 _candidate 접미
USER = "."


TPL = ROOT / "prompts" / "system.hbs"


def slots_empty(impersonation: bool) -> str:
    return render_system({
        "char_name": "서리", "user_name": "하람",
        "char_description": "", "user_description": "",
        "char_keywordbook": "", "chat_history": "",
        "option": {"impersonation": impersonation},
    }, TPL)


def main() -> None:
    global TPL
    ap = argparse.ArgumentParser(); ap.add_argument("--template"); a = ap.parse_args()
    if a.template:
        TPL = Path(a.template)
    try:
        client = OpenRouterClient()
    except MissingKey as e:
        raise SystemExit(f"[실행 안 함] {e}")

    # 기준선: 시스템 프롬프트가 비었을 때의 prompt_tokens (템플릿 오버헤드 + 유저 메시지)
    base = client.complete("", USER, max_tokens=16, reasoning_max_tokens=128)
    if not base.ok:
        raise SystemExit(f"기준선 호출 실패: {base.error[:200]}")
    baseline = base.tokens_in

    rows, recs = [], {"measured_on": str(date.today()), "baseline_prompt_tokens": baseline}
    for label, imp in (("OFF", False), ("ON", True)):
        text = slots_empty(imp)
        res = client.complete(text, USER, max_tokens=16, reasoning_max_tokens=128)
        if not res.ok:
            raise SystemExit(f"{label} 호출 실패: {res.error[:200]}")
        measured = res.tokens_in - baseline
        est = round(estimate(render_est(TPL.read_text("utf-8"), imp)))
        rows.append((label, est, measured, measured / est))
        recs[label] = {"prompt_tokens": res.tokens_in, "measured_system_tokens": measured,
                       "heuristic_estimate": est, "cost_usd": res.cost_usd_api}

    print(f"기준선(빈 시스템 프롬프트) prompt_tokens = {baseline}\n")
    print("| 사칭 모드 | 휴리스틱 추정 | 실측 | 추정/실측 |")
    print("|---|---|---|---|")
    for label, est, measured, ratio in rows:
        print(f"| {label} | {est:,} | **{measured:,}** | {est / measured:.2f} |")
    out = OUT if not a.template else OUT.with_name("prompt_tokens_candidate.json")
    out.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
