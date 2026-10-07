"""사용자 행동 대리 2단계 판정.

1단계 정규식(impersonation_guard.user_action_matches)은 "하람이 + 동사"를 넓게 잡아
3인칭 서술에서 오탐이 많다. 플레이그라운드 응답에서 정규식이 고른 110문장에 사람이
라벨을 붙여 보니 실제 위반은 10개였고(tests/cases/user_action_judge.json), 그중 8개가
사용자가 쓰지 않은 자세·위치("창가에 앉아 있는 하람", "하람이 앉은 의자")였다.

2단계는 이 유형만 결정적 규칙으로 잡는다. 사용자가 *행동 서술*에 앉거나 섰다고 쓴 적이
없는데 응답이 사용자의 지금 자세를 단정하면 위반이다. 같은 110문장에서 LLM 판정
(Flash-Lite, Flash)은 정밀도·재현율이 이 규칙보다 낮았다(scripts/evaluate_user_action_judge.py).
나머지 의심 문장은 차단하지 않고 표시만 한다.
"""
from __future__ import annotations

import re

from impersonation_guard import user_action_matches

# 문장 경계: 마침표류 뒤 공백, 줄바꿈, 서술(*)과 대사(") 사이
_SENT_END = re.compile(r'(?<=[.!?…])\s+|\n+|(?<=\*)\s*(?=")|(?<=")\s*(?=\*)')
_POSTURE = r"(?:(?<!가라)앉|서\s*있|누워|누운|기대)"   # "가라앉다"의 앉은 자세가 아니다
# 습관("늘 앉던")과 기대("~하기를 기다리며 서 있었다"의 서 있는 쪽은 캐릭터)는 사용자의 지금 자세가 아니다.
_NOT_NOW = re.compile(r"앉던|앉는|서\s*있던|기대던|늘|평소|자주|으레|매번|매일|기를")
_HABIT_BEFORE = re.compile(r"(?:늘|평소|자주|으레|매번|매일같이|매일)\s*$")


def sentence_at(text: str, pos: int) -> str:
    starts = [0] + [m.end() for m in _SENT_END.finditer(text)]
    start = max(s for s in starts if s <= pos)
    ends = [m.start() for m in _SENT_END.finditer(text) if m.start() > pos]
    return text[start:(ends[0] if ends else len(text))].strip(" *")


def flagged_sentences(text: str, user_name: str) -> list[str]:
    """1단계 정규식이 사용자 행동으로 의심한 문장. 같은 문장은 한 번만 넣는다."""
    out: list[str] = []
    for m in user_action_matches(text, user_name):
        sent = sentence_at(m.string, m.start())
        if sent and sent not in out:
            out.append(sent)
    return out


def _posture_pattern(user_name: str) -> re.Pattern[str]:
    u = re.escape(user_name)
    return re.compile(
        rf"(?:{u}|그)(?:이|가|은|는)\s*(?:[^.!?\n\"]{{0,25}}?)?{_POSTURE}"          # 하람이 … 앉
        rf"|{_POSTURE}[^.!?\n\" ]*\s*(?:있는\s*)?(?:{u}|그(?:의|는|가)?)(?=[\s.,의이가은는을를]|$)")  # 앉아있는 하람


def invents_posture(sentence: str, user_name: str, recent_user_messages: list[str]) -> bool:
    """사용자가 행동 서술(*…*)에 쓰지 않은 지금 자세를 문장이 단정하면 True."""
    said = " ".join(" ".join(re.findall(r"\*([^*]+)\*", m)) for m in recent_user_messages)
    if re.search(_POSTURE, said):
        return False
    for m in _posture_pattern(user_name).finditer(sentence):
        before = sentence[max(0, m.start() - 8):m.start()]
        if not _NOT_NOW.search(m.group(0)) and not _HABIT_BEFORE.search(before):
            return True
    return False


def posture_violations(text: str, user_name: str, recent_user_messages: list[str]) -> list[str]:
    """의심 문장 중 2단계 규칙으로 위반이 확정된 문장."""
    return [s for s in flagged_sentences(text, user_name)
            if invents_posture(s, user_name, recent_user_messages)]
