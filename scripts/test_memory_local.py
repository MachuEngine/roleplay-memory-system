"""메모리 시스템 로컬 검증 (API 호출 없음).

문서가 주장하는 동작을 scripts/memory_sim.py 참조 구현으로 실제 실행해 확인한다.
추출 단계의 LLM은 결정적인 가짜 추출기로 대체한다 — 검증 대상은 저장·충돌·격리·예산
로직이지 요약 품질이 아니다.

usage: .venv/bin/python scripts/test_memory_local.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_sim import (BUDGET_TOTAL, Episode, Fact, MemoryStore, Scope,  # noqa: E402
                        EPHEMERAL, est_tokens)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def scoped(s: str = "s1") -> Scope:
    return Scope(char_id="c_seori", user_id="u_haram", session_id=s)


def extractor_factory(facts=(), episodes=(), fail: bool = False):
    def extract(turns):
        if fail:
            raise RuntimeError("추출 실패(모의)")
        idxs = [t.idx for t in turns]
        fs = [Fact(s, v, max(idxs), list(idxs)) for s, v in facts]
        es = [Episode(t, max(idxs), imp, list(idxs)) for t, imp in episodes]
        return fs, es
    return extract


# 1) 동일 사실 중복 저장 방지
def t_dedup() -> None:
    st, sc = MemoryStore(), scoped()
    for _ in range(3):
        st.append_turn(sc, "user", "나 커피 못 마셔")
        st.run_extraction(sc, extractor_factory(facts=[("음료", "커피를 못 마신다")],
                                                episodes=[("보리차를 가져왔다", 0.4)]))
    facts = st.facts[sc.profile_key()]
    eps = st.episodes[sc]
    check("동일 사실 중복 저장 방지", len(facts) == 1 and len(eps) == 1,
          f"facts={len(facts)}, episodes={len(eps)}")


# 2) 기존 사실 수정
def t_update() -> None:
    st, sc = MemoryStore(), scoped()
    st.append_turn(sc, "user", "나 서울 살아")
    st.run_extraction(sc, extractor_factory(facts=[("거주지", "서울")]))
    st.append_turn(sc, "user", "이사했어, 이제 부산이야")
    st.run_extraction(sc, extractor_factory(facts=[("거주지", "부산")]))
    f = st.facts[sc.profile_key()]["거주지"]
    check("기존 사실 수정(덮어쓰기)", len(st.facts[sc.profile_key()]) == 1 and f.value == "부산",
          f"거주지={f.value}")


# 3) 최근 정보와 오래된 정보의 충돌
def t_conflict() -> None:
    st, sc = MemoryStore(), scoped()
    st.append_turn(sc, "char", "그 시집은 못 찾겠어")
    st.run_extraction(sc, extractor_factory(facts=[("시집", "구하지 못했다")]))
    old_turn = st.facts[sc.profile_key()]["시집"].turn
    st.append_turn(sc, "char", "시집 구했어, 여기")
    st.run_extraction(sc, extractor_factory(facts=[("시집", "구해서 건넸다")]))
    f = st.facts[sc.profile_key()]["시집"]
    check("충돌 시 최신값 채택", f.value == "구해서 건넸다" and f.turn > old_turn,
          f"값={f.value}, turn {old_turn}→{f.turn}")


# 4) 임시 발언과 지속 기억의 구분
def t_ephemeral() -> None:
    samples = [("오늘만 야근할게", True), ("잠깐만 기다려", True),
               ("나 커피 못 마셔", False), ("이사했어, 이제 부산이야", False),
               ("아까 한 말은 취소할게", True)]
    bad = [s for s, want in samples if bool(EPHEMERAL.search(s)) != want]
    check("임시 발언/지속 기억 구분", not bad, f"오분류={bad}" if bad else "5건 전부 일치")


# 5) 삭제·재생성된 대화의 기억 무효화
def t_invalidate() -> None:
    st, sc = MemoryStore(), scoped()
    st.append_turn(sc, "user", "나 고양이 키워")
    st.run_extraction(sc, extractor_factory(facts=[("반려동물", "고양이")],
                                            episodes=[("고양이 사진을 보여줬다", 0.5)]))
    st.delete_turn(sc, 0)
    facts = st.facts[sc.profile_key()]
    eps = [e for e in st.episodes[sc] if not e.invalidated]
    st2, sc2 = MemoryStore(), scoped()
    st2.append_turn(sc2, "user", "나 서울 살아")
    st2.run_extraction(sc2, extractor_factory(facts=[("거주지", "서울")]))
    st2.append_turn(sc2, "user", "이사했어, 이제 부산이야")
    st2.run_extraction(sc2, extractor_factory(facts=[("거주지", "부산")]))
    st2.delete_turn(sc2, 1)
    restored = st2.facts[sc2.profile_key()]["거주지"].value
    check("삭제된 턴 무효화 / 이전 version 복원", not facts and not eps and restored == "서울",
          f"facts={len(facts)}, 유효 episodes={len(eps)}, 복원값={restored}")


# 6) 캐릭터·유저·세션 간 격리
def t_isolation() -> None:
    st = MemoryStore()
    a = Scope("c_seori", "u_haram", "s1")
    b = Scope("c_noa", "u_haram", "s1")       # 캐릭터가 다름
    c = Scope("c_seori", "u_other", "s1")     # 유저가 다름
    d = Scope("c_seori", "u_haram", "s2")     # 세션만 다름
    e = Scope("c_seori", "u_haram", "s3", "alt")  # 같은 캐릭터의 다른 세계선
    st.append_turn(a, "user", "비밀 얘기")
    st.run_extraction(a, extractor_factory(facts=[("비밀", "삼촌 이야기")],
                                           episodes=[("비밀을 털어놨다", 0.9)]))
    leaked = [n for n, s in (("다른 캐릭터", b), ("다른 유저", c))
              if st.facts.get(s.profile_key())]
    ep_leak = [n for n, s in (("다른 캐릭터", b), ("다른 유저", c), ("다른 세션", d))
               if st.episodes.get(s)]
    carry = bool(st.facts.get(d.profile_key()))   # 같은 연속성은 세션을 넘어 이어진다
    branch_leak = bool(st.facts.get(e.profile_key()))
    check("캐릭터·유저 간 기억 격리", not leaked and not ep_leak, f"유출={leaked+ep_leak}")
    check("세션 간 L2 격리 / 같은 연속성 L1 승계 / 분기 격리",
          not st.episodes.get(d) and carry and not branch_leak,
          f"s2 에피소드={len(st.episodes.get(d, []))}, "
          f"s2 프로필 승계={carry}, 다른 세계선 유출={branch_leak}")


# 7) 검색 결과가 없을 때 정상 대화 유지
def t_no_recall() -> None:
    st, sc = MemoryStore(), scoped()
    st.append_turn(sc, "user", "간판 전구 갈았잖아")
    st.run_extraction(sc, extractor_factory(facts=[("음료", "커피를 못 마신다")],
                                            episodes=[("간판 전구를 갈았다", 0.3)]))
    hits = st.recall(sc, "오늘 손님 많았어?")
    kb = st.build_keywordbook(sc, "오늘 손님 많았어?")
    check("무관한 질의에 리콜 0건", not hits, f"hits={len(hits)}")
    check("리콜 0건에도 프로필·에피소드는 주입", "<profile>" in kb and "<recall>" not in kb,
          f"블록={[b for b in ('profile','episodes','recall') if f'<{b}>' in kb]}")
    empty = MemoryStore()
    check("기억이 전혀 없으면 빈 문자열 주입(대화 유지)",
          empty.build_keywordbook(scoped(), "아무거나") == "", "")


# 7-B) L2 → L1 승격 (문서 3.2절 "이 구조의 핵심 동작")
def t_promotion() -> None:
    st, sc = MemoryStore(), scoped()

    def promoter(ep):
        """롤업 대상 중 '약속'은 사건이 아니라 상태이므로 L1으로 올린다."""
        if "바다에 가기로" in ep.text:
            return ("약속", "다음 주말 바다에 가기로 함")
        return None

    for i in range(12):                      # 상한(10) 초과까지 쌓아 롤업을 유발
        text = "바다에 가기로 했다" if i == 0 else f"에피소드 {i}"
        st.append_turn(sc, "user", text)
        st.run_extraction(sc, extractor_factory(episodes=[(text, 0.5)]), promoter=promoter)

    facts = st.facts[sc.profile_key()]
    eps = [e.text for e in st.episodes[sc] if not e.invalidated]
    check("L2→L1 승격: 확정 사실이 프로필로 이동", "약속" in facts,
          f"프로필={list(facts)}")
    check("승격된 항목은 L2에서 제거", not any("바다에 가기로" in e for e in eps),
          f"L2에 잔존={[e for e in eps if '바다' in e]}")
    check("승격 없이는 롤업 요약으로만 접힘", "(요약)" in " ".join(eps), "")


# 8) 메모리 토큰 상한 적용
def t_budget() -> None:
    st, sc = MemoryStore(), scoped()
    for i in range(40):
        st.append_turn(sc, "user", f"사실 {i}")
        st.run_extraction(sc, extractor_factory(
            facts=[(f"항목{i}", "매우 " * 20 + "긴 값")],
            episodes=[(f"에피소드 {i} " + "내용이 길게 이어진다 " * 10, 0.5)]))
    kb = st.build_keywordbook(sc, "에피소드")
    n = est_tokens(kb)
    check(f"주입 토큰 ≤ 예산 {BUDGET_TOTAL}", n <= BUDGET_TOTAL, f"실측 {n} 토큰")
    check("L2 롤업으로 항목 수 상한 유지", len(st.episodes[sc]) <= 11,
          f"에피소드 {len(st.episodes[sc])}건")


# 9) 비동기 처리가 끝나지 않은 최신 대화 보존
def t_pending() -> None:
    st, sc = MemoryStore(), scoped()
    st.append_turn(sc, "user", "방금 한 중요한 말")
    ok = st.run_extraction(sc, extractor_factory(fail=True))
    pending = st.pending_turns(sc)
    check("추출 실패 시 원문 보존 + 재시도 가능", not ok and len(pending) == 1,
          f"성공={ok}, 대기={len(pending)}")
    ok2 = st.run_extraction(sc, extractor_factory(facts=[("메모", "중요")]))
    check("재시도 성공 후 큐 비움", ok2 and not st.pending_turns(sc),
          f"성공={ok2}, 대기={len(st.pending_turns(sc))}")
    st2, sc2 = MemoryStore(), scoped()
    st2.append_turn(sc2, "user", "아직 추출 전인 최신 발화")
    check("추출 전 최신 턴이 유실되지 않음", len(st2.pending_turns(sc2)) == 1, "")


def main() -> None:
    for fn in (t_dedup, t_update, t_conflict, t_ephemeral, t_invalidate,
               t_isolation, t_no_recall, t_promotion, t_budget, t_pending):
        fn()
    width = max(len(n) for n, _, _ in RESULTS)
    print("## 메모리 시스템 로컬 검증\n")
    for name, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = sum(not ok for _, ok, _ in RESULTS)
    print(f"\n{len(RESULTS)}건 중 통과 {len(RESULTS) - failed} / 실패 {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
