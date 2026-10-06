"""표시 전 response guard와 안전 fallback의 키 없는 로컬 회귀 테스트."""
from __future__ import annotations

from response_guard import safe_fallback, validate


def main() -> None:
    safe = safe_fallback("서리")
    cases = [
        ("fallback", not validate(safe, "하람", False)),
        ("meta", bool(validate("I am 서리. 1. **Plan** " + safe, "하람", False))),
        ("unwrapped", bool(validate(safe.lstrip("*"), "하람", False))),
        ("off_user_action", bool(validate(
            safe.replace("*서리는", "*하람은 웃고 자리에서 일어났다. 서리는", 1),
            "하람", False,
        ))),
        ("on_two_voices", bool(validate(
            safe + '\n\n<user_voice>"응."</user_voice>\n<user_voice>"알았어."</user_voice>',
            "하람", True,
        ))),
    ]
    for name, ok in cases:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    passed = sum(ok for _, ok in cases)
    print(f"\n{passed}/{len(cases)} 통과")
    if passed != len(cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
