#!/usr/bin/env bash
# 후행 앵커 A/B 본 측정: 5 seed × 2 arm × 18 케이스 = 180회
set -eo pipefail
cd "$(dirname "$0")/.."
set -a; . /Users/jongmin/JobChange/.env; set +a
OUT=tests/logs/tail_ab
mkdir -p "$OUT"
for s in 11 23 37 53 71; do
  for arm in with without; do
    echo "=== seed $s / arm $arm ==="
    if [ "$arm" = "with" ]; then
      .venv/bin/python scripts/run_live_tests.py --seed "$s" --budget-usd 0.5 2>&1 | tail -3
    else
      .venv/bin/python scripts/run_live_tests.py --template tests/variants/v_no_tail.hbs \
        --seed "$s" --budget-usd 0.5 2>&1 | tail -3
    fi
    LATEST=$(ls -t tests/logs/live_*.jsonl | head -1)
    cp "$LATEST" "$OUT/${arm}_seed${s}.jsonl"
  done
done
echo "완료: $OUT"
