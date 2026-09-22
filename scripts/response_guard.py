"""모델 응답을 표시하기 전 구조·언어·사칭 위반을 검사한다.

실패 시 운영 경로는 같은 입력으로 한 번 재생성하고, 다시 실패하면 캐릭터만
행동하는 짧은 안전 응답을 사용한다. 이 스크립트는 저장된 live 결과의 판정을
재현한다.

usage: .venv/bin/python scripts/response_guard.py tests/results/live_....json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from impersonation_guard import validate as validate_impersonation
from run_live_tests import FIXTURES, SUITE, korean_ratio

META = re.compile(
    r"(?:Synthesize and Plan|Drafting the Response|Core Conflict|"
    r"^\s*I am\s+|^\s*#{1,6}\s|^\s*\d+\.\s+\*\*)",
    re.IGNORECASE | re.MULTILINE,
)


def safe_fallback(char_name: str) -> str:
    """두 번째 검사 실패 때 표시할 캐릭터 전용 최소 응답."""
    return (
        f"*{char_name}는 하던 일을 멈추고 잠시 시선을 내렸다. 쉽게 꺼낼 수 없는 말을 "
        "고르는 듯 입술을 다물었다. 방 안에는 작은 생활 소리만 남았고, 그 짧은 "
        "침묵을 억지로 메우지는 않았다.*\n\n"
        '"잠깐만."\n\n'
        f"*{char_name}는 천천히 숨을 고른 뒤 자세를 바로잡았다. 서둘러 결론을 내리기보다 "
        "지금 해야 할 말부터 찾으려는 표정이었다. 손끝으로 가까운 물건의 모서리를 한 번 "
        "쓸고는 조용히 손을 거두었다.*\n\n"
        '"어떻게 말해야 할지 생각 중이야."\n\n'
        f"*{char_name}는 더 둘러대지 않고 고개를 들었다. 아직 정리되지 않은 마음까지 아는 "
        "척할 수는 없었다. 그래서 이번에는 침묵 뒤에 숨지 않고 다음 말을 기다렸다.*\n\n"
        '"조금만 더 말해 줘."'
    )


def validate(text: str, user_name: str, impersonation: bool) -> list[str]:
    errors: list[str] = []
    stripped = text.lstrip()
    if not stripped.startswith("*"):
        errors.append("action 문단으로 시작하지 않음")
    ratio = korean_ratio(text)
    if ratio < 0.5:
        errors.append(f"한국어 비율 {ratio:.2f}")
    if not 300 <= len(text) <= 2200:
        errors.append(f"응답 길이 {len(text)}자")
    if META.search(text):
        errors.append("분석·계획 또는 meta 출력")
    impersonation_result = validate_impersonation(text, user_name, impersonation)
    errors.extend(impersonation_result.errors)
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    cases = {row["id"]: row for row in SUITE["cases"]}
    total = blocked = 0
    for path in args.files:
        for row in json.loads(path.read_text("utf-8")):
            case = cases[row["test_id"]]
            user_name = FIXTURES["chars"][case["char"]]["user_name"]
            errors = validate(row["result"]["text"], user_name, row["impersonation"])
            total += 1
            blocked += bool(errors)
            print(f"{'BLOCK' if errors else 'PASS'} {path.name} {row['test_id']}"
                  + (f" — {'; '.join(errors)}" if errors else ""))
    print(f"\n검사 {total}건 / 표시 허용 {total - blocked}건 / 차단 {blocked}건")


if __name__ == "__main__":
    main()
