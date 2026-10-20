import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = FastAPI(title="predictive-autoscaler-demo")

# CPU time burned per request. The HPA only sees CPU, so requests have to cost some.
WORK_MS = float(os.environ.get("WORK_MS", "20"))

REQUEST_COUNT = Counter("demo_app_requests_total", "Total requests handled by this pod")
REQUEST_LATENCY = Histogram("demo_app_request_latency_seconds", "Request latency in seconds")


def burn_cpu(ms: float) -> None:
    # thread_time counts CPU actually used by this thread, so cgroup throttling
    # can't shrink the work, it only makes the request take longer.
    end = time.thread_time() + ms / 1000
    while time.thread_time() < end:
        pass


@app.middleware("http")
async def track_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    # scrapes of /metrics would otherwise count as traffic
    if request.url.path != "/metrics":
        REQUEST_COUNT.inc()
        REQUEST_LATENCY.observe(time.perf_counter() - start)
    return response


# plain def on purpose: FastAPI runs it in a thread pool, so the spin loop
# doesn't block the event loop (and /metrics, /healthz) while it runs
@app.get("/")
def root():
    burn_cpu(WORK_MS)
    return {"status": "ok"}


@app.get("/healthz")
async def healthz():
    return {"status": "healthy"}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)