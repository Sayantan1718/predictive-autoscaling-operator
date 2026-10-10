"""Samples replica counts and request rate every few seconds and writes them to a CSV."""
import argparse
import csv
import json
import signal
import subprocess
import time
import urllib.parse

PROM_PROXY = "/api/v1/namespaces/monitoring/services/prometheus-server:80/proxy/api/v1/query"
RATE_QUERY = "sum(rate(demo_app_requests_total[1m]))"

running = True


def stop(*_):
    global running
    running = False


def kubectl_json(*args):
    out = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def observed_rps():
    url = f"{PROM_PROXY}?query={urllib.parse.quote(RATE_QUERY)}"
    out = subprocess.run(["kubectl", "get", "--raw", url], capture_output=True, text=True)
    if out.returncode != 0:
        return ""
    try:
        return json.loads(out.stdout)["data"]["result"][0]["value"][1]
    except (KeyError, IndexError, ValueError):
        return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", default="demo-app")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    started = time.time()
    with open(args.out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["elapsed_s", "spec_replicas", "ready_replicas", "observed_rps"])
        while running:
            deploy = kubectl_json("get", "deployment", args.deployment, "-n", args.namespace)
            if deploy:
                spec = deploy["spec"].get("replicas", "")
                ready = deploy["status"].get("readyReplicas", 0)
                writer.writerow([round(time.time() - started, 1), spec, ready, observed_rps()])
                fh.flush()
            time.sleep(args.interval)


if __name__ == "__main__":
    main()