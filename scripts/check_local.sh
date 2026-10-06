#!/usr/bin/env bash
# 키 없이 도는 로컬 검증을 한 번에 실행한다. 모델을 호출하지 않으며 파일을 쓰지 않는다.
# 하나라도 실패하면 실패한 검사의 출력을 보여 주고 1로 끝난다.
#
# usage: bash scripts/check_local.sh
set -uo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif command -v python3.11 >/dev/null 2>&1; then PY=python3.11
else PY=python3
fi

failed=0
run() {
  local name="$1"; shift
  local out
  if out=$("$@" 2>&1); then
    echo "PASS  $name"
  else
    echo "FAIL  $name"
    echo "$out" | tail -30 | sed 's/^/      /'
    failed=1
  fi
}

run "구문 검사 (scripts/*.py, demo/*.py)" "$PY" -m py_compile scripts/*.py demo/*.py
run "원가 계산 (cost_model)" "$PY" scripts/cost_model.py
run "원가 독립 검산 (verify_cost)" "$PY" scripts/verify_cost.py
run "memory 참조 구현 (test_memory_local)" "$PY" scripts/test_memory_local.py
run "response guard (test_response_guard_local)" "$PY" scripts/test_response_guard_local.py
run "플레이그라운드 엔진 (test_demo_local)" "$PY" scripts/test_demo_local.py

exit $failed
