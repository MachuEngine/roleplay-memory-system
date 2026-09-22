#!/usr/bin/env bash
# 출력 사양 재설계 A/B: 2 변형 × 5 seed × 18 케이스 = 180회
# 기준선(현행 5~6문단)은 tests/logs/tail_ab/with_seed*.jsonl 재사용
set -eo pipefail
cd "$(dirname "$0")/.."
set -a; . /Users/jongmin/JobChange/.env; set +a
OUT=tests/logs/spec_ab
mkdir -p "$OUT"
for s in 11 23 37 53 71; do
  for v in paras nocount; do
    echo "=== seed $s / $v ==="
    .venv/bin/python scripts/run_live_tests.py --template "tests/variants/v_spec_${v}.hbs" \
      --seed "$s" --budget-usd 0.5 2>&1 | tail -3
    cp "$(ls -t tests/logs/live_*.jsonl | head -1)" "$OUT/${v}_seed${s}.jsonl"
  done
done
echo "완료: $OUT"
