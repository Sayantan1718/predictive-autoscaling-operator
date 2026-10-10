#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="predictive-autoscaler"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for cmd in docker kind kubectl helm; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "missing required tool: $cmd" >&2
    exit 1
  fi
done

echo "==> creating kind cluster"
if kind get clusters | grep -qx "$CLUSTER_NAME"; then
  echo "cluster '$CLUSTER_NAME' already exists, skipping create"
else
  kind create cluster --config "$ROOT_DIR/configs/kind-config.yaml"
fi

echo "==> installing the PredictiveScaler CRD"
kubectl apply -f "$ROOT_DIR/configs/namespace.yaml"
kubectl apply -f "$ROOT_DIR/configs/crd.yaml"
kubectl apply -f "$ROOT_DIR/configs/rbac.yaml"

echo "==> installing Prometheus"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo update >/dev/null
helm upgrade --install prometheus prometheus-community/prometheus \
  --namespace monitoring --create-namespace \
  --values "$ROOT_DIR/configs/prometheus-values.yaml" \
  --wait --timeout 5m

echo "==> building images"
docker build -t demo-app:local -f "$ROOT_DIR/src/demo_app/Dockerfile" "$ROOT_DIR/src/demo_app"
docker build -t predictive-autoscaler:local -f "$ROOT_DIR/Dockerfile" "$ROOT_DIR"

echo "==> loading images into kind (this cluster can't pull from a registry)"
kind load docker-image demo-app:local --name "$CLUSTER_NAME"
kind load docker-image predictive-autoscaler:local --name "$CLUSTER_NAME"

echo "==> deploying the demo app and the operator"
kubectl apply -f "$ROOT_DIR/configs/demo-app-deployment.yaml"
kubectl apply -f "$ROOT_DIR/configs/operator-deployment.yaml"

echo "==> waiting for everything to come up"
kubectl rollout status deployment/demo-app --timeout=120s
kubectl rollout status deployment/predictive-autoscaler -n predictive-autoscaler-system --timeout=120s

echo "==> done. demo app reachable at http://localhost:8080"
echo "    apply configs/sample-predictivescaler.yaml when you're ready to turn on autoscaling"