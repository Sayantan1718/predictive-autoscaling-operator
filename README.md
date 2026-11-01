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
  metricQuery: 'sum(rate(demo_app_requests_total[1m]))'
  requestsPerReplica: 3.5
  forecastHorizonSeconds: 120
  confidenceThreshold: 0.6
  syncPeriodSeconds: 30
```

| Field | Meaning | Default |
|---|---|---|
| `targetDeployment` | Deployment to scale (same namespace) | required |
| `minReplicas` / `maxReplicas` | Hard bounds on the replica count | required |
| `metricQuery` | PromQL returning the load, e.g. requests per second | required |
| `requestsPerReplica` | Load one replica can handle | required |
| `forecastHorizonSeconds` | How far ahead to forecast (min 30). Set it near how long a new pod takes to become useful, not higher. | 300 |
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
- A bash shell. On Windows, use Git Bash or WSL2; the scripts won't run in PowerShell.

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

The tests need no cluster. They cover spec validation (including `maxReplicas < minReplicas`), the replica math (clamping, rounding up, the low-confidence fallback), forecaster edge cases (too little data, flat series, rising and falling trends), and the reconcile handler (a Prometheus outage or empty result leaves replicas untouched, and a healthy series scales).

## Load tests

Two traffic profiles, generated with k6 against the demo app:

- `ramp`: a steady climb from 2 to 30 requests per second, a hold, then a drop (15 minutes).
- `daily`: a compressed 24 hour pattern, one "hour" per 60 seconds (24 minutes).

The same profile is run against two controllers, one at a time: this operator, and a stock CPU-based HPA (`configs/hpa.yaml`, 80% target, default behavior).

```bash
load-tests/run.sh ramp predictive
load-tests/run.sh ramp hpa
load-tests/run.sh daily predictive
load-tests/run.sh daily hpa
```

For a fair comparison, each demo app request burns about 20 ms of CPU (22 ms measured including framework overhead), so CPU-based scaling has something to react to. The HPA target (80% of a 100m CPU request) and `requestsPerReplica: 3.5` both work out to roughly 3.5 requests per second per pod.

Each run removes both controllers, resets the demo app to one replica, enables the chosen one, warms up for two minutes, then records replica counts and request rate every 5 seconds. Everything goes to `load-tests/results/<label>-<profile>-<timestamp>/`:

- `replicas.csv`: spec replicas, ready replicas, observed requests per second over time
- `run-info.txt`: label, profile, peak rate, k6 and Kubernetes versions
- `k6-summary.json` and `k6-output.txt`: k6's own output

## Forecast accuracy

The forecaster was evaluated offline on synthetic traffic that mirrors the two k6 profiles. The traffic is turned into what `sum(rate(...[1m]))` would report (Poisson request counts per 15 second scrape, averaged over the last minute). At every 30 second sync tick the forecaster predicts the load some seconds ahead, and the prediction is compared with what the metric showed at that time. It is scored against "persistence", which simply uses the current value, since that is what a purely reactive scaler effectively assumes.

Mean absolute error in requests per second, peak 30 rps, 20 seeds (mean, lower is better):

| Profile | Horizon | Persistence | Model | Model vs persistence |
|---|---|---|---|---|
| ramp | 30s | 1.65 | 0.84 | -49% |
| ramp | 60s | 3.03 | 1.91 | -37% |
| ramp | 120s | 5.46 | 3.60 | -34% |
| ramp | 300s | 13.37 | 10.01 | -25% |
| daily | 30s | 1.31 | 0.70 | -46% |
| daily | 60s | 2.50 | 1.62 | -35% |
| daily | 120s | 4.70 | 3.71 | -21% |
| daily | 300s | 10.28 | 12.60 | +23% |

What this says:

- Up to two minutes ahead the model beats persistence on both profiles, by roughly a fifth to a half.
- At 300 seconds on the compressed daily profile it loses. A 5 minute horizon there covers five simulated hours, and the traffic changes direction inside that window. The horizon should match the lead time scaling needs (pod start plus metric delay), which is why the sample uses 120 seconds.
- The confidence gate does not separate good forecasts from bad ones. The model is trusted 89% to 96% of the time, including the daily 300 second case where it is worse than persistence, and gating changes the error by a few percent at most. Confidence here measures how well the model fits past data, not whether it will be right next.

These are synthetic results with a simple noise model, not production traffic. Reproduce them with:

```bash
PYTHONPATH=src python -m forecasting.evaluate --seeds 20 --out evaluation/forecast-accuracy.json
```

The full output, including standard deviations and library versions, is in `evaluation/forecast-accuracy.json`.

## Results

Raw output from every run is committed under `load-tests/results/` so the numbers can be checked rather than taken on trust.

### Ramp profile, HPA vs PredictiveScaler

One 15 minute run of each, back to back, same cluster, same demo app, peak 30 requests per second. Replica counts come from `replicas.csv` (sampled every 5 seconds), latency from k6's own summary.

![Replica count over time for the HPA and the PredictiveScaler](docs/ramp-comparison.png)

| Metric | HPA (CPU 80%) | PredictiveScaler |
|---|---|---|
| Replica-seconds (cost proxy) | 4460 | 5731 |
| Max ready replicas | 8 | 10 |
| Seconds below 3.5 req/s per replica | 604 | 53 |
| Replica count changes | 7 | 17 |
| Scale-downs | 0 | 6 |
| Requests / failed | 15232 / 0 | 15221 / 17 |
| Median latency | 51 ms | 352 ms |
| p90 / p95 latency | 1858 / 2024 ms | 1585 / 1750 ms |
| Max latency | 2822 ms | 22146 ms |

"Below 3.5 req/s per replica" counts the seconds where ready replicas times 3.5 was less than the offered load, using the ramp shape from `traffic.js`. 3.5 is the capacity both controllers were tuned for. The HPA saw no scale-down because its default stabilization window (300 s) is longer than the ramp-down.

What the runs show:

- **It scales ahead.** On the climb the PredictiveScaler stayed above the replica count the load needed, while the HPA trailed it for most of the ramp (604 s below capacity against 53 s).
- **It costs more.** About 28% more replica-seconds, and it peaked at 10 replicas for a load that needs 9.
- **It is jumpier.** At about 1 minute, with load around 1.4 req/s, it scaled to 3 and then 4 replicas, then back to 1. At about 13.7 minutes it dropped from 9 to 4 replicas while 28 req/s were still arriving.
- **Latency did not improve.** The median was worse, the p90 and p95 were slightly better, and the worst request was 22 s with 17 failed requests (0.11%). Four k6 runs exist in total: both HPA runs had zero failures, both predictive runs had some (1 and 17), and the p95 stayed between 1.7 and 2.0 s in all four. Tail latency in this setup is therefore not set by replica count alone. The cause is not isolated. A single-node kind cluster shares one laptop's CPU with k6 and Prometheus, and the abrupt scale-down is a likely source of the failures, but neither has been tested.

These are single runs per controller on one machine, so treat the differences as indications. Two earlier runs without a replica timeline are kept in `load-tests/results/` as well.

## Limitations

- **Trend only, no seasonality.** The model extrapolates recent momentum. It can lead a steady climb, but it cannot anticipate a sudden spike it has never seen. Seasonal terms need days of history to learn.
- **No cooldown window.** Replica counts can move up and down between syncs. The HPA's stabilization window has no equivalent here yet. In the ramp test this showed up as 17 replica changes against the HPA's 7.
- **Scale-downs follow the forecast, not the load being served.** When the forecast says traffic will fall, the replica count drops even while current traffic still needs the capacity. In the ramp test it went from 9 to 4 replicas at about 28 req/s.
- **No metrics, no scaling.** If Prometheus is unreachable, returns an error, or returns no data, the operator logs a warning and leaves the replica count where it is. It does not scale down on missing data, but it also can't scale up until metrics come back.
- **Confidence is a heuristic and a weak one.** It is an in-sample fit measure, and the accuracy evaluation shows it does not flag the cases where the forecast is worse than just using the current load. Scoring the model on a held-out recent window would be a better signal.
- **One operator replica.** It runs with `--standalone`, with no leader election, so running two copies would make them fight over the same objects.
- **Namespace-local targets.** `targetDeployment` is looked up in the `PredictiveScaler`'s own namespace.

## Dependency notes

All versions are pinned in `pyproject.toml`. Two are deliberate:

- `kubernetes==35.0.0`: version 36.0.0 was reported to drop the `Authorization` header when using `load_incluster_config()`, which breaks every in-cluster API call ([kubernetes-client/python#2591](https://github.com/kubernetes-client/python/issues/2591)).
- `pandas==2.3.3`: staying on the 2.x line rather than moving to 3.0 until statsmodels support is confirmed.

## Project layout

```
src/
  controller/    Kopf handlers, spec/status models, Prometheus client, replica math
  forecasting/   Holt-Winters forecaster, confidence score, offline accuracy evaluation
  demo_app/      small FastAPI service with Prometheus metrics, the thing being scaled
configs/         CRD, RBAC, operator and demo app manifests, kind and Prometheus config
scripts/         setup.sh, the one-command local cluster
load-tests/      k6 profiles, recorder, runner, saved results
evaluation/      saved output of the offline forecast accuracy run
tests/           unit tests
```

The package is called `controller` rather than `operator` on purpose, since `operator` would shadow a Python standard library module.

## License

MIT. See [LICENSE](LICENSE).