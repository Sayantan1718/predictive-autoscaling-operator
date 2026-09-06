# Project: Predictive Autoscaling Operator

## Goal
Build a real Kubernetes operator (custom CRD + controller, not a script) that forecasts load and
scales workloads ahead of demand, replacing reactive CPU-threshold HPA behavior with a
proactive, prediction-driven scaling decision.

## Why This Matters (recruiter framing)
This demonstrates platform-engineering depth — writing an actual controller with a reconciliation
loop and CRDs — distinct from "wrap an LLM in an API." Shows breadth beyond agentic-AI-only
projects, which matters for roles that blend ML and infrastructure.

## Stack (pin exact versions before coding)
- **Decision point (pick one, don't do both)**: kubebuilder/operator-sdk (Go) — idiomatic,
  reads as more production-grade to a platform team — vs. Kopf (Python operator framework) —
  faster to write, fits your existing Python/ML stack better. Verify Kopf's current maintenance
  status before committing to it; if it looks stale, default to kubebuilder.
- Prophet (or a simpler statistical model — ARIMA/exponential smoothing) for load forecasting.
  This does not need a GPU or deep learning; justify the model choice, don't default to a neural
  net for the sake of it.
- Prometheus — historical metrics source for training the forecaster and for live signal
- kind/minikube — local cluster for development and testing

## Architecture (high level)
1. Custom CRD (e.g. `PredictiveScaler`) defines target Deployment, forecast horizon, scaling
   bounds
2. Controller reconciliation loop: pulls recent Prometheus metrics for the target workload
3. Forecasting step: predicts load N minutes ahead
4. Scaling decision: computes target replica count from the forecast (with min/max bounds from
   the CRD spec), patches the target Deployment's replica count
5. Fallback: if forecast confidence is low or data is insufficient, defer to standard HPA
   behavior rather than making a blind prediction

## Achievement Criteria (Definition of Done)
- [ ] Custom CRD installs cleanly on a local kind/minikube cluster
- [ ] Controller reconciliation loop runs continuously without crashing under normal operation
- [ ] Forecaster trained/evaluated on real or realistic synthetic traffic pattern data, with
      reported forecast accuracy (no fabricated numbers — use your own logged evaluation)
      the numbers you actually measured
- [ ] Demonstrated scale-ahead-of-demand behavior on a synthetic load test (e.g. using `k6` or
      `hey` to generate a predictable traffic ramp), compared side-by-side against standard HPA
      reaction time
- [ ] Fallback-to-HPA behavior implemented and tested for the low-confidence case
- [ ] Unit tests for: forecasting logic, replica-count computation, CRD validation
- [ ] Dependencies pinned; local cluster setup fully scripted (not manual steps in a README only)

## Git / Documentation Reminders
- Structure: `src/` (or Go module layout if kubebuilder), `tests/`, `configs/` (CRD manifests,
  sample `PredictiveScaler` resources), `README.md`, `LICENSE`
- README must cover: problem, approach, setup, exact run commands, the side-by-side comparison
  against standard HPA (with the actual measured numbers/plot), limitations, license
- State explicitly which language/framework you chose (kubebuilder vs. Kopf) and why, as the
  one-line tradeoff — this is a deliberate engineering decision worth surfacing to reviewers
- Commit incrementally; tag a release once the full CRD→reconcile→scale loop works on a local
  cluster with the synthetic load test passing
- Include the raw synthetic load test results (not just a summary claim) so the "scales ahead of
  demand" claim is verifiable
