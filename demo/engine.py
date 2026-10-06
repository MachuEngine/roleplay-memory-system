"""플레이그라운드 대화 엔진. UI와 분리해 키 없이 테스트할 수 있게 한다.

턴 하나의 처리 순서
  상한 확인 → 사용자 발화 저장 → chat_history 조립(6,000토큰 추정 이내 + 미추출 턴)
  → L1·L2 주입 → 렌더링 → 호출 → response guard(실패 시 1회 재생성, 다시 실패하면
  대체 응답) → AI 응답 저장
memory 추출은 미추출 대화가 MEMORY_BATCH회 쌓이면 응답을 보여 준 뒤 따로 실행한다.
"""
from __future__ import annotations

import html
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parent
for p in (ROOT / "scripts", DEMO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from extraction import DEMO_INSTRUCTION, make_extractor  # noqa: E402
from limits import DailyCounter, check_turn  # noqa: E402
from llm_client import ChatResult  # noqa: E402
from memory_sim import MemoryStore, Scope, est_tokens  # noqa: E402
from prompt_render import render_system, unresolved  # noqa: E402
from response_guard import safe_fallback, validate  # noqa: E402

TEMPLATE = DEMO / "prompts" / "system.hbs"
MAIN_MAX_TOKENS = 1200
THINKING_BUDGET = 128
HISTORY_BUDGET = 6000          # chat_history 토큰 추정 상한 (docs/03-assumptions.md)
SETTING_BUDGET = 6000          # 캐릭터 + 페르소나 입력 상한
MEMORY_BATCH = 6               # 미추출 대화 6회(사용자 1턴 + AI 1턴)마다 추출
NAME_MAX, MESSAGE_MAX = 20, 500
# 규칙 기반 사용자 행동 판정은 3인칭 서술에서 오탐이 많다("하람이 던진 질문에" 같은 수식어,
# 캐릭터의 기대 표현). 플레이그라운드에서는 이 판정만 막지 않고 의심 문장으로 표시한다.
WARN_ONLY_PREFIX = "사용자 행동·반응 생성"


@dataclass
class Character:
    name: str
    identity: str
    initial_state: str = ""
    speech: str = ""
    examples: str = ""


@dataclass
class Persona:
    name: str
    description: str = ""


@dataclass
class TurnRecord:
    """턴 하나의 검사 결과. UI의 검사 패널과 스모크 테스트 기록에 쓴다."""
    status: str                          # 통과 / 재생성 후 통과 / 대체 응답 / 호출 실패 / 차단
    errors: list[list[str]] = field(default_factory=list)   # 시도별 차단 사유
    warnings: list[str] = field(default_factory=list)       # 표시한 응답의 의심 문장
    calls: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)   # guard가 막은 응답 원문 (분석용)


@dataclass
class Session:
    character: Character
    persona: Persona
    store: MemoryStore = field(default_factory=MemoryStore)
    scope: Scope = field(default_factory=lambda: Scope("demo", "viewer", uuid.uuid4().hex))
    turns_used: int = 0
    records: list[TurnRecord] = field(default_factory=list)
    extraction_runs: list[dict[str, Any]] = field(default_factory=list)


def setup_error(character: Character, persona: Persona) -> str | None:
    """대화를 시작할 수 없는 입력이면 이유를 돌려준다."""
    for label, name in (("캐릭터 이름", character.name), ("사용자 이름", persona.name)):
        if not name.strip():
            return f"{label}을 입력해 주세요."
        if len(name.strip()) > NAME_MAX:
            return f"{label}은 {NAME_MAX}자 이하로 입력해 주세요."
    if character.name.strip() == persona.name.strip():
        return "캐릭터와 사용자 이름은 서로 달라야 합니다."
    if not character.identity.strip():
        return "고정 정체성을 입력해 주세요."
    used = est_tokens("\n".join([character.identity, character.initial_state, character.speech,
                                 character.examples, persona.description]))
    if used > SETTING_BUDGET:
        return f"설정이 너무 깁니다(추정 {used:,}토큰, 상한 {SETTING_BUDGET:,}토큰)."
    return None


def new_session(character: Character, persona: Persona) -> Session:
    clean = lambda s: s.strip()  # noqa: E731
    return Session(
        Character(clean(character.name), clean(character.identity), clean(character.initial_state),
                  clean(character.speech), clean(character.examples)),
        Persona(clean(persona.name), clean(persona.description)),
    )


def chat_history(session: Session) -> str:
    """최근 턴부터 HISTORY_BUDGET 안에서 담는다. 미추출 턴은 예산을 넘어도 뺄 수 없다.

    memory에 반영되기 전 원문을 버리면 그 사이의 사건이 사라지므로, 추출이 끝나기
    전까지는 원문으로 제공한다(docs/03-assumptions.md memory_trigger의 불변 조건).
    """
    pending = {t.idx for t in session.store.pending_turns(session.scope)}
    turns = [t for t in session.store.turns.get(session.scope, []) if not t.deleted]
    picked, used = [], 0
    for t in reversed(turns):
        line = f"{t.speaker}: {t.text}"
        n = est_tokens(line)
        if used + n > HISTORY_BUDGET and t.idx not in pending:
            break
        picked.append(line)
        used += n
    return "\n".join(reversed(picked))


def build_system(session: Session) -> str:
    c, p = session.character, session.persona
    ctx = {
        "char_name": c.name, "user_name": p.name,
        "char_identity": c.identity, "char_initial_state": c.initial_state,
        "char_speech": c.speech, "style_examples": c.examples,
        "user_description": p.description,
        "char_keywordbook": session.store.build_keywordbook(session.scope, paid=False),
        "chat_history": chat_history(session),
    }
    system = render_system(ctx, TEMPLATE)
    left = unresolved(system)
    if left:
        raise RuntimeError(f"미치환 플레이스홀더: {left}")
    return system


def _call_record(res: ChatResult) -> dict[str, Any]:
    return {"ok": res.ok, "error": res.error, "tokens_in": res.tokens_in,
            "tokens_out": res.tokens_out, "tokens_reasoning": res.tokens_reasoning,
            "cost_usd": res.cost_usd_api, "latency_s": res.latency_s}


def respond(session: Session, message: str, client, daily: DailyCounter) -> tuple[str, TurnRecord]:
    """사용자 발화 하나를 처리하고 (표시할 응답, 검사 기록)을 돌려준다."""
    message = message.strip()
    if not message:
        return "", TurnRecord("차단", [["빈 메시지"]])
    if len(message) > MESSAGE_MAX:
        return f"메시지는 {MESSAGE_MAX}자 이하로 입력해 주세요.", TurnRecord("차단", [["메시지 길이"]])
    blocked = check_turn(session.turns_used, daily)
    if blocked:
        return blocked, TurnRecord("차단", [[blocked]])

    c, p = session.character, session.persona
    system = build_system(session)            # 현재 발화는 transcript가 아니라 user 메시지로 보낸다
    user_turn = session.store.append_turn(session.scope, p.name, message)
    record = TurnRecord("통과")
    text = ""
    for attempt in range(2):
        res = client.complete(system, message, max_tokens=MAIN_MAX_TOKENS,
                              reasoning_max_tokens=THINKING_BUDGET, temperature=1.0, seed=None)
        record.calls.append(_call_record(res))
        if not res.ok:
            session.store.delete_turn(session.scope, user_turn.idx)
            record.status = "호출 실패"
            session.records.append(record)
            return "응답을 받지 못했습니다. 잠시 후 다시 보내 주세요.", record
        candidate = html.unescape(res.text).strip()
        found = validate(candidate, p.name, impersonation=False)
        errors = [e for e in found if not e.startswith(WARN_ONLY_PREFIX)]
        record.errors.append(errors)
        if not errors:
            text = candidate
            record.warnings = [e for e in found if e.startswith(WARN_ONLY_PREFIX)]
            record.status = "통과" if attempt == 0 else "재생성 후 통과"
            break
        record.rejected.append(candidate)
    if not text:
        text = safe_fallback(c.name)
        record.status = "대체 응답"

    session.store.append_turn(session.scope, c.name, text)
    session.turns_used += 1
    session.records.append(record)
    return text, record


def extraction_due(session: Session) -> bool:
    return len(session.store.pending_turns(session.scope)) >= MEMORY_BATCH * 2


def run_extraction(session: Session, client) -> bool:
    """대기 중인 턴에서 L1·L2를 추출한다. 실패하면 큐가 남아 다음 차례에 다시 시도한다."""
    # keywordbook은 프롬프트 주입용으로 escape되어 있다. 추출 모델에는 원문으로 보낸다.
    existing = html.unescape(session.store.build_keywordbook(session.scope, paid=False))
    n = len(session.store.pending_turns(session.scope))
    ok = session.store.run_extraction(session.scope, make_extractor(client, existing))
    session.extraction_runs.append({"ok": ok, "turns": n})
    return ok


def memory_view(session: Session) -> tuple[list[str], list[str]]:
    """UI에 보여 줄 현재 L1 profile과 L2 episodes."""
    facts = sorted(session.store.facts.get(session.scope.profile_key(), {}).values(),
                   key=lambda f: f.turn)
    eps = [e for e in session.store.episodes.get(session.scope, []) if not e.invalidated]
    return [f"{f.subject}: {f.value}" for f in facts], [e.text for e in eps]


class FakeClient:
    """키 없이 화면과 흐름을 확인하는 고정 응답 클라이언트 (DEMO_FAKE_LLM=1, 테스트)."""

    def __init__(self, replies: list[str] | None = None, extraction: str | None = None,
                 fail: bool = False) -> None:
        self.replies = list(replies or [])
        self.extraction = extraction
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, **_: Any) -> ChatResult:
        self.calls.append((system, user))
        if self.fail:
            return ChatResult(ok=False, error="fake failure")
        if system == DEMO_INSTRUCTION:
            text = self.extraction if self.extraction is not None else (
                "<profile>\n- 호칭: 상대를 '너'라고 부른다\n</profile>\n"
                "<episodes>\n- 비 오는 오후에 둘이 책방에서 이야기를 나눴다\n</episodes>")
            return ChatResult(ok=True, text=text)
        if self.replies:
            return ChatResult(ok=True, text=self.replies.pop(0))
        name = re.search(r"Play (.+?) in an immersive scene", system)
        char = name.group(1) if name else "캐릭터"
        return ChatResult(ok=True, text=_fake_scene(char))


def _fake_scene(char: str) -> str:
    return (
        f"*{char}는 읽던 책을 덮고 창밖을 잠시 바라보았다. 유리창에 맺힌 빗방울이 천천히 "
        "흘러내리고 있었다. 가게 안에는 오래된 종이 냄새가 낮게 깔려 있었다.*\n\n"
        "\"왔어.\"\n\n"
        f"*{char}는 카운터 아래에서 작은 주전자를 꺼내 물을 올렸다. 손놀림은 무심했지만 "
        "익숙한 순서를 하나도 건너뛰지 않았다. 물이 끓는 동안 책등을 손끝으로 한 번 훑었다.*\n\n"
        "\"오늘은 손님 없어. 비 와서.\"\n\n"
        f"*{char}는 창가 의자를 조금 당겨 놓고 다시 자리에 앉았다. 더 묻지 않고, 대답을 "
        "재촉하지도 않았다. 빗소리만 가게 안을 채웠고, 주전자가 낮게 끓기 시작했다.*\n\n"
        "\"……앉든지.\""
    )
