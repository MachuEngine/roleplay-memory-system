"""L1·L2 memory 추출: 사실 추출과 사건 요약을 두 호출로 나눈다.

처음에는 한 호출(scripts/extraction_prompt.py 지시문 변형)이 L1·L2를 함께 뽑았다. 정답 세트
(tests/cases/memory_gold.json)로 재 보니 temperature 0에서 3회 반복 결과가 같아 편차가 아니라
체계적 오류였고, 원인은 작업 설계였다.
  - 입력 대부분이 캐릭터의 긴 서술이라 사용자의 짧은 발화(생일, 선호)가 묻혀 누락됐다
  - 캐릭터 발언·설정에서 사실을 만들어 기존 값을 덮어쓰거나 설정을 복사했다
  - L1·L2를 한꺼번에 길게 쓰다 출력 상한에서 잘렸다

그래서
  1) 사실 추출: 사용자 발화 전문과 캐릭터 대사만 넣고, JSON으로 사실과 '근거 사용자 발화'를 받는다.
     근거가 실제 사용자 발화에 없으면 버린다. 사용자가 말하지 않은 사실은 L1에 들어갈 수 없다.
  2) 사건 요약: 전체 대화에서 상태를 바꾼 사건 최대 4개를 짧은 문장으로 받는다.
기존 L1 값은 더 최근 사용자 발화에 근거가 있을 때만 바뀐다. 호출이나 파싱이 실패하면 예외를
던지고, MemoryStore.run_extraction()이 대기 턴을 보존해 다음 차례에 다시 시도한다.
"""
from __future__ import annotations

import json
import re

from memory_sim import Episode, Fact, Turn

FACT_MAX_TOKENS = 600
EPISODE_MAX_TOKENS = 400
SPEECH_MAX_CHARS = 300       # 캐릭터 줄은 대사만, 이 길이까지

FACT_INSTRUCTION = """롤플레이 대화에서 사용자가 직접 밝힌 사실만 뽑는다.
뽑을 것: 사용자의 생일·일정·계획, 사용자와 캐릭터의 약속, 사용자나 사용자 가족의 선호·사정, 호칭이나 관계의 변화.
뽑지 않을 것: 캐릭터의 행동·감정·서술, 캐릭터 설정, 사용자가 묻기만 한 질문, <existing_profile>과 같은 내용.
사용자가 나중에 취소하거나 농담이라고 한 내용은 status를 "취소"로 둔다.

항목명 규칙: 생일은 '생일(누구)', 약속은 '약속(대상)', 일정·계획은 '일정(대상)', 선호는 '선호(누구)'.
<existing_profile>에 같은 사실이 있으면 같은 항목명을 쓴다.
약속은 value 끝에 캐릭터의 반응을 괄호로 붙인다. 예: "마감 후 같이 가자고 함 (캐릭터는 확답을 피함)"
evidence에는 근거가 된 사용자 줄의 문장을 그대로 복사한다. 근거가 없으면 그 사실을 쓰지 않는다.

JSON 하나만 출력한다.
{"facts": [{"subject": "항목명", "value": "현재값", "evidence": "사용자 발화 원문", "status": "확정"}]}
새 사실이 없으면 {"facts": []}"""

EPISODE_INSTRUCTION = """롤플레이 대화 구간에서 이후 관계나 상황을 바꾼 사건을 최대 4개 고른다.
각 사건은 한국어 한 문장(60자 이내)으로, 누가 무엇을 해서 어떻게 끝났는지 쓴다.
대사를 인용하지 않고, 대화에 없는 날짜·시간을 붙이지 않는다. 장식 묘사나 사소한 동작은 쓰지 않는다.
JSON 하나만 출력한다. {"episodes": ["사건 문장"]}"""

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

# 분류 이름이거나 설정이 정하는 말투는 기억 항목이 아니다.
_CATEGORY_SUBJECTS = {"확정된 관계", "사용자가 직접 밝힌 사실", "관계", "말투", "사실"}
_EMPTY_VALUES = {"없음", "없다", "해당 없음", "미정", "-"}
# 생일·생신 값에는 날짜 표현이 있어야 한다("생일: 보리차 티백" 같은 잘못된 갱신 차단).
# "생일 축하"의 '일'이 걸리지 않도록 실제 날짜 표현만 본다.
_DATE = re.compile(r"[월화수목금토일]요일|\d+\s*[월일]|[이다]번\s*주|다음\s*주|이번\s*달|다음\s*달|"
                   r"내일|모레|오늘|주말|\d")


class ExtractionError(RuntimeError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[\s.,·!?~…'\"“”‘’*]", "", s)


def canonical_subject(subject: str) -> str:
    """'하람의 생일', '하람과의 약속'을 '생일(하람)', '약속(하람)'으로 맞춰 같은 항목이 두 번 쌓이지 않게 한다."""
    subject = subject.strip()
    m = re.fullmatch(r"(.+?)(?:과의|와의|의)\s*(\S+)", subject)
    return f"{m.group(2)}({m.group(1)})" if m and "(" not in subject else subject


def _speech(text: str) -> str:
    lines = re.findall(r'"([^"]+)"', text)
    return " / ".join(lines)[:SPEECH_MAX_CHARS]


def build_fact_request(user_name: str, turns: list[Turn], existing: dict[str, Fact]) -> str:
    profile = "\n".join(f"- {f.subject}: {f.value}" for f in existing.values()) or "(없음)"
    header = f"사용자 이름: {user_name} (항목명에는 '사용자' 대신 이 이름을 쓴다)\n"
    lines = [f"[사용자 {t.speaker}] {t.text}" if t.speaker == user_name
             else f"[캐릭터 {t.speaker} 대사] {_speech(t.text)}" for t in turns]
    return (header + f"<existing_profile>\n{profile}\n</existing_profile>\n"
            "<transcript>\n" + "\n".join(lines) + "\n</transcript>")


def build_episode_request(turns: list[Turn]) -> str:
    return "<transcript>\n" + "\n".join(f"{t.speaker}: {t.text}" for t in turns) + "\n</transcript>"


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m is None:
        raise ExtractionError("JSON 객체가 없음")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionError("JSON 객체가 아님")
    return data


def parse_facts(text: str, turns: list[Turn], user_name: str,
                existing: dict[str, Fact]) -> list[Fact]:
    """근거가 실제 사용자 발화에 있는 사실만 남긴다."""
    raw = _json(text).get("facts")
    if not isinstance(raw, list):
        raise ExtractionError("facts 목록이 없음")
    user_turns = [t for t in turns if t.speaker == user_name]
    out: list[Fact] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        subject = canonical_subject(str(item.get("subject", "")))
        value = str(item.get("value", "")).strip()
        evidence = _norm(str(item.get("evidence", "")))
        if (not subject or subject in _CATEGORY_SUBJECTS or subject.startswith("말투")
                or value.rstrip(".") in _EMPTY_VALUES | {""} or len(evidence) < 4):
            continue
        source = next((t for t in reversed(user_turns) if evidence in _norm(t.text)), None)
        if source is None:
            continue                                   # 사용자가 말하지 않은 사실
        if re.match(r"생일|생신", subject) and not _DATE.search(value):
            continue
        if str(item.get("status", "")).strip() == "취소":
            value = f"{value} (취소됨)"
        prev = existing.get(subject)
        if prev is not None and (_norm(prev.value) == _norm(value) or prev.turn >= source.idx):
            continue                                   # 같은 값이거나 더 오래된 근거
        if prev is not None and _DATE.search(prev.value) and not _DATE.search(value):
            continue                                   # 날짜 정보를 잃는 갱신 ("생일: 축하를 받음")
        same = next((f for f in out if f.subject == subject), None)
        if same is not None:                           # 한 번에 같은 항목이 여러 개면 값을 합친다
            same.value = f"{same.value}, {value}"
            same.turn = max(same.turn, source.idx)
            same.source_turns = sorted({*same.source_turns, source.idx})
            continue
        out.append(Fact(subject, value, source.idx, [source.idx]))
    return out


def parse_episodes(text: str, turns: list[Turn]) -> list[Episode]:
    raw = _json(text).get("episodes")
    if not isinstance(raw, list):
        raise ExtractionError("episodes 목록이 없음")
    idxs = [t.idx for t in turns]
    items = [re.sub(r"^[-*\s]*\d*[.)]?\s*", "", str(x)).strip() for x in raw]
    return [Episode(x, max(idxs), 0.5, list(idxs)) for x in items[:4] if x]


def make_extractor(client, user_name: str, existing: dict[str, Fact]):
    """MemoryStore.run_extraction()에 넘길 추출 함수를 만든다."""
    def ask(system: str, user: str, max_tokens: int) -> str:
        res = client.complete(system, user, max_tokens=max_tokens, reasoning_max_tokens=0,
                              temperature=0.0, seed=None)
        if not res.ok:
            raise ExtractionError(res.error or "추출 호출 실패")
        if res.finish_reason == "length":
            raise ExtractionError("추출 출력이 상한에서 잘림")
        return res.text

    def extract(turns: list[Turn]) -> tuple[list[Fact], list[Episode]]:
        facts = parse_facts(ask(FACT_INSTRUCTION, build_fact_request(user_name, turns, existing),
                                FACT_MAX_TOKENS), turns, user_name, existing)
        episodes = parse_episodes(ask(EPISODE_INSTRUCTION, build_episode_request(turns),
                                      EPISODE_MAX_TOKENS), turns)
        return facts, episodes
    return extract


def make_roller(client, known_subjects=lambda: set()):
    """롤업용 (promoter, summarizer). 호출이 실패하면 승격하지 않고 기본 요약으로 돌아간다.

    승격 결과는 memory_sim이 바로 L1에 저장하므로 사실 추출과 같은 검증을 여기서 한다.
    이미 있는 항목은 덮어쓰지 않는다(사건 한 줄에서 다시 뽑으면 값이 틀어졌다: "생일: 축하를 받음").
    """
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
        subject = canonical_subject(subject)
        if subject in known_subjects() or subject in _CATEGORY_SUBJECTS or subject.startswith("말투"):
            return None
        if re.match(r"생일|생신", subject) and not _DATE.search(value):
            return None
        return subject, value

    def summarizer(episodes: list[Episode]) -> str:
        out = ask(SUMMARY_INSTRUCTION, "\n".join(f"- {e.text}" for e in episodes), 200)
        if out and "<" not in out:
            line = re.sub(r"^[-*\s]*\d*[.)]?\s*", "", out.splitlines()[0]).strip()
            return line[:SUMMARY_MAX_CHARS]
        return "(요약) " + " / ".join(e.text for e in episodes)[:120]

    return promoter, summarizer
