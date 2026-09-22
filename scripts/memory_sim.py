"""3계층 기억 보조 시스템의 인메모리 참조 구현.

문서(본문 3장 / docs/06-memory-architecture.md)가 서술한 흐름을 실행 가능한 형태로 옮긴 것이다.
운영 인프라(벡터 DB 등)는 만들지 않는다. 검색은 동일 인터페이스의 인메모리 구현으로 대체한다.

  대화 저장 → 비동기 요약·추출 → 구조화 기억 저장 → 검색 후보 생성
  → 관련성·중요도·최신성 선택 → 토큰 예산 압축 → char_keywordbook 주입

계층: L1 프로필 300 / L2 에피소드 600 / L3 리콜 300 (합계 1,200)
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Iterable

CHARS_PER_TOKEN_HANGUL, CHARS_PER_TOKEN_LATIN = 1.5, 4.0
HANGUL = re.compile(r"[가-힣ᄀ-ᇿ]")

BUDGET_L1, BUDGET_L2, BUDGET_L3 = 300, 600, 300
BUDGET_TOTAL = BUDGET_L1 + BUDGET_L2 + BUDGET_L3
L2_ITEM_CAP = 10
L2_ROLLUP_BATCH = 3           # 상한 도달 시 한 번에 접는 개수 (문서 3.2절)

# 지속 기억으로 승격하지 않는 임시 발언 표지 (문서 3.3 '저장하지 않는 것')
EPHEMERAL = re.compile(
    r"(오늘만|지금은|잠깐|방금|일단|아마|~?할까 말까|인 것 같기도|농담|취소할게|아까 한 말은)"
)


def est_tokens(text: str) -> int:
    h = len(HANGUL.findall(text))
    return round(h / CHARS_PER_TOKEN_HANGUL + (len(text) - h) / CHARS_PER_TOKEN_LATIN)


@dataclass(frozen=True)
class Scope:
    """기억의 격리 단위.

    session_id는 L2 사건 범위, continuity_id는 L1 상태가 이어지는 세계선이다.
    일반 세션 재개는 같은 continuity_id를 쓰고 초기화·분기는 새 값을 발급한다.
    """
    char_id: str
    user_id: str
    session_id: str
    continuity_id: str = "main"

    def profile_key(self) -> tuple[str, str, str]:
        return (self.char_id, self.user_id, self.continuity_id)


@dataclass
class Fact:
    """L1 프로필 항목. 같은 subject는 덮어쓴다."""
    subject: str
    value: str
    turn: int
    source_turns: list[int] = field(default_factory=list)


@dataclass
class Episode:
    """L2 에피소드 항목. 누적되며 상한 도달 시 롤업된다."""
    text: str
    turn: int
    importance: float = 0.5
    source_turns: list[int] = field(default_factory=list)
    invalidated: bool = False


@dataclass
class Turn:
    idx: int
    speaker: str
    text: str
    deleted: bool = False


class MemoryStore:
    def __init__(self) -> None:
        self.turns: dict[Scope, list[Turn]] = {}
        self.facts: dict[tuple[str, str, str], dict[str, Fact]] = {}
        self.fact_versions: dict[tuple[str, str, str], dict[str, list[Fact]]] = {}
        self.episodes: dict[Scope, list[Episode]] = {}
        self.queue: dict[Scope, list[int]] = {}        # 추출 대기 턴 인덱스
        self.extracted_upto: dict[Scope, int] = {}     # 추출이 끝난 턴 경계

    # ---------------------------------------------------------- 1) 대화 저장
    def append_turn(self, scope: Scope, speaker: str, text: str) -> Turn:
        turns = self.turns.setdefault(scope, [])
        t = Turn(len(turns), speaker, text)
        turns.append(t)
        self.queue.setdefault(scope, []).append(t.idx)
        return t

    def delete_turn(self, scope: Scope, idx: int) -> None:
        """대화 삭제/재생성. 그 턴에서 파생된 기억도 무효화한다."""
        self.turns[scope][idx].deleted = True
        for ep in self.episodes.get(scope, []):
            if idx in ep.source_turns:
                ep.invalidated = True
        pkey = scope.profile_key()
        facts = self.facts.get(pkey, {})
        versions = self.fact_versions.get(pkey, {})
        for subject, rows in list(versions.items()):
            for row in rows:
                row.source_turns = [turn for turn in row.source_turns if turn != idx]
            rows[:] = [row for row in rows if row.source_turns]
            if rows:
                facts[subject] = max(rows, key=lambda row: row.turn)
            else:
                versions.pop(subject, None)
                facts.pop(subject, None)

    # ------------------------------------------- 2~3) 비동기 추출 → 구조화 저장
    def run_extraction(self, scope: Scope, extractor, promoter=None) -> bool:
        """큐에 쌓인 턴을 한 번에 처리한다. 성공한 경우에만 큐를 비운다.

        실패 시 원문을 지우지 않으므로 다음 실행에서 다시 시도할 수 있다.
        """
        pending = [i for i in self.queue.get(scope, []) if not self.turns[scope][i].deleted]
        if not pending:
            return True
        payload = [self.turns[scope][i] for i in pending]
        try:
            facts, episodes = extractor(payload)
        except Exception:
            return False                       # 큐 보존 → 원문 유지

        for f in facts:
            self._store_fact(scope, f)
        eps = self.episodes.setdefault(scope, [])
        for e in episodes:
            if any(x.text == e.text and not x.invalidated for x in eps):
                continue                        # 중복 저장 방지
            eps.append(e)
        self._rollup(scope, promoter)
        self.queue[scope] = []
        self.extracted_upto[scope] = max(pending) + 1
        return True

    def _store_fact(self, scope: Scope, fact: Fact) -> None:
        """현재값과 version history를 함께 갱신한다."""
        pkey = scope.profile_key()
        store = self.facts.setdefault(pkey, {})
        versions = self.fact_versions.setdefault(pkey, {}).setdefault(fact.subject, [])
        prev = store.get(fact.subject)
        if prev and prev.value == fact.value:
            prev.source_turns = sorted(set(prev.source_turns) | set(fact.source_turns))
            return
        versions.append(fact)
        store[fact.subject] = fact

    def _rollup(self, scope: Scope, promoter=None) -> None:
        """문서 3.2절: 상한 도달 시 가장 오래된 3개를 한 줄로 접는다.

        롤업은 압축이 아니라 **사건에서 상태를 추출하는 단계**다. 접기 전에
        promoter가 확정 사실을 찾아내면 L2에서 빼고 L1으로 승격한다.
        promoter는 운영에서 보조 LLM이 맡을 판단이라 주입 가능하게 둔다.
        """
        eps = [e for e in self.episodes.get(scope, []) if not e.invalidated]
        if len(eps) <= L2_ITEM_CAP:
            return
        batch = sorted(eps, key=lambda e: e.turn)[:L2_ROLLUP_BATCH]

        # L2 → L1 승격: 사건이 남긴 상태만 프로필로 올린다
        promoted: list[Episode] = []
        if promoter is not None:
            for e in batch:
                got = promoter(e)
                if got is None:
                    continue
                subject, value = got
                self._store_fact(scope, Fact(subject, value, e.turn, list(e.source_turns)))
                promoted.append(e)

        old = [e for e in batch if e not in promoted]
        keep = [e for e in eps if e not in batch]
        if not old:                          # 전부 승격되면 접을 것이 없다
            self.episodes[scope] = keep
            return
        merged = Episode(
            text="(요약) " + " / ".join(e.text for e in old)[:120],
            turn=max(e.turn for e in old),
            importance=max(e.importance for e in old),
            source_turns=sorted({t for e in old for t in e.source_turns}),
        )
        self.episodes[scope] = [merged] + keep

    def pending_turns(self, scope: Scope) -> list[Turn]:
        """아직 추출되지 않은 최신 대화. 비동기 지연 중에도 보존되어야 한다."""
        return [self.turns[scope][i] for i in self.queue.get(scope, [])
                if not self.turns[scope][i].deleted]

    # ------------------------------ 4~5) 검색 후보 → 관련성·중요도·최신성 선택
    def recall(self, scope: Scope, query: str, k: int = 3) -> list[Episode]:
        eps = [e for e in self.episodes.get(scope, []) if not e.invalidated]
        q = set(re.findall(r"[가-힣A-Za-z]{2,}", query))
        scored = []
        for e in eps:
            words = set(re.findall(r"[가-힣A-Za-z]{2,}", e.text))
            rel = len(q & words) / len(q) if q else 0.0
            if rel == 0.0:
                continue                        # 무관한 기억은 후보에서 제외
            recency = 1 / (1 + max(0, max(x.turn for x in eps) - e.turn))
            scored.append((0.6 * rel + 0.25 * e.importance + 0.15 * recency, e))
        return [e for _, e in sorted(scored, key=lambda p: -p[0])[:k]]

    # ---------------------------------- 6~7) 예산 압축 → char_keywordbook 주입
    def build_keywordbook(self, scope: Scope, query: str = "", *, paid: bool = True) -> str:
        facts = sorted(self.facts.get(scope.profile_key(), {}).values(),
                       key=lambda f: -f.turn)
        eps = sorted([e for e in self.episodes.get(scope, []) if not e.invalidated],
                     key=lambda e: -e.turn)
        rec = self.recall(scope, query) if (paid and query) else []

        l1 = _fit([f"- {html.escape(f.subject, quote=True)}: {html.escape(f.value, quote=True)}"
                   for f in facts], BUDGET_L1)
        l2 = _fit([f"- {html.escape(e.text, quote=True)}" for e in eps], BUDGET_L2)
        l3 = _fit([f"- {html.escape(e.text, quote=True)}" for e in rec], BUDGET_L3)

        blocks = []
        if l1:
            blocks.append("<profile>\n" + "\n".join(l1) + "\n</profile>")
        if l2:
            blocks.append("<episodes>\n" + "\n".join(l2) + "\n</episodes>")
        if l3:
            blocks.append("<recall>\n" + "\n".join(l3) + "\n</recall>")
        return "\n".join(blocks)                # 후보가 없으면 빈 문자열


def _fit(lines: Iterable[str], budget: int) -> list[str]:
    out, used = [], 0
    for ln in lines:
        n = est_tokens(ln)
        if used + n > budget:
            break
        out.append(ln)
        used += n
    return out
