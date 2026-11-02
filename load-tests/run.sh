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

command -v kubectl >/dev/null || { echo "missing required tool: kubectl" >&2; exit 1; }

# K6_DOCKER=1 runs k6 from a container, for machines where a k6 binary can't be installed or run
K6_IMAGE="${K6_IMAGE:-grafana/k6:2.3.0}"
if [ -n "${K6_DOCKER:-}" ]; then
  command -v docker >/dev/null || { echo "missing required tool: docker" >&2; exit 1; }
else
  command -v k6 >/dev/null || { echo "missing required tool: k6" >&2; exit 1; }
  # on Windows the extensionless `k6` can be a package-manager shim Git Bash can't execute
  K6="${K6:-k6}"
  [ "$K6" = k6 ] && command -v k6.exe >/dev/null && K6=k6.exe
fi

# Git Bash on Windows usually has `python`, not `python3` (and a Store stub that fails when run)
PY=""
for cand in python3 python; do
  if "$cand" -c 'import sys' >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "missing required tool: python" >&2; exit 1; }

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
  if [ -n "${K6_DOCKER:-}" ]; then
    echo "k6: $(docker run --rm "$K6_IMAGE" version | head -1) (docker image $K6_IMAGE)"
  else
    echo "k6: $("$K6" version | head -1)"
  fi
  echo "kubectl_server: $(kubectl version -o json | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')"
  if [ "$LABEL" = "predictive" ]; then
    echo "scaler_spec: $(kubectl get predictivescaler demo-app-scaler -o jsonpath='{.spec}')"
  fi
} > "$RUN_DIR/run-info.txt"

"$PY" "$ROOT_DIR/load-tests/record.py" --out "$RUN_DIR/replicas.csv" &
RECORDER_PID=$!
trap 'kill $RECORDER_PID 2>/dev/null || true' EXIT

# the recorder writes its header on startup; no file means it died and the run would be useless
sleep 5
[ -s "$RUN_DIR/replicas.csv" ] || { echo "recorder did not start, aborting before the load test" >&2; exit 1; }

echo "==> running k6 profile=$PROFILE, results in $RUN_DIR"
run_k6() {
  if [ -n "${K6_DOCKER:-}" ]; then
    local out_dir
    out_dir="$(command -v cygpath >/dev/null && cygpath -w "$RUN_DIR" || echo "$RUN_DIR")"
    MSYS_NO_PATHCONV=1 docker run --rm -i \
      -v "$out_dir:/out" \
      -e PROFILE="$PROFILE" -e PEAK_RPS="${PEAK_RPS:-30}" -e HOUR_SECONDS="${HOUR_SECONDS:-60}" \
      -e TARGET_URL="${TARGET_URL:-http://host.docker.internal:8080/}" \
      "$K6_IMAGE" run --summary-export /out/k6-summary.json - < "$ROOT_DIR/load-tests/traffic.js"
  else
    PROFILE="$PROFILE" "$K6" run \
      --summary-export "$RUN_DIR/k6-summary.json" \
      "$ROOT_DIR/load-tests/traffic.js"
  fi
}
run_k6 | tee "$RUN_DIR/k6-output.txt"

echo "==> done: $RUN_DIR"