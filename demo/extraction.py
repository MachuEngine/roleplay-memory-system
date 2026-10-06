"""L1·L2 memory 추출 호출과 응답 파싱.

호출 형식은 scripts/evaluate_extraction_quality.py와 같고, 지시문은 형식 예시만 자리표시자로 바꾼 변형이다.
<profile>의 "- 항목: 값"은 Fact로, <episodes>의 "- 내용"은 Episode로 바꿔
MemoryStore.run_extraction()에 넘긴다. 호출이나 파싱이 실패하면 예외를 던지고,
run_extraction()이 큐를 보존해 다음 차례에 다시 시도한다.
"""
from __future__ import annotations

import re

from extraction_prompt import INSTRUCTION
from memory_sim import Episode, Fact, Turn

EXTRACT_MAX_TOKENS = 800

# 측정에 쓴 지시문은 형식 예시에 실제 내용(호칭: 선배, 시집)을 담고 있어, 입력이 빈약하면
# Flash-Lite가 예시를 그대로 기억으로 복사했다(플레이그라운드 스모크 테스트). 플레이그라운드는 예시만 자리표시자로
# 바꾼 변형을 쓴다. 공용 지시문이 바뀌면 아래 치환이 실패해 바로 드러난다.
# 20턴 테스트에서는 기존 기억을 통째로 다시 적다가 출력 상한에 걸려 새 항목이 사라졌다.
# MemoryStore는 추출 결과를 변경분으로 받아 합치므로, 새로 생기거나 바뀐 항목만 쓰게 한다.
_EXAMPLE = ("<profile>\n- 호칭: 선배\n</profile>\n<episodes>\n- 시집을 찾아 건넸고 사용자가 받았다\n"
            "</episodes>")
_EXAMPLE_NOTE = "위 내용은 형식 예시일 뿐이며 입력에 없는 선배·시집 정보를 복사하지 않는다."
assert _EXAMPLE in INSTRUCTION and _EXAMPLE_NOTE in INSTRUCTION
DEMO_INSTRUCTION = INSTRUCTION.replace(
    _EXAMPLE, "<profile>\n- 항목명: 현재값\n</profile>\n<episodes>\n- 상태를 바꾼 사건과 결과\n</episodes>"
).replace(_EXAMPLE_NOTE, "위 블록의 '항목명: 현재값', '상태를 바꾼 사건과 결과'는 자리표시자이며 그대로 출력하지 않는다.") + """
<existing_memory>에 이미 있는 항목은 다시 쓰지 않는다. 새로 생기거나 값이 바뀐 항목만 쓴다.
사용자가 직접 밝힌 사실(생일, 일정, 약속, 계획)은 profile에 '항목명: 현재값'으로 쓴다.
episodes는 대사를 인용하지 않고, 무엇이 일어나 어떻게 끝났는지 한 문장으로 요약한다."""
# 자리표시자나 원래 예시가 그대로 나오면 저장하지 않는다.
_ECHOES = ("항목명", "현재값", "상태를 바꾼 사건과 결과", "호칭: 선배", "시집을 찾아 건넸고")
# "확정된 관계: 없음"처럼 지시문의 분류명을 빈 값으로 채운 항목도 저장하지 않는다.
_EMPTY_VALUES = {"없음", "없다", "해당 없음", "미정", "-"}
_BLOCK = r"<{tag}>(.*?)</{tag}>"


class ExtractionError(RuntimeError):
    pass


def build_request(existing_memory: str, turns: list[Turn]) -> str:
    transcript = "\n".join(f"{t.speaker}: {t.text}" for t in turns)
    return ("<existing_memory>\n" + existing_memory + "\n</existing_memory>\n"
            "<transcript>\n" + transcript + "\n</transcript>")


def _items(text: str, tag: str) -> list[str] | None:
    m = re.search(_BLOCK.format(tag=tag), text, re.DOTALL)
    if m is None:
        return None
    return [line.strip()[2:].strip() for line in m.group(1).splitlines()
            if line.strip().startswith("- ") and line.strip()[2:].strip()]


def parse(text: str, turns: list[Turn]) -> tuple[list[Fact], list[Episode]]:
    profile, episodes = _items(text, "profile"), _items(text, "episodes")
    if profile is None or episodes is None:
        # 출력이 잘리면 닫는 태그가 빠진다. 한쪽만 읽고 성공 처리하면 대기 턴이 지워지므로 실패로 본다.
        raise ExtractionError("추출 응답에 <profile>과 <episodes> 블록이 모두 있지 않음")
    idxs = [t.idx for t in turns]
    last = max(idxs)
    facts = []
    for item in profile or []:
        if any(echo in item for echo in _ECHOES):
            continue
        subject, sep, value = item.partition(":")
        if not sep:
            facts.append(Fact(item, "확정", last, list(idxs)))
        elif subject.strip() and value.strip(":").strip().rstrip(".") not in _EMPTY_VALUES | {""}:
            facts.append(Fact(subject.strip(), value.strip(":").strip(), last, list(idxs)))
        # "항목:"처럼 값이 빈 항목은 저장할 사실이 없으므로 버린다.
    eps = [Episode(item, last, 0.5, list(idxs)) for item in episodes or []
           if not any(echo in item for echo in _ECHOES)]
    return facts, eps


def make_extractor(client, existing_memory: str):
    """MemoryStore.run_extraction()에 넘길 추출 함수를 만든다."""
    def extract(turns: list[Turn]) -> tuple[list[Fact], list[Episode]]:
        result = client.complete(DEMO_INSTRUCTION, build_request(existing_memory, turns),
                                 max_tokens=EXTRACT_MAX_TOKENS, reasoning_max_tokens=0,
                                 temperature=0.0, seed=None)
        if not result.ok:
            raise ExtractionError(result.error or "추출 호출 실패")
        if result.finish_reason == "length":
            raise ExtractionError("추출 출력이 상한에서 잘림")
        return parse(result.text, turns)
    return extract
