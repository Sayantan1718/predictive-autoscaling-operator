"""Plots HPA vs predictive replica counts for one load profile and prints a summary table.

Picks the newest run of each controller that has a replicas.csv, so runs from before the
recorder worked are skipped.

    python load-tests/plot.py --profile ramp --out docs/ramp-120s.png

Pass --hpa and --predictive with result folders to plot older runs instead.
"""
import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = Path(__file__).parent / "results"
COLORS = {"hpa": "#1f77b4", "predictive": "#d95f02"}
NAMES = {"hpa": "HPA (CPU 80%)", "predictive": "PredictiveScaler"}


def newest_run(label: str, profile: str) -> Path:
    candidates = sorted(RESULTS.glob(f"{label}-{profile}-*/replicas.csv"))
    if not candidates:
        raise SystemExit(f"no {label}-{profile} run with a replicas.csv under {RESULTS}")
    return candidates[-1].parent


def read_timeline(run: Path) -> list[dict]:
    with open(run / "replicas.csv", newline="") as fh:
        return [
            {
                "t": float(row["elapsed_s"]),
                "spec": int(row["spec_replicas"]),
                "ready": int(row["ready_replicas"]),
                "rps": float(row["observed_rps"]) if row["observed_rps"] else math.nan,
            }
            for row in csv.DictReader(fh)
        ]


def summarize(run: Path, timeline: list[dict], capacity: float, load_at) -> dict:
    step = (timeline[-1]["t"] - timeline[0]["t"]) / (len(timeline) - 1)
    short = sum(step for p in timeline if p["ready"] * capacity < load_at(p["t"]))
    changes = [b["spec"] - a["spec"] for a, b in zip(timeline, timeline[1:]) if b["spec"] != a["spec"]]
    k6 = json.loads((run / "k6-summary.json").read_text())["metrics"]
    dur = k6["http_req_duration"]
    return {
        "run": run.name,
        "replica_seconds": round(sum(p["ready"] * step for p in timeline)),
        "max_ready": max(p["ready"] for p in timeline),
        "seconds_below_capacity": round(short),
        "replica_changes": len(changes),
        "scale_downs": sum(1 for c in changes if c < 0),
        "requests": k6["http_reqs"]["count"],
        "failed": k6["http_req_failed"]["passes"],
        "median_ms": round(dur["med"]),
        "p90_ms": round(dur["p(90)"]),
        "p95_ms": round(dur["p(95)"]),
        "max_ms": round(dur["max"]),
    }


def ramp_load(t: float) -> float:
    """Offered load of the ramp profile in traffic.js (2 rps hold, climb, hold, drop)."""
    if t < 120:
        return 2
    if t < 600:
        return 2 + 28 * (t - 120) / 480
    if t < 780:
        return 30
    if t < 900:
        return 30 - 28 * (t - 780) / 120
    return 2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="ramp", choices=["ramp"])
    parser.add_argument("--capacity", type=float, default=3.5, help="requests/s one replica is sized for")
    parser.add_argument("--out", default="docs/ramp-comparison.png")
    parser.add_argument("--hpa", type=Path, help="result folder of the HPA run (default: newest)")
    parser.add_argument("--predictive", type=Path, help="result folder of the predictive run (default: newest)")
    parser.add_argument("--title", help="plot title (default: the profile name)")
    args = parser.parse_args()

    runs = {
        "hpa": args.hpa or newest_run("hpa", args.profile),
        "predictive": args.predictive or newest_run("predictive", args.profile),
    }
    timelines = {label: read_timeline(run) for label, run in runs.items()}

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 6.5), sharex=True, height_ratios=[3, 1.4])
    for label, timeline in timelines.items():
        minutes = [p["t"] / 60 for p in timeline]
        top.step(minutes, [p["ready"] for p in timeline], where="post", color=COLORS[label],
                 linewidth=2, label=NAMES[label])
        bottom.plot(minutes, [p["rps"] for p in timeline], color=COLORS[label], linewidth=1.5)

    grid = [i * 5 for i in range(int(timelines["hpa"][-1]["t"] // 5) + 1)]
    needed = [math.ceil(ramp_load(t) / args.capacity) for t in grid]
    top.step([t / 60 for t in grid], needed, where="post", color="#666666", linestyle="--",
             linewidth=1.2, label=f"needed for the offered load ({args.capacity:g} req/s per replica)")

    top.set_ylabel("ready replicas")
    top.set_title(args.title or f"{args.profile} profile: replica count over time")
    top.legend(frameon=False, loc="upper left")
    top.grid(alpha=0.25)
    bottom.set_ylabel("observed req/s\n(1m rate)")
    bottom.set_xlabel("minutes since the load test started")
    bottom.grid(alpha=0.25)
    fig.tight_layout()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    print(f"wrote {out}\n")

    table = {label: summarize(runs[label], timelines[label], args.capacity, ramp_load) for label in runs}
    rows = [
        ("run folder", "run"), ("replica-seconds", "replica_seconds"), ("max ready replicas", "max_ready"),
        (f"seconds below {args.capacity:g} req/s per replica", "seconds_below_capacity"),
        ("replica count changes", "replica_changes"), ("scale-downs", "scale_downs"),
        ("requests", "requests"), ("failed requests", "failed"), ("median latency (ms)", "median_ms"),
        ("p90 latency (ms)", "p90_ms"), ("p95 latency (ms)", "p95_ms"), ("max latency (ms)", "max_ms"),
    ]
    print(f"| metric | {NAMES['hpa']} | {NAMES['predictive']} |\n|---|---|---|")
    for title, key in rows:
        print(f"| {title} | {table['hpa'][key]} | {table['predictive'][key]} |")


if __name__ == "__main__":
    main()