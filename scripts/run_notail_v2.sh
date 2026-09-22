#!/usr/bin/env bash
# 새 사양(8~10문단)에서 앵커 유무 비교. '앵커 있음'은 tests/logs/spec_ab/paras_seed*.jsonl 재사용
set -eo pipefail
cd "$(dirname "$0")/.."
set -a; . /Users/jongmin/JobChange/.env; set +a
OUT=tests/logs/notail_v2
mkdir -p "$OUT"
for s in 11 23 37 53 71; do
  echo "=== seed $s / 앵커 없음 ==="
  .venv/bin/python scripts/run_live_tests.py --template tests/variants/v_notail_v2.hbs \
    --seed "$s" --budget-usd 0.5 2>&1 | tail -3
  cp "$(ls -t tests/logs/live_*.jsonl | head -1)" "$OUT/notail_seed${s}.jsonl"
done
echo "완료"
