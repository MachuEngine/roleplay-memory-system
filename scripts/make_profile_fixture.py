"""운영 프로필(입력 약 14,220토큰) 재현용 입력 생성.

5장 실호출은 입력이 772~1,504토큰이라 암시적 캐싱 최소 조건(2,048)에 미달했다.
캐시 효과를 보려면 설계가 전제한 크기의 입력이 필요하다.

내용은 전부 가상이며, 분량을 맞추기 위해 장면·항목을 기계적으로 확장한 부분이 있다.
캐싱은 프리픽스 일치 기반이라 내용의 문학적 완성도는 측정에 영향을 주지 않는다.

usage: .venv/bin/python scripts/make_profile_fixture.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_tokens import estimate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "profile_full.json"

TARGET_USER_CONTENT, TARGET_MEMORY, TARGET_HISTORY = 6000, 1200, 6000

CHAR_HEAD = """이름: 서리 (본명 비공개. 삼촌만 본명을 알았고, 그는 2년 전에 죽었다)
나이: 27
직업: 헌책방 '설야서림' 주인. 3년 전 삼촌에게 물려받았다. 폐업 직전이다.
외양: 목까지 오는 검은 머리를 늘 대충 묶는다. 왼쪽 손목에 오래된 흉터가 있고, 긴장하면 무의식적으로 그 자리를 매만진다. 계절과 무관하게 얇은 가디건을 걸친다.
말투: 존댓말을 쓰지 않는다. 문장이 짧고 끝을 흐리는 버릇이 있다. 감정이 올라오면 오히려 말수가 준다. 상대를 이름으로 부르지 않고 '너'라고 한다.
성격: 무심해 보이지만 남의 습관을 잘 기억한다. 호의를 표현하는 방식이 서툴러서 챙겨주고도 퉁명스럽게 말한다. 사과를 못 한다.
금기: 가족 이야기가 나오면 화제를 돌린다. 책방을 팔 생각이 없냐는 질문에 대답하지 않는다.
습관: 손님이 없을 때 창가 자리에서 식은 커피를 마신다. 비 오는 날에는 문을 조금 열어둔다.
"""

SHELVES = [
    ("A열", "국내 시집", "삼촌이 가장 아꼈다. 서리는 이 열만은 먼지를 매일 턴다"),
    ("B열", "번역 소설", "판매의 대부분. 표지가 상한 것이 많다"),
    ("C열", "절판 인문서", "값을 매기기 어려워 대부분 팔지 않는다"),
    ("D열", "잡지 합본", "1980년대 것이 상당수. 사는 사람이 거의 없다"),
    ("E열", "아동 도서", "삼촌이 마지막으로 들여놓은 칸. 서리는 손대지 않는다"),
    ("F열", "실용서", "회전이 가장 빠르다. 서리가 유일하게 신경 쓰는 매대"),
    ("G열", "화집·도록", "무거워서 아래 칸에만 둔다"),
    ("H열", "외국어 원서", "정리가 안 되어 있다. 언젠가 하겠다고 미뤄둔 칸"),
]
OBJECTS = [
    ("낡은 금전등록기", "여닫을 때 소리가 크다. 고장 나면 고칠 사람이 없다"),
    ("창가 2인용 탁자", "하람이 늘 앉는 자리. 다리 하나가 짧아 종이를 접어 괴어 두었다"),
    ("검은 장우산", "문 옆. 주인을 모른다. 서리는 삼촌 것이라고 생각한다"),
    ("접이식 사다리", "높은 칸용. 한쪽 경첩이 헐거워 흔들린다"),
    ("전기 주전자", "커피용. 보온이 안 돼서 늘 식는다"),
    ("먼지 쌓인 라디오", "주파수가 하나만 잡힌다. 서리는 끄지 않는다"),
    ("삼촌의 장부", "손글씨. 2년 전에서 멈춰 있다. 서리는 이어 쓰지 않았다"),
    ("고양이 밥그릇", "가게 뒤편. 오지 않게 된 지 반년쯤 됐다"),
]
PEOPLE = [
    ("하람", "단골", "반년째 주 2~3회. 유일하게 오래 머무는 사람"),
    ("건물주 최씨", "임대인", "월세 이야기를 꺼낼 때마다 서리는 말을 돌린다"),
    ("옆집 세탁소 부부", "이웃", "가끔 반찬을 넘겨준다. 서리는 갚을 방법을 모른다"),
    ("헌책 도매상 박씨", "거래처", "삼촌 때부터 거래했다. 서리에게 값을 후하게 쳐준다"),
    ("이름 모르는 노인", "손님", "매일 아침 신문만 보고 간다. 한 번도 책을 산 적 없다"),
    ("삼촌", "고인", "2년 전 사망. 서리가 유일하게 가족으로 여겼던 사람"),
]
BEATS = [
    "문을 열자 종이 한 번 울렸다. 그 소리에 고개를 들지 않는 것이 서리의 방식이다.",
    "계산대 아래에는 팔리지 않은 책들이 상자째 쌓여 있다. 서리는 그것을 치우지 않는다.",
    "비가 오면 바닥 한쪽이 눅눅해진다. 수건을 깔아 두었다가 마르면 걷는다.",
    "오후 네 시쯤 햇빛이 창가 탁자에 닿는다. 그 시간에만 먼지가 보인다.",
    "손님이 책을 오래 들여다보면 서리는 일부러 다른 쪽을 본다.",
    "값을 묻는 사람에게는 늘 실제보다 조금 낮게 말한다.",
    "닫을 때는 라디오를 먼저 끄고, 그다음 불을 끈다. 순서를 바꾼 적이 없다.",
    "책등이 갈라진 책은 따로 빼 둔다. 고칠 생각은 없으면서 버리지도 못한다.",
]


def grow(base: str, target: int, blocks: list[str]) -> str:
    """목표 토큰에 닿을 때까지 블록을 순환하며 덧붙인다."""
    out, i = [base], 0
    while estimate("\n".join(out)) < target:
        out.append(blocks[i % len(blocks)])
        i += 1
    return "\n".join(out)


def char_description() -> str:
    blocks = []
    blocks.append("\n[서가 구성]\n" + "\n".join(
        f"- {a}: {b} — {c}" for a, b, c in SHELVES))
    blocks.append("\n[가게 안의 물건]\n" + "\n".join(
        f"- {a}: {b}" for a, b in OBJECTS))
    blocks.append("\n[관계]\n" + "\n".join(
        f"- {a} ({b}): {c}" for a, b, c in PEOPLE))
    for n, beat in enumerate(BEATS, 1):
        blocks.append(f"\n[장면 습관 {n}] {beat}")
    for y in range(1, 40):
        blocks.append(
            f"[연표 {y:02d}] 개업 {y}년차 기록 — 그해 가장 많이 팔린 책은 "
            f"{SHELVES[y % len(SHELVES)][1]}였고, 가장 오래 남은 책은 "
            f"{SHELVES[(y + 3) % len(SHELVES)][1]}였다. "
            f"{BEATS[y % len(BEATS)]}")
    return grow(CHAR_HEAD, TARGET_USER_CONTENT - 200, blocks)


def user_description() -> str:
    return ("이름: 하람\n나이: 25\n직업: 계약직 교정교열자. 마감이 불규칙하다.\n"
            "관계: 설야서림의 거의 유일한 단골. 반년째 주 2~3회 들른다.\n"
            "특징: 말이 많은 편이고 침묵을 잘 못 견딘다. 커피를 못 마셔서 늘 보리차를 가져온다.\n"
            "버릇: 긴장하면 말이 빨라진다. 책을 살 때 표지를 먼저 쓸어 본다.")


def memory() -> str:
    prof = ["- 하람을 '너'라고 부른다. 이름으로 부른 적 없음",
            "- 하람은 커피를 못 마신다. 늘 보리차를 가져옴",
            "- 서리는 사과를 말로 하지 못하고 행동으로 대신한다",
            "- 가족 이야기는 회피 대상. 삼촌만 예외"]
    eps = [f"- ({d}일 전) {b}" for d, b in
           zip(range(2, 60, 4), BEATS * 3)]
    rec = ["(2일 전 대화) 하람: \"여기 계속 할 수 있는 거지?\" / 서리: \"...비 그쳤네.\""]
    base = ("<profile>\n" + "\n".join(prof) + "\n</profile>\n<episodes>\n"
            + "\n".join(eps[:6]) + "\n</episodes>\n<recall>\n" + "\n".join(rec) + "\n</recall>")
    blocks = [f"<!-- 확장 {i} --> {b}" for i, b in enumerate(eps[6:], 1)]
    return grow(base, TARGET_MEMORY - 60, blocks or ["- (기록 없음)"])


def history() -> str:
    turns = []
    for i in range(200):
        turns.append(f"하람: \"{BEATS[i % len(BEATS)][:20]}… 그거 아직 그대로야?\"")
        turns.append(f"서리: *{BEATS[(i + 2) % len(BEATS)]}* \"...몰라.\"")
        if estimate("\n".join(turns)) > TARGET_HISTORY - 80:
            break
    return "\n".join(turns)


def main() -> None:
    data = {
        "_note": "가상 데이터. 운영 프로필(입력 약 14,220토큰) 재현용. 분량 확보를 위해 "
                 "장면·연표 항목을 기계적으로 확장했다.",
        "char_name": "서리", "user_name": "하람",
        "char_description": char_description(),
        "user_description": user_description(),
        "char_keywordbook": memory(),
        "chat_history": history(),
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    est = {k: round(estimate(v)) for k, v in data.items() if k.startswith(("char_", "user_", "chat_"))}
    uc = est["char_description"] + est["user_description"]
    print(f"유저 콘텐츠 (char+user_description) {uc:>6,} 토큰  목표 {TARGET_USER_CONTENT:,}")
    print(f"메모리 (char_keywordbook)          {est['char_keywordbook']:>6,} 토큰  목표 {TARGET_MEMORY:,}")
    print(f"대화 내역 (chat_history)           {est['chat_history']:>6,} 토큰  목표 {TARGET_HISTORY:,}")
    print(f"시스템 프롬프트                       {920:>6,} 토큰")
    print(f"{'합계(추정)':<32} {uc + est['char_keywordbook'] + est['chat_history'] + 920:>6,} 토큰  목표 14,220")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
