#!/usr/bin/env bash
# usage: load-tests/run.sh <ramp|daily> <label>
# label is something like "predictive" or "hpa", so runs sort nicely side by side
set -euo pipefail

PROFILE="${1:?profile required: ramp or daily}"
LABEL="${2:?label required, e.g. predictive or hpa}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/load-tests/results/${LABEL}-${PROFILE}-$(date +%Y%m%d-%H%M%S)"

for cmd in k6 kubectl python3; do
  command -v "$cmd" >/dev/null || { echo "missing required tool: $cmd" >&2; exit 1; }
done

mkdir -p "$RUN_DIR"

echo "==> resetting demo-app to 1 replica and letting things settle"
kubectl scale deployment/demo-app --replicas=1
kubectl rollout status deployment/demo-app --timeout=120s
sleep 30

python3 "$ROOT_DIR/load-tests/record.py" --out "$RUN_DIR/replicas.csv" &
RECORDER_PID=$!
trap 'kill $RECORDER_PID 2>/dev/null || true' EXIT

echo "==> running k6 profile=$PROFILE, results in $RUN_DIR"
PROFILE="$PROFILE" k6 run \
  --summary-export "$RUN_DIR/k6-summary.json" \
  "$ROOT_DIR/load-tests/traffic.js" | tee "$RUN_DIR/k6-output.txt"

echo "==> done: $RUN_DIR"