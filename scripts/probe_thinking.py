"""Gemini 2.5 Pro thinking budget 실측.

확인 목적 (docs/05-pricing.md 미확정 항목):
  1. thinking_budget 으로 수락되는 최소값 (0 비활성화 가능 여부 포함)
  2. 최소 budget 설정 시 롤플레이 프롬프트의 실제 thoughts_token_count

usage: .venv/bin/python scripts/probe_thinking.py
"""
import os
import sys
from pathlib import Path

from google import genai
from google.genai import types

MODEL = "gemini-2.5-pro"
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# 롤플레이 1턴과 성격이 유사한 프롬프트 (thinking 유발 정도를 현실적으로 보기 위함)
SYSTEM = (
    "너는 롤플레이 채팅의 AI 캐릭터 '세아'다. 20대 후반, 무뚝뚝하지만 속정 깊은 성격. "
    "행동 묘사는 *별표*로, 대사는 따옴표로 쓴다. OOC 발화 금지. 캐릭터를 벗어나지 않는다."
)
USER = "*문을 열고 들어서며* \"늦어서 미안. 많이 기다렸어?\""


def load_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"GEMINI_API_KEY 없음 ({ENV_PATH})")


def probe(client: genai.Client, budget: int | None):
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        max_output_tokens=2048,
    )
    if budget is not None:
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=budget)
    try:
        r = client.models.generate_content(model=MODEL, contents=USER, config=cfg)
    except Exception as e:  # 거부된 budget 값의 에러 메시지가 유효 범위를 드러냄
        msg = str(e).replace("\n", " ")
        return {"budget": budget, "ok": False, "error": msg[:300]}
    u = r.usage_metadata
    return {
        "budget": budget,
        "ok": True,
        "prompt": u.prompt_token_count,
        "thoughts": u.thoughts_token_count,
        "output": u.candidates_token_count,
        "total": u.total_token_count,
        "cached": u.cached_content_token_count,
    }


def main():
    client = genai.Client(api_key=load_key())
    print(f"model={MODEL}\n")
    for budget in [None, 0, 128, 512, 1024]:
        label = "미지정(기본값)" if budget is None else str(budget)
        r = probe(client, budget)
        if r["ok"]:
            print(
                f"budget={label:>12} | OK  prompt={r['prompt']:>5} "
                f"thoughts={r['thoughts']} output={r['output']} "
                f"total={r['total']} cached={r['cached']}"
            )
        else:
            print(f"budget={label:>12} | REJECTED  {r['error']}")


if __name__ == "__main__":
    main()
