#!/usr/bin/env bash
# PostToolUse(Edit|Write) hook: 검증에 영향을 주는 파일이 바뀌면 로컬 검증을 돌린다.
# 실패하면 exit 2로 결과를 Claude에게 돌려보내 바로 고치게 한다.
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
file=$(jq -r '.tool_response.filePath // .tool_input.file_path // empty')
[ -n "$file" ] || exit 0

case "$file" in
  "$root"/scripts/*.py | "$root"/prompts/* | "$root"/docs/*.md | \
  "$root"/tests/cases/* | "$root"/tests/fixtures/*) ;;
  *) exit 0 ;;
esac

if ! out=$(bash "$root/scripts/check_local.sh" 2>&1); then
  echo "로컬 검증 실패 (${file#"$root"/} 수정 후):" >&2
  echo "$out" >&2
  exit 2
fi
exit 0
