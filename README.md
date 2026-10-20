# Predictive Autoscaling Operator

A Kubernetes operator that scales a Deployment ahead of traffic instead of after it. It reads request-rate history from Prometheus, forecasts a few minutes forward, and sets the replica count for the load that is coming.

## The problem

The standard Horizontal Pod Autoscaler is reactive. CPU crosses a threshold, the HPA notices, new pods get scheduled, and then they need time to start. When traffic climbs steadily you are always one step behind, and users feel the gap.

This project adds a `PredictiveScaler` resource to the cluster. You point it at a Deployment and a Prometheus query, and a controller keeps the replica count matched to the load it expects, not the load it just saw.

## How it works

```
Prometheus ──> forecaster ──> replica math ──> patch Deployment scale
 (last hour)    (+ confidence)   (min/max clamp)
                     │
                     └── low confidence: use current load instead
```

Every sync period, for each `PredictiveScaler`:

1. The controller runs your PromQL query over the last hour in 15 second steps.
2. A damped-trend Holt-Winters model (statsmodels) forecasts the load `forecastHorizonSeconds` ahead. A confidence score comes from how closely the fitted curve tracked the recent data.
3. If confidence is at or above `confidenceThreshold`, desired replicas is `ceil(forecast / requestsPerReplica)`. Below it, the same formula runs on the current load, so the operator behaves reactively instead of guessing.
4. The result is clamped to `[minReplicas, maxReplicas]` and written to the Deployment's `scale` subresource.
5. The forecast, confidence, chosen replica count and whether the fallback is active are written to the object's `status`.

### The resource

```yaml
apiVersion: autoscaling.predictive-operator.dev/v1alpha1
kind: PredictiveScaler
metadata:
  name: demo-app-scaler
  namespace: default
spec:
  targetDeployment: demo-app
  minReplicas: 1
  maxReplicas: 10
  metricQuery: 'rate(demo_app_requests_total[2m])'
  requestsPerReplica: 5
  forecastHorizonSeconds: 300
  confidenceThreshold: 0.6
  syncPeriodSeconds: 30
```

| Field | Meaning | Default |
|---|---|---|
| `targetDeployment` | Deployment to scale (same namespace) | required |
| `minReplicas` / `maxReplicas` | Hard bounds on the replica count | required |
| `metricQuery` | PromQL returning the load, e.g. requests per second | required |
| `requestsPerReplica` | Load one replica can handle | required |
| `forecastHorizonSeconds` | How far ahead to forecast (min 30) | 300 |
| `confidenceThreshold` | Below this, fall back to current load (0 to 1) | 0.6 |
| `syncPeriodSeconds` | How often to re-evaluate (min 10) | 30 |

`kubectl get pscaler` shows the target, desired replicas, whether the fallback is active, and age.

`metricQuery` should be scoped to one workload. If it returns several series, the first one is used and a warning is logged.

## Design choices

**Kopf instead of kubebuilder.** Kopf is a Python operator framework, so the forecaster runs in the same process as the controller and there is no second service to deploy. kubebuilder (Go) is the more common choice on platform teams, but Go has no good time-series ecosystem, so it would need a separate Python forecasting service. Kopf is feature-complete and in maintenance mode, with regular releases (1.44.0 as of March 2026), which is fine for this scope.

**Holt-Winters instead of Prophet.** The forecast covers about five minutes, refit every sync period. Holt-Winters fits in milliseconds with no compiled dependency. Prophet is built for daily and yearly seasonality over long histories, which is a different problem. The trend is damped so a short burst isn't extrapolated into a huge replica count.

**A heuristic confidence score.** statsmodels doesn't give a cheap prediction interval for this model, and simulating one on every reconcile is too slow. Confidence is `1 - (residual std / mean load)`, clamped to 0 to 1. It is a measure of how well the model fits recent data, not a statistical guarantee.

**Least-privilege RBAC.** The ClusterRole covers only `predictivescalers`, `deployments/scale`, events, and read access to CRDs.

## Requirements

- Docker, [kind](https://kind.sigs.k8s.io/), kubectl, Helm
- [k6](https://grafana.com/docs/k6/) (load tests only)
- Python 3.12 (running the tests)

## Quick start

From the repo root:

```bash
./scripts/setup.sh
kubectl apply -f configs/sample-predictivescaler.yaml
kubectl get pscaler
```

`setup.sh` creates a kind cluster, installs the CRD, RBAC and Prometheus (Helm), builds the operator and demo app images, loads them into the cluster, and deploys both. It is safe to re-run. The demo app is reachable at `http://localhost:8080`.

Operator logs show each decision and the reason for it:

```bash
kubectl logs -n predictive-autoscaler-system deploy/predictive-autoscaler -f
```

### Operator settings

| Env var | Default |
|---|---|
| `PROMETHEUS_URL` | `http://prometheus-server.monitoring.svc:80` |
| `HISTORY_WINDOW_SECONDS` | `3600` |
| `METRIC_STEP_SECONDS` | `15` |

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests need no cluster. They cover spec validation (including `maxReplicas < minReplicas`), the replica math (clamping, rounding up, the low-confidence fallback), and forecaster edge cases (too little data, flat series, rising and falling trends).

## Load tests

Two traffic profiles, generated with k6 against the demo app:

- `ramp`: a steady climb from 2 to 40 requests per second, a hold, then a drop (15 minutes).
- `daily`: a compressed 24 hour pattern, one "hour" per 60 seconds (24 minutes).

```bash
load-tests/run.sh ramp predictive
load-tests/run.sh daily predictive
```

Each run resets the demo app to one replica, records replica counts and request rate every 5 seconds, and writes everything to `load-tests/results/<label>-<profile>-<timestamp>/`:

- `replicas.csv`: spec replicas, ready replicas, observed requests per second over time
- `k6-summary.json` and `k6-output.txt`: k6's own output

## Results

Raw output from every run is committed under `load-tests/results/` so the numbers can be checked rather than taken on trust.

<!-- Add the measured comparison (table + plot) here once the runs are done. Only use numbers from files in load-tests/results/. -->

## Limitations

- **Trend only, no seasonality.** The model extrapolates recent momentum. It can lead a steady climb, but it cannot anticipate a sudden spike it has never seen. Seasonal terms need days of history to learn.
- **No cooldown window.** Replica counts can move up and down between syncs. The HPA's stabilization window has no equivalent here yet.
- **Prometheus outage scales down.** If the query fails, there are no data points, the load is treated as zero, and the operator scales to `minReplicas`.
- **Confidence is a heuristic**, as described above.
- **One operator replica.** It runs with `--standalone`, with no leader election, so running two copies would make them fight over the same objects.
- **Namespace-local targets.** `targetDeployment` is looked up in the `PredictiveScaler`'s own namespace.

## Dependency notes

All versions are pinned in `pyproject.toml`. Two are deliberate:

- `kubernetes==35.0.0`: version 36.0.0 was reported to drop the `Authorization` header when using `load_incluster_config()`, which breaks every in-cluster API call ([kubernetes-client/python#2591](https://github.com/kubernetes-client/python/issues/2591)).
- `pandas==2.3.3`: staying on the 2.x line rather than moving to 3.0 for the time being.

## Project layout

```
src/
  controller/    Kopf handlers, spec/status models, Prometheus client, replica math
  forecasting/   Holt-Winters forecaster and confidence score
  demo_app/      small FastAPI service with Prometheus metrics, the thing being scaled
configs/         CRD, RBAC, operator and demo app manifests, kind and Prometheus config
scripts/         setup.sh, the one-command local cluster
load-tests/      k6 profiles, recorder, runner, saved results
tests/           unit tests
```

The package is called `controller` rather than `operator` on purpose, since `operator` would shadow a Python standard library module.

## License

MIT. See [LICENSE](LICENSE).