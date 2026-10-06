"""출력 길이 하한 미준수(5.5절 실패 2)에 대한 두 가설 시험.

  v_para  : 문단당 문장 수를 명시 (약 +12토큰)
  v_style : <style> 예시를 목표 길이(약 1,000자)로 확장 (약 +450토큰, 정적이라 캐시 대상)
base 와 같은 케이스 3종(RP-01, LEN-01, MEM-04)을 같은 시각에 호출해 비교한다.

usage: .venv/bin/python scripts/test_length_variants.py
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import MissingKey, OpenRouterClient
from prompt_render import render_system
import run_live_tests as R
ROOT = Path(__file__).resolve().parents[1]
CASES = ["RP-01", "LEN-01", "MEM-04"]
VARIANTS = {"base": ROOT/"prompts/system.hbs", "v_para": ROOT/"tests/variants/v_para.hbs", "v_style": ROOT/"tests/variants/v_style.hbs"}

def main():
    try: c = OpenRouterClient()
    except MissingKey as e: raise SystemExit(f"[실행 안 함] {e}")
    cases = {x["id"]: x for x in R.SUITE["cases"]}
    recs = []
    print(f"{'변형':8} {'케이스':7} {'입력':>6} {'출력tok':>7} {'글자':>6} {'문단':>4} {'≥900':>5}")
    for vname, tpl in VARIANTS.items():
        for cid in CASES:
            case = cases[cid]; ch = R.FIXTURES["chars"][case["char"]]
            ctx = {**ch, "char_keywordbook": R.FIXTURES["memories"][case["memory"]],
                   "chat_history": R.FIXTURES["histories"][case["history"]], "option": {"impersonation": False}}
            system = render_system(ctx, tpl)
            r = c.complete(system, case["user_message"], max_tokens=1200, reasoning_max_tokens=128)
            if not r.ok: print(vname, cid, "실패", r.error[:80]); continue
            n = len(r.text); paras = len([p for p in r.text.split("\n\n") if p.strip()])
            print(f"{vname:8} {cid:7} {r.tokens_in:>6,} {r.tokens_out:>7} {n:>6} {paras:>4} {'O' if n>=900 else '-':>5}")
            recs.append({"variant": vname, "test_id": cid, "chars": n, "paragraphs": paras, "result": r.as_record()})
            time.sleep(1)
    (ROOT/"tests/results/length_variants.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    for v in VARIANTS:
        xs = [x["chars"] for x in recs if x["variant"] == v]
        if xs: print(f"{v:8} 평균 {sum(xs)/len(xs):.0f}자, ≥900자 {sum(x>=900 for x in xs)}/{len(xs)}")
    print("비용:", round(sum(x["result"]["cost_usd_api"] for x in recs), 4))
main()
