"""사칭 모드 응답의 사용자 발화·행동을 출력 전에 검사한다.

ON의 선택적 사용자 발화는 <user_voice> 하나로 식별하고 표시 직전에 wrapper를 제거한다.
OFF의 marker, 이름표 대사, 사용자를 주어로 새 행동·표정·반응을 만든 문장은 차단한다.
검사 실패 시 운영 경로는 1회 재생성하고, 다시 실패하면 캐릭터만 행동하는 짧은
fallback으로 턴을 넘긴다. 이 모듈은 검출과 wrapper 제거만 담당한다.

usage:
  .venv/bin/python scripts/impersonation_guard.py tests/results/live_....json [...]
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

VOICE = re.compile(r"<user_voice>\s*(\"[^\"]+\")\s*</user_voice>", re.DOTALL)
ANY_VOICE_TAG = re.compile(r"</?user_voice>")
ACTION = (
    r"말|대답|묻|중얼|웃|울|앉|서|일어나|움직|다가|다가오|내려놓|올려놓|꺼내|"
    r"열|닫|마시|먹|바라|쳐다|끄덕|멈칫|숨|뒤적|잡|쥐|내밀|건네|걷|들어오|"
    r"나가|표정|시선|목소리|소리|반응|입술|손가락|어깨"
)

# 주어가 직전 문장에 생략된 채 사용자의 자세를 새로 만드는 표현도 보수적으로 막는다.
# 예: "오늘은 굳이 카운터 앞에 서 있는 모습이 낯설었다."
IMPLICIT_USER_STATE = re.compile(
    r"(?:창가|카운터|문(?:가|앞)|내\s*옆|그\s*자리)[^.!?\n\"]{0,45}"
    r"(?:앉아|서|누워|웅크려|기대어)\s*있(?:는|던|을|었)"
)


@dataclass
class GuardResult:
    ok: bool
    errors: list[str]
    display_text: str


def validate(text: str, user_name: str, impersonation: bool) -> GuardResult:
    errors: list[str] = []
    voices = VOICE.findall(text)
    raw_tags = ANY_VOICE_TAG.findall(text)
    if len(raw_tags) != len(voices) * 2:
        errors.append("user_voice marker가 짝을 이루지 않음")
    if impersonation:
        if len(voices) > 1:
            errors.append(f"사용자 발화 {len(voices)}회")
    elif voices or raw_tags:
        errors.append("OFF 모드의 사용자 발화 marker")

    if re.search(rf"(?m)^\s*{re.escape(user_name)}\s*:", text):
        errors.append("사용자 이름표 대사")

    # 사용자/2인칭이 문법적 주어이거나 사용자 신체·소지품이 새 상태를 만드는 경우.
    subject = (
        rf"(?:{re.escape(user_name)}(?:은|는|이|가)|너(?:는|가)|네가|"
        rf"네\s*(?:손|시선|표정|목소리|가방|발|어깨|입술|눈|손가락))"
    )
    action_pattern = re.compile(subject + rf"[^.!?\n\"]{{0,70}}(?:{ACTION})")
    # 허용된 user_voice 본문은 검사에서 가린다. 발화 외 사용자 행동은 그대로 잡힌다.
    masked = VOICE.sub("<allowed_user_voice>", text)
    action_hits = [m.group(0)[:100] for m in action_pattern.finditer(masked)]
    action_hits.extend(m.group(0)[:100] for m in IMPLICIT_USER_STATE.finditer(masked))
    if action_hits:
        errors.append("사용자 행동·반응 생성: " + " / ".join(action_hits[:3]))

    display = VOICE.sub(lambda match: match.group(1), text)
    return GuardResult(not errors, errors, display)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    total = caught = 0
    for path in args.files:
        for row in json.loads(path.read_text("utf-8")):
            if row["test_id"] not in {"IMP-01", "IMP-02"}:
                continue
            result = validate(row["result"]["text"], "하람", row["impersonation"])
            total += 1
            caught += not result.ok
            print(f"{'PASS' if result.ok else 'BLOCK'} {path.name} {row['test_id']}"
                  + (f" — {'; '.join(result.errors)}" if result.errors else ""))
    print(f"\n검사 {total}건 / 차단 {caught}건")


if __name__ == "__main__":
    main()
