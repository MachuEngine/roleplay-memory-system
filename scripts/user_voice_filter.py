"""사칭 모드 출력 측 검사 — 유저에게 귀속된 발화 수 세기.

5.5절 결론: "최대 1회" 제약은 프롬프트로 보장되지 않으므로 출력 측에서 세어야 한다.
이 모듈이 그 카운터다. 정규식으로 산문 속 귀속을 완벽히 잡을 수는 없으므로
정밀도·재현율을 저장된 응답으로 측정하고 한계를 기록한다.

판정 규칙: 따옴표 구간 앞뒤 80자 안에
  (a) {유저명}[이가은는]? … (말|물|대답|답|핀잔|중얼|던|받아치|덧붙|속삭|내뱉|웃|끄덕)
  (b) 또는 "너[는가]" … 같은 발화 동사
  가 있고, 같은 창에 {캐릭터명}이 발화 주체로 더 가까이 있지 않으면 유저 발화로 센다.

usage: .venv/bin/python scripts/user_voice_filter.py
"""
from __future__ import annotations
import glob, json, re, sys
from pathlib import Path

SPEECH = r"(말|물|대답|답|핀잔|중얼|던졌|던진|받아쳤|덧붙|속삭|내뱉|웃으며|이었다|였다|했다|하는)"
QUOTE = re.compile(r'["“]([^"”]+)["”]')

def count_user_lines(text: str, user: str, char: str, window: int = 80) -> list[str]:
    hits = []
    for m in QUOTE.finditer(text):
        lo, hi = max(0, m.start() - window), min(len(text), m.end() + window)
        after = text[m.end():hi]; before = text[lo:m.start()]
        # 따옴표 바로 뒤 귀속: "..." 하람은/이 …   또는   "..." *너는 그렇게 말하고는…*
        if re.match(rf'\s*\*?\s*({user}|너)[이가은는]?\s', after) and re.search(SPEECH, after[:60]):
            hits.append(m.group(1)); continue
        # 앞 문장 귀속: 하람이 … "..."  /  너는 … "..."
        tail = before[-60:]
        if re.search(rf'({user}|너)[이가은는]?[^"”]{{0,40}}$', tail) and not re.search(rf'{char}[이가은는]?[^"”]{{0,20}}$', tail):
            if re.search(SPEECH, tail) or re.search(r'(묻|물|말|입을 열)', tail):
                hits.append(m.group(1))
    return hits

def main() -> None:
    root = Path(__file__).resolve().parents[1]
    recs = []
    for f in sorted(glob.glob(str(root / "tests/results/live_*.json"))): recs += json.load(open(f))
    print(f"{'케이스':7} {'사칭':4} {'유저 발화 수':>8}  근거")
    tp = fp = fn = 0
    for r in recs:
        fx = json.loads((root/"tests/fixtures/fixtures.json").read_text("utf-8"))["chars"][r["fixtures"]["char"]]
        hits = count_user_lines(r["result"]["text"], fx["user_name"], fx["char_name"])
        expected = 2 if r["test_id"] == "IMP-02" else 0   # 수동 평가에서 확인한 값
        n = len(hits)
        if r["test_id"] == "IMP-02": tp += min(n, expected); fn += max(0, expected - n)
        else: fp += n
        flag = "" if n == expected else f"  ← 기대 {expected}"
        print(f"{r['test_id']:7} {'ON' if r['impersonation'] else 'OFF':4} {n:>8}{flag}  {'; '.join(h[:18] for h in hits)}")
    print(f"\n수동 평가 대비: 정탐 {tp} / 오탐 {fp} / 미탐 {fn}  (표본 {len(recs)}건, 양성은 IMP-02 1건뿐)")

if __name__ == "__main__":
    main()
