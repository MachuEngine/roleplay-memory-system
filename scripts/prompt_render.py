"""Handlebars 부분집합 렌더러.

system.hbs가 쓰는 문법만 지원한다: {{var}}, {{a.b}}, {{#if path}}...{{else}}...{{/if}}.
실제 서비스가 쓰는 Handlebars와 동작을 맞추기 위한 최소 구현이며,
목적은 "조건부 블록이 사칭 모드 on/off에서 서로 다른 프롬프트를 만든다"를
테스트에서 실제로 재현하는 것이다.

usage: .venv/bin/python scripts/prompt_render.py --impersonation on
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "prompts" / "system.hbs"

_IF = re.compile(
    r"\{\{#if\s+([\w.]+)\s*\}\}(.*?)(?:\{\{else\}\}(.*?))?\{\{/if\}\}",
    re.DOTALL,
)
_VAR = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
_TRUSTED_STRUCTURED = {"char_keywordbook"}  # server renderer가 tag를 만들고 값은 이미 escape함


def lookup(ctx: Mapping[str, Any], path: str) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def truthy(v: Any) -> bool:
    """Handlebars의 falsy 규칙: false, undefined, null, "", 0, []."""
    if v is None or v is False:
        return False
    if isinstance(v, (str, list, tuple, dict)):
        return len(v) > 0
    if isinstance(v, (int, float)):
        return v != 0
    return True


def render(template: str, ctx: Mapping[str, Any]) -> str:
    def if_sub(m: re.Match[str]) -> str:
        branch = m.group(2) if truthy(lookup(ctx, m.group(1))) else (m.group(3) or "")
        return render(branch, ctx)

    prev = None
    out = template
    while prev != out:                      # 중첩 블록을 안쪽부터 접는다
        prev = out
        out = _IF.sub(if_sub, out)

    def var_sub(m: re.Match[str]) -> str:
        path = m.group(1)
        v = lookup(ctx, path)
        if v is None:
            return ""
        text = str(v)
        return text if path in _TRUSTED_STRUCTURED else html.escape(text, quote=True)

    return _VAR.sub(var_sub, out)


def render_system(ctx: Mapping[str, Any], template_path: Path = TEMPLATE) -> str:
    rendered = render(template_path.read_text(encoding="utf-8"), ctx)
    # 조건부 블록이 접히며 남는 빈 줄 정리 (실제 서비스 렌더러와 동일하게 취급)
    return re.sub(r"\n{3,}", "\n\n", rendered).strip() + "\n"


def unresolved(text: str) -> list[str]:
    """렌더 후에도 남아 있는 플레이스홀더. 비어 있어야 정상."""
    return sorted(set(_VAR.findall(text)) | set(re.findall(r"\{\{#if|\{\{/if|\{\{else", text)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--impersonation", choices=["on", "off"], default="off")
    a = ap.parse_args()
    ctx = {
        "char_name": "서리",
        "user_name": "하람",
        "char_description": "(char_description)",
        "user_description": "(user_description)",
        "char_keywordbook": "(char_keywordbook)",
        "chat_history": "(chat_history)",
        "option": {"impersonation": a.impersonation == "on"},
    }
    text = render_system(ctx)
    print(text)
    print(f"--- 미치환 플레이스홀더: {unresolved(text) or '없음'}")
