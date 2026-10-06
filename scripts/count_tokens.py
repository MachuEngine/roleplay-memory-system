"""시스템 프롬프트 토큰 수 **추정**.

시스템 프롬프트의 예상 토큰 수를 내려는 것이므로 문자 기반 휴리스틱으로
산정한다. API 실측은 요구되지 않으며, 수행하지도 않았다(docs/05-pricing.md 4절).

추정식 (SentencePiece 계열 다국어 토크나이저의 일반적 압축률):
  - 라틴 문자·공백·기호: 4.0 chars/token
  - 한글 음절: 1.5 chars/token
  - CJK 기타·기타 문자: 1.5 chars/token
보수적으로 ±15% 구간을 함께 제시한다.

usage: .venv/bin/python scripts/count_tokens.py
"""
import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "prompts" / "system.hbs"

CHARS_PER_TOKEN_LATIN = 4.0
CHARS_PER_TOKEN_HANGUL = 1.5
UNCERTAINTY = 0.15

# 런타임에 실제 값으로 치환되는 이름 (짧은 문자열이므로 대표값으로 계산)
NAME_SAMPLES = {"{{char_name}}": "세아", "{{user_name}}": "지훈"}

# 별도 예산으로 집계되는 대형 슬롯 — 시스템 프롬프트 토큰에서 제외
CONTENT_SLOTS = [
    "{{char_description}}",
    "{{user_description}}",
    "{{char_keywordbook}}",
    "{{chat_history}}",
]

HANGUL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")


def estimate(text: str) -> float:
    hangul = len(HANGUL.findall(text))
    other = len(text) - hangul
    return hangul / CHARS_PER_TOKEN_HANGUL + other / CHARS_PER_TOKEN_LATIN


def render(raw: str, impersonation: bool) -> str:
    """핸들바 조건부 블록을 분기별로 전개하고, 대형 슬롯을 제거한다."""
    pattern = re.compile(
        r"\{\{#if option\.impersonation\}\}(.*?)\{\{else\}\}(.*?)\{\{/if\}\}", re.S
    )
    text = pattern.sub(lambda m: m.group(1) if impersonation else m.group(2), raw)
    for ph, sample in NAME_SAMPLES.items():
        text = text.replace(ph, sample)
    for slot in CONTENT_SLOTS:
        text = text.replace(slot, "")
    return text


def main() -> None:
    raw = TEMPLATE.read_text(encoding="utf-8")

    print("## 시스템 프롬프트 토큰 추정 (문자 기반)\n")
    print("| 사칭 모드 | 문자 수 | 한글 | 추정 토큰 | 하한(-15%) | 상한(+15%) |")
    print("|---|---|---|---|---|---|")
    results = {}
    for label, imp in [("ON", True), ("OFF", False)]:
        text = render(raw, imp)
        tokens = estimate(text)
        hangul = len(HANGUL.findall(text))
        results[label] = tokens
        print(
            f"| {label} | {len(text):,} | {hangul} | **{tokens:.0f}** | "
            f"{tokens * (1 - UNCERTAINTY):.0f} | {tokens * (1 + UNCERTAINTY):.0f} |"
        )

    worst = max(results.values())
    print(f"\n설계 채택값(보수적, 사칭 ON 기준): **{round(worst / 10) * 10}** 토큰")
    print(f"상한 기준 최악값: {worst * (1 + UNCERTAINTY):.0f} 토큰")
    on_text = render(raw, True)
    empty_memory = "<memory>\n\n</memory>"
    boundary = on_text.index(empty_memory) + len("<memory>\n")
    print(f"동적 memory payload 앞 고정 prefix 추정: **{estimate(on_text[:boundary]):.0f}** 토큰")
    print("\n주: 여기서 필요한 것은 예상치이므로 추정으로 충분하다. ±15% 구간을 병기한다.")


if __name__ == "__main__":
    main()
