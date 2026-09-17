from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import ContentTypeLatest, Counter, Histogram, generate_latest

app = FastAPI(title="Predictive Autoscaling Operator Metrics")

REQUEST_COUNT = Counter("predictive_autoscaling_operator_request_count", "Total number of requests by this pod")
REQUEST_LATENCY = Histogram("predictive_autoscaling_operator_request_latency_seconds", "Request latency in seconds")


@app.middleware("http")
async def track_request(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/metrics":
        REQUEST_COUNT.inc()
        REQUEST_LATENCY.observe(time.perf_counter() - start)
    return response

@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=ContentTypeLatest)