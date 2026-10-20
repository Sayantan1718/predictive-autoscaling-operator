import logging
from datetime import datetime, timedelta

import kopf
import pytest
import requests

from controller import handlers
from controller.prometheus import PrometheusQueryError

SPEC = {
    "targetDeployment": "demo-app",
    "minReplicas": 1,
    "maxReplicas": 10,
    "metricQuery": "sum(rate(demo_app_requests_total[1m]))",
    "requestsPerReplica": 3.5,
}


class FakeApps:
    def __init__(self):
        self.scale_calls = []

    def patch_namespaced_deployment_scale(self, **kwargs):
        self.scale_calls.append(kwargs)


@pytest.fixture
def apps(monkeypatch):
    fake = FakeApps()
    monkeypatch.setattr(handlers, "get_apps_v1", lambda: fake)
    return fake


def series(values, step=15):
    start = datetime(2026, 1, 1)
    return [(start + timedelta(seconds=i * step), v) for i, v in enumerate(values)]


def reconcile():
    patch = kopf.Patch()
    handlers.reconcile(
        spec=SPEC,
        status={},
        patch=patch,
        name="demo-app-scaler",
        namespace="default",
        logger=logging.getLogger("test"),
    )
    return patch


def serve(monkeypatch, result):
    def fake_query(*args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(handlers.prom_client, "query_range", fake_query)


@pytest.mark.parametrize(
    "failure",
    [requests.ConnectionError("down"), PrometheusQueryError("bad query"), []],
    ids=["unreachable", "error-response", "no-data"],
)
def test_missing_metrics_leave_replicas_alone(apps, monkeypatch, failure):
    serve(monkeypatch, failure)

    patch = reconcile()

    assert apps.scale_calls == []
    assert patch.status == {}


def test_healthy_rising_load_scales_up_and_reports_status(apps, monkeypatch):
    serve(monkeypatch, series([2 + 0.3 * i for i in range(60)]))  # 2 -> ~19.7 rps

    patch = reconcile()

    assert len(apps.scale_calls) == 1
    call = apps.scale_calls[0]
    assert call["name"] == "demo-app"
    assert call["namespace"] == "default"
    assert call["body"]["spec"]["replicas"] > 1
    assert patch.status["desiredReplicas"] == call["body"]["spec"]["replicas"]


def test_too_little_history_falls_back_to_current_load(apps, monkeypatch):
    serve(monkeypatch, series([3.0, 5.0, 7.0]))  # under MIN_DATA_POINTS

    patch = reconcile()

    assert apps.scale_calls[0]["body"]["spec"]["replicas"] == 2  # ceil(7 / 3.5)
    assert patch.status["fallbackActive"] is True