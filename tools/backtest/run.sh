#!/bin/bash
# Один прогон истории канала:  tools/backtest/run.sh TAG [ПЕРЕМЕННАЯ=ЗНАЧЕНИЕ ...]
#   DATA=<папка с ch.json, gold_hybrid.csv, config.yaml>  (по умолчанию ./bt_data)
#   OUT=<куда класть прогоны>                          (по умолчанию ./bt_runs)
#   PY=<python с telethon, pyyaml, tzdata>
# Пример: DATA=~/bt_data tools/backtest/run.sh S2 CHASE_SELL=2
# Результат: $OUT/r_TAG/out.txt (итог одной строкой JSON) и bt_TAG.json (сделки, сигналы, сообщения бота).
set -e
BT="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$BT/../.." && pwd)"
DATA="$(cd "${DATA:-./bt_data}" && pwd)"; OUT="${OUT:-./bt_runs}"; PY="${PY:-python3}"
tag=$1; shift
rm -rf "$OUT/r_$tag"; mkdir -p "$OUT/r_$tag/logs"; cd "$OUT/r_$tag"
cp "$REPO"/{main,trader,parser,guard,mt5link}.py "$REPO/tests/fake_mt5.py" "$BT/bt3.py" .
cp "$DATA/config.yaml" "$DATA/ch.json" . ; ln -sf "$DATA/gold_hybrid.csv" .
env TAG=$tag "$@" "$PY" bt3.py > out.txt 2>&1
tail -1 out.txt
