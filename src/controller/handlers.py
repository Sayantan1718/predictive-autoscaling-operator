from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import kopf
import requests
from kubernetes import client, config

from forecasting.model import forecast_load
from controller.models import PredictiveScalerSpec, PredictiveScalerStatus
from controller.prometheus import PrometheusClient, PrometheusQueryError
from controller.scaling import compute_desired_replicas

GROUP = "autoscaling.predictive-operator.dev"
VERSION = "v1alpha1"
PLURAL = "predictivescalers"

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus-server.monitoring.svc:80")
HISTORY_WINDOW_SECONDS = int(os.environ.get("HISTORY_WINDOW_SECONDS", "3600"))
METRIC_STEP_SECONDS = int(os.environ.get("METRIC_STEP_SECONDS", "15"))

prom_client = PrometheusClient(PROMETHEUS_URL)
_apps_v1: client.AppsV1Api | None = None


def get_apps_v1() -> client.AppsV1Api:
    """Lazily loads kube config on first use - lets kopf run 'run' before any
    cluster call is actually attempted, and works whether we're in-cluster or
    pointed at a local kind cluster via kubeconfig.
    """
    global _apps_v1
    if _apps_v1 is None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        _apps_v1 = client.AppsV1Api()
    return _apps_v1


@kopf.on.startup()
def on_startup(logger: logging.Logger, **_) -> None:
    logger.info("predictive-autoscaling-operator starting, prometheus=%s", PROMETHEUS_URL)


@kopf.timer(GROUP, VERSION, PLURAL, interval=15, initial_delay=5)
def reconcile(
    spec: dict,
    status: dict,
    patch: kopf.Patch,
    name: str,
    namespace: str,
    logger: logging.Logger,
    **_,
) -> None:
    parsed_spec = PredictiveScalerSpec(**spec)
    parsed_status = PredictiveScalerStatus(**(status or {}))

    now = datetime.now(timezone.utc)
    if parsed_status.last_reconcile_time is not None:
        elapsed = (now - parsed_status.last_reconcile_time).total_seconds()
        if elapsed < parsed_spec.sync_period_seconds:
            return  # this object isn't due for another look yet

    try:
        points = prom_client.query_range(
            parsed_spec.metric_query,
            start=now - timedelta(seconds=HISTORY_WINDOW_SECONDS),
            end=now,
            step_seconds=METRIC_STEP_SECONDS,
        )
    except (PrometheusQueryError, requests.RequestException) as err:
        # no data is not the same as zero load: scaling on it during an outage would
        # drop the deployment to minReplicas at the worst moment, so leave it alone
        logger.warning(
            "prometheus query failed for %s/%s, leaving replicas unchanged: %s",
            namespace, name, err,
        )
        return

    if not points:
        logger.warning(
            "query returned no data for %s/%s, leaving replicas unchanged", namespace, name
        )
        return

    current_load = points[-1][1]
    forecast = forecast_load(points, parsed_spec.forecast_horizon_seconds, METRIC_STEP_SECONDS)
    decision = compute_desired_replicas(parsed_spec, forecast, current_load)

    get_apps_v1().patch_namespaced_deployment_scale(
        name=parsed_spec.target_deployment,
        namespace=namespace,
        body={"spec": {"replicas": decision.desired_replicas}},
    )

    new_status = PredictiveScalerStatus(
        current_forecast=forecast.predicted_value,
        confidence=forecast.confidence,
        desired_replicas=decision.desired_replicas,
        fallback_active=decision.fallback_active,
    )
    new_status.touch()
    for key, value in new_status.to_patch_dict().items():
        patch.status[key] = value

    logger.info(
        "%s/%s -> %d replicas | %s",
        namespace, name, decision.desired_replicas, decision.reason,
    )