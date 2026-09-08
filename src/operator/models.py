from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator


class PredictiveScalerSpec(BaseModel):
    target_deployment: str = Field(alias="targetDeployment")
    min_replicas: int = Field(alias="minReplicas", ge=1)
    max_replicas: int = Field(alias="maxReplicas", ge=1)
    metric_query: str = Field(alias="metricQuery")
    requests_per_replica: float = Field(alias="requestsPerReplica", gt=0)
    forecast_horizon_seconds: int = Field(default=300, alias="forecastHorizonSeconds", ge=30)
    confidence_threshold: float = Field(default=0.6, alias="confidenceThreshold", ge=0, le=1)
    sync_period_seconds: int = Field(default=30, alias="syncPeriodSeconds", ge=10)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def check_replica_bounds(self) -> "PredictiveScalerSpec":
        # the CRD already enforces this via x-kubernetes-validations, but we don't
        # want the controller to trust that every cluster it runs on has that
        # admission check wired up correctly (older API server, webhook disabled, etc.)
        if self.max_replicas < self.min_replicas:
            raise ValueError("maxReplicas must be >= minReplicas")
        return self


class PredictiveScalerStatus(BaseModel):
    current_forecast: float | None = Field(default=None, alias="currentForecast")
    confidence: float | None = None
    desired_replicas: int | None = Field(default=None, alias="desiredReplicas")
    fallback_active: bool = Field(default=False, alias="fallbackActive")
    last_reconcile_time: datetime | None = Field(default=None, alias="lastReconcileTime")

    model_config = {"populate_by_name": True}

    def touch(self) -> None:
        self.last_reconcile_time = datetime.now(timezone.utc)

    def to_patch_dict(self) -> dict:
        # Kopf expects plain camelCase dicts when patching .status, not our
        # snake_case Python attribute names
        return self.model_dump(by_alias=True, exclude_none=True)