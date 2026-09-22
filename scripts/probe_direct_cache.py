"""[대리 측정] Gemini Developer API 직접 호출에서 부분 프리픽스 캐시가 히트하는지.

메인 모델(2.5 Pro)은 신규 키로 404라 호출할 수 없다. 이 스크립트는 호출 가능한
gemini-3.1-flash-lite 로 '플랫폼 동작'만 본다 — 정적 프리픽스가 같고 꼬리만 바뀐 요청이
직접 API 경로에서 cached_content_token_count > 0 이 되는가. 2.5 Pro의 히트율을 대신하는
값이 아니며, 설계 문서에도 대리 측정임을 밝혔다.

usage: .venv/bin/python scripts/probe_direct_cache.py
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_render import render_system
from google import genai
from google.genai import types
ROOT = Path(__file__).resolve().parents[1]
FX = json.loads((ROOT / "tests/fixtures/profile_full.json").read_text("utf-8"))
MODEL = "gemini-3.1-flash-lite"

def build(tail):
    return render_system({**{k: FX[k] for k in ("char_name","user_name","char_description","user_description","char_keywordbook")},
                          "chat_history": FX["chat_history"] + tail, "option": {"impersonation": False}})

def main():
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key: raise SystemExit("[실행 안 함] GEMINI_API_KEY 없음")
    c = genai.Client(api_key=key)
    T2 = "\n하람: \"저 C열 정리 안 해?\"\n서리: \"언젠가.\""
    T3 = "\n하람: \"비 온다더니 진짜 오네.\"\n서리: \"...그러게.\""
    runs = [("R1 신규", ""), ("R2 동일", ""), ("R3 꼬리↑", T2), ("R4 꼬리↑", T3), ("R5 R3반복", T2), ("R6 꼬리↑", T3 + "\n하람: \"갈까.\"")]
    recs = []
    print(f"모델 {MODEL} (직접 API, 대리 측정)\n{'회차':10} {'입력':>7} {'캐시':>7} {'비율':>6}")
    for label, tail in runs:
        try:
            r = c.models.generate_content(model=MODEL, contents="\"오늘은 좀 오래 있어도 돼?\"",
                config=types.GenerateContentConfig(system_instruction=build(tail), max_output_tokens=300))
        except Exception as e:
            print(label, "실패:", str(e)[:120]); break
        u = r.usage_metadata
        cached = u.cached_content_token_count or 0
        print(f"{label:10} {u.prompt_token_count:>7,} {cached:>7,} {cached/u.prompt_token_count if cached else 0:>5.0%}")
        recs.append({"run": label, "model": MODEL, "prompt_tokens": u.prompt_token_count, "cached": cached})
        time.sleep(3)
    (ROOT/"tests/results/direct_cache_proxy.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
main()
