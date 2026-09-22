#!/usr/bin/env bash
# MEM-04 재현 확인: seed 20개 × 앵커 유/무 = 40회
set -eo pipefail
cd "$(dirname "$0")/.."
set -a; . /Users/jongmin/JobChange/.env; set +a
OUT=tests/logs/mem04_probe
mkdir -p "$OUT"
for s in 11 101 202 303 404 505 606 707 808 909 1010 1111 1212 1313 1414 1515 1616 1717 1818 1919; do
  for arm in with without; do
    if [ "$arm" = "with" ]; then
      .venv/bin/python scripts/run_live_tests.py --only MEM-04 --seed "$s" --budget-usd 0.3 >/dev/null 2>&1
    else
      .venv/bin/python scripts/run_live_tests.py --only MEM-04 --template tests/variants/v_notail_v2.hbs \
        --seed "$s" --budget-usd 0.3 >/dev/null 2>&1
    fi
    cp "$(ls -t tests/logs/live_*.jsonl | head -1)" "$OUT/${arm}_seed${s}.jsonl"
    echo "seed $s / $arm 완료"
  done
done
echo "끝"
