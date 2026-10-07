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
<setting>에 이미 있는 캐릭터·사용자 설정은 쓰지 않는다.
사용자가 직접 밝힌 생일·일정·계획·약속은 episodes가 아니라 profile에 쓴다.
항목명은 내용을 나타내는 짧은 이름이다. 생일은 '생일(누구)', 약속은 '약속(대상)' 형식으로 쓴다.
예: '생일(하람): 다음 주 목요일', '약속(바다): 마감 후 같이 가자고 함 (캐릭터는 확답을 피함)'
'확정된 관계', '사용자가 직접 밝힌 사실' 같은 분류 이름을 항목명으로 쓰지 않는다.
화자는 <transcript> 각 줄 맨 앞의 '이름:'이다. 캐릭터 줄 안의 따옴표 대사는 캐릭터의 말이다.
질문에 답을 피했으면 답한 것으로 쓰지 않는다. 말했다가 취소한 내용은 취소된 상태로 쓴다.
episodes는 대사를 인용하지 않고, 무엇이 일어나 어떻게 끝났는지 한 문장으로 요약한다.
대화에 나오지 않은 날짜나 시간을 붙이지 않고, 항목 앞에 번호를 달지 않는다."""

# L2가 상한을 넘으면 오래된 3개를 접는다(memory_sim._rollup). 기본 구현은 이어 붙여 120자로 잘라
# 약속 같은 내용이 중간에서 끊겼다(20턴 테스트). 플레이그라운드는 접기 전에 지속 상태를 L1으로
# 올리고, 남은 사건은 한 문장으로 요약한다.
PROMOTE_INSTRUCTION = """롤플레이 기억 정리 단계다. 아래 사건이 지금도 유효한 상태를 남겼는지 판단한다.
유효한 상태: 아직 지켜지지 않은 약속, 사용자의 생일·일정·계획, 관계나 호칭의 변화.
있으면 '항목명: 현재값' 한 줄만, 없으면 '없음'만 출력한다.
항목명은 생일은 '생일(누구)', 약속은 '약속(대상)', 일정·계획은 '일정(대상)'처럼 짧게 쓴다.
날짜·요일이 사건에 있으면 현재값에 반드시 남긴다.
현재값에는 사건에 나온 내용만 쓰고, 상대가 답을 피했으면 그 반응도 괄호로 남긴다.
예: '생일(하람): 다음 주 목요일', '약속(바다): 마감 후 같이 가자고 함 (확답을 피함)'"""
SUMMARY_INSTRUCTION = """롤플레이 기억 정리 단계다. 아래 사건들을 한국어 한 문장(80자 이내)으로 요약한다.
사건에 나온 약속과 결과는 빠뜨리지 않는다. 사건에 없는 날짜나 시간은 붙이지 않는다.
번호나 기호 없이 요약 문장만 출력한다."""
SUMMARY_MAX_CHARS = 160
# 자리표시자나 원래 예시가 그대로 나오면 저장하지 않는다.
_ECHOES = ("항목명", "현재값", "상태를 바꾼 사건과 결과", "호칭: 선배", "시집을 찾아 건넸고")
# 지시문의 분류 이름이 항목명으로 나오면 내용을 가리키지 않으므로 저장하지 않는다.
_CATEGORY_SUBJECTS = {"확정된 관계", "사용자가 직접 밝힌 사실", "관계", "말투", "사실"}
# "확정된 관계: 없음"처럼 지시문의 분류명을 빈 값으로 채운 항목도 저장하지 않는다.
_EMPTY_VALUES = {"없음", "없다", "해당 없음", "미정", "-"}
_BLOCK = r"<{tag}>(.*?)</{tag}>"


class ExtractionError(RuntimeError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[\s.,·'\"]", "", s)


def canonical_subject(subject: str) -> str:
    """'하람의 생일', '하람과의 약속'을 '생일(하람)', '약속(하람)'으로 맞춰 같은 항목이 두 번 쌓이지 않게 한다."""
    subject = subject.strip()
    m = re.fullmatch(r"(.+?)(?:과의|와의|의)\s*(\S+)", subject)
    return f"{m.group(2)}({m.group(1)})" if m and "(" not in subject else subject


def build_request(existing_memory: str, turns: list[Turn], setting: str = "") -> str:
    transcript = "\n".join(f"{t.speaker}: {t.text}" for t in turns)
    return ("<setting>\n" + setting + "\n</setting>\n"
            "<existing_memory>\n" + existing_memory + "\n</existing_memory>\n"
            "<transcript>\n" + transcript + "\n</transcript>")


def _items(text: str, tag: str) -> list[str] | None:
    m = re.search(_BLOCK.format(tag=tag), text, re.DOTALL)
    if m is None:
        return None
    items = [line.strip()[2:].strip() for line in m.group(1).splitlines()
             if line.strip().startswith("- ") and line.strip()[2:].strip()]
    return [re.sub(r"^\d+[.)]\s*", "", item) for item in items]     # "1. …" 번호 제거


def parse(text: str, turns: list[Turn], setting: str = "") -> tuple[list[Fact], list[Episode]]:
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
        elif subject.strip() in _CATEGORY_SUBJECTS or subject.strip().startswith("말투"):
            continue    # 분류 이름이거나, 설정이 정하는 말투는 기억 항목이 아니다
        elif setting and _norm(value.strip(":").strip().rstrip(".")) in _norm(setting):
            continue    # 설정 문장을 그대로 옮긴 항목은 새 기억이 아니다
        elif subject.strip() and value.strip(":").strip().rstrip(".") not in _EMPTY_VALUES | {""}:
            facts.append(Fact(canonical_subject(subject), value.strip(":").strip(), last, list(idxs)))
        # "항목:"처럼 값이 빈 항목은 저장할 사실이 없으므로 버린다.
    eps = [Episode(item, last, 0.5, list(idxs)) for item in episodes or []
           if not any(echo in item for echo in _ECHOES)]
    return facts, eps


def make_extractor(client, existing_memory: str, setting: str = ""):
    """MemoryStore.run_extraction()에 넘길 추출 함수를 만든다."""
    def extract(turns: list[Turn]) -> tuple[list[Fact], list[Episode]]:
        result = client.complete(DEMO_INSTRUCTION, build_request(existing_memory, turns, setting),
                                 max_tokens=EXTRACT_MAX_TOKENS, reasoning_max_tokens=0,
                                 temperature=0.0, seed=None)
        if not result.ok:
            raise ExtractionError(result.error or "추출 호출 실패")
        if result.finish_reason == "length":
            raise ExtractionError("추출 출력이 상한에서 잘림")
        return parse(result.text, turns, setting)
    return extract


def make_roller(client):
    """롤업용 (promoter, summarizer). 호출이 실패하면 승격하지 않고 기본 요약으로 돌아간다."""
    def ask(system: str, user: str, max_tokens: int) -> str | None:
        res = client.complete(system, user, max_tokens=max_tokens, reasoning_max_tokens=0,
                              temperature=0.0, seed=None)
        if not res.ok or res.finish_reason == "length":
            return None
        return res.text.strip()

    def promoter(episode: Episode) -> tuple[str, str] | None:
        out = ask(PROMOTE_INSTRUCTION, episode.text, 80)
        line = (out or "").splitlines()[0].lstrip("- ").strip() if out else ""
        subject, sep, value = line.partition(":")
        value = value.strip()
        if not sep or not subject.strip() or value.rstrip(".") in _EMPTY_VALUES | {""}:
            return None
        return canonical_subject(subject), value

    def summarizer(episodes: list[Episode]) -> str:
        out = ask(SUMMARY_INSTRUCTION, "\n".join(f"- {e.text}" for e in episodes), 200)
        if out and "<" not in out:
            line = re.sub(r"^[-*\s]*\d*[.)]?\s*", "", out.splitlines()[0]).strip()
            return line[:SUMMARY_MAX_CHARS]
        return "(요약) " + " / ".join(e.text for e in episodes)[:120]

    return promoter, summarizer
