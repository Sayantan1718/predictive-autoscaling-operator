from __future__ import annotations

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


class PrometheusQueryError(Exception):
    pass


class PrometheusClient:
    """Pulls metric history out of Prometheus so the forecaster has something to train on."""

    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 15,
    ) -> list[tuple[datetime, float]]:
        """Runs a PromQL range query and returns (timestamp, value) pairs, oldest first."""
        resp = requests.get(
            f"{self.base_url}/api/v1/query_range",
            params={
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step_seconds,
            },
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        payload = resp.json()

        if payload.get("status") != "success":
            raise PrometheusQueryError(payload.get("error", "unknown Prometheus error"))

        results = payload["data"]["result"]
        if not results:
            return []

        # a range query can return multiple series if the query isn't scoped to one
        # target (e.g. missing a label filter). We only know how to deal with one.
        if len(results) > 1:
            logger.warning(
                "query '%s' returned %d series, using the first one", promql, len(results)
            )

        raw_points = results[0]["values"]
        return [(datetime.fromtimestamp(float(ts)), float(val)) for ts, val in raw_points]