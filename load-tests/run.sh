#!/usr/bin/env bash
# usage: load-tests/run.sh <ramp|daily> <predictive|hpa>
# Switches the demo app to the chosen controller, then runs the traffic profile
# and records what the cluster did. Never run both controllers on one Deployment.
set -euo pipefail

PROFILE="${1:?profile required: ramp or daily}"
LABEL="${2:?label required: predictive or hpa}"
WARMUP_SECONDS="${WARMUP_SECONDS:-120}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/load-tests/results/${LABEL}-${PROFILE}-$(date +%Y%m%d-%H%M%S)"

case "$LABEL" in
  predictive|hpa) ;;
  *) echo "label must be 'predictive' or 'hpa', got '$LABEL'" >&2; exit 1 ;;
esac

for cmd in k6 kubectl python3; do
  command -v "$cmd" >/dev/null || { echo "missing required tool: $cmd" >&2; exit 1; }
done

mkdir -p "$RUN_DIR"

echo "==> removing both controllers and resetting demo-app to 1 replica"
kubectl delete hpa demo-app --ignore-not-found
kubectl delete predictivescaler demo-app-scaler --ignore-not-found
kubectl scale deployment/demo-app --replicas=1
kubectl rollout status deployment/demo-app --timeout=120s

echo "==> enabling: $LABEL"
if [ "$LABEL" = "hpa" ]; then
  kubectl apply -f "$ROOT_DIR/configs/hpa.yaml"
else
  kubectl apply -f "$ROOT_DIR/configs/sample-predictivescaler.yaml"
fi

# both controllers need some idle history before the test starts
echo "==> warming up for ${WARMUP_SECONDS}s"
sleep "$WARMUP_SECONDS"

{
  echo "label: $LABEL"
  echo "profile: $PROFILE"
  echo "started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "peak_rps: ${PEAK_RPS:-30}"
  echo "hour_seconds: ${HOUR_SECONDS:-60}"
  echo "k6: $(k6 version | head -1)"
  echo "kubectl_server: $(kubectl version -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')"
} > "$RUN_DIR/run-info.txt"

python3 "$ROOT_DIR/load-tests/record.py" --out "$RUN_DIR/replicas.csv" &
RECORDER_PID=$!
trap 'kill $RECORDER_PID 2>/dev/null || true' EXIT

echo "==> running k6 profile=$PROFILE, results in $RUN_DIR"
PROFILE="$PROFILE" k6 run \
  --summary-export "$RUN_DIR/k6-summary.json" \
  "$ROOT_DIR/load-tests/traffic.js" | tee "$RUN_DIR/k6-output.txt"

echo "==> done: $RUN_DIR"