"""Offline accuracy check for the forecaster on synthetic traffic.

The traffic mirrors the k6 profiles in load-tests/traffic.js. It is turned into what a
1m rate() query would report (Poisson request counts per scrape, averaged over the last
minute), then the forecaster is asked for the load 5 minutes ahead at every sync tick and
compared with what the metric actually showed at that point.

Run: PYTHONPATH=src python -m forecasting.evaluate --out evaluation/forecast-accuracy.json
"""
from __future__ import annotations

import argparse
import json
import math
import warnings
from datetime import datetime, timedelta
from statistics import mean, stdev

import numpy as np
import pandas
import statsmodels

from forecasting.model import forecast_load

STEP_SECONDS = 15
HORIZONS_SECONDS = (30, 60, 120, 300)
SYNC_SECONDS = 30
WARMUP_SECONDS = 120  # idle time before traffic starts, same as load-tests/run.sh
CONFIDENCE_THRESHOLD = 0.6
RATE_WINDOW_STEPS = 4  # rate(...[1m]) with a 15s scrape interval

# keep in sync with dayShape in load-tests/traffic.js
DAY_SHAPE = [
    0.10, 0.08, 0.07, 0.07, 0.08, 0.12, 0.20, 0.40,
    0.70, 0.85, 0.90, 0.95, 0.90, 0.85, 0.80, 0.80,
    0.85, 0.90, 0.70, 0.50, 0.35, 0.25, 0.18, 0.12,
]
HOUR_SECONDS = 60


def profile_stages(name: str, peak: float) -> list[tuple[float, int]]:
    """(target rate, seconds) stages, same shape as the k6 stages."""
    if name == "ramp":
        return [(2, 120), (peak, 480), (peak, 180), (2, 120)]
    if name == "daily":
        # floor(x + 0.5) because k6 uses Math.round, and Python's round() rounds halves to even
        return [(max(1, math.floor(peak * share + 0.5)), HOUR_SECONDS) for share in DAY_SHAPE]
    raise ValueError(f"unknown profile: {name}")


def true_rate(stages: list[tuple[float, int]], start_rate: float = 2) -> np.ndarray:
    """Requests/s at every scrape step, linear between stage targets like k6 does."""
    knots_t, knots_r = [0.0], [float(start_rate)]
    for target, seconds in stages:
        knots_t.append(knots_t[-1] + seconds)
        knots_r.append(float(target))
    times = np.arange(0, knots_t[-1], STEP_SECONDS)
    return np.interp(times, knots_t, knots_r)


def observed_series(rate: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """What sum(rate(counter[1m])) would show: Poisson counts, averaged over the window."""
    counts = rng.poisson(rate * STEP_SECONDS)
    out = np.empty(len(rate))
    for i in range(len(rate)):
        lo = max(0, i - RATE_WINDOW_STEPS + 1)
        out[i] = counts[lo : i + 1].mean() / STEP_SECONDS
    return out


def evaluate_run(profile: str, peak: float, seed: int, horizon_seconds: int) -> dict:
    rng = np.random.default_rng(seed)
    warm_steps = WARMUP_SECONDS // STEP_SECONDS
    rate = np.concatenate([np.zeros(warm_steps), true_rate(profile_stages(profile, peak))])
    observed = observed_series(rate, rng)

    horizon_steps = horizon_seconds // STEP_SECONDS
    sync_steps = SYNC_SECONDS // STEP_SECONDS
    base = datetime(2026, 1, 1)

    err_persist, err_model, err_operated, trusted = [], [], [], []
    for i in range(warm_steps, len(observed) - horizon_steps, sync_steps):
        points = [(base + timedelta(seconds=j * STEP_SECONDS), float(observed[j])) for j in range(i + 1)]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # statsmodels convergence chatter
            result = forecast_load(points, horizon_seconds, STEP_SECONDS)

        actual = observed[i + horizon_steps]
        persist = observed[i]
        trust = result.confidence >= CONFIDENCE_THRESHOLD
        operated = result.predicted_value if trust else persist

        err_persist.append(abs(persist - actual))
        err_model.append(abs(result.predicted_value - actual))
        err_operated.append(abs(operated - actual))
        trusted.append(trust)

    return {
        "origins": len(trusted),
        "mae_persistence": float(np.mean(err_persist)),
        "mae_model": float(np.mean(err_model)),
        "mae_operated": float(np.mean(err_operated)),
        "trusted_fraction": float(np.mean(trusted)),
    }


def summarize(values: list[float]) -> dict:
    return {"mean": round(mean(values), 3), "std": round(stdev(values), 3) if len(values) > 1 else 0.0}


def run(peak: float, seeds: int) -> dict:
    report = {
        "config": {
            "peak_rps": peak,
            "seeds": seeds,
            "step_seconds": STEP_SECONDS,
            "horizons_seconds": list(HORIZONS_SECONDS),
            "sync_seconds": SYNC_SECONDS,
            "warmup_seconds": WARMUP_SECONDS,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "statsmodels": statsmodels.__version__,
            "numpy": np.__version__,
            "pandas": pandas.__version__,
        },
        "results": [],
    }
    for profile in ("ramp", "daily"):
        for horizon in HORIZONS_SECONDS:
            runs = [evaluate_run(profile, peak, seed, horizon) for seed in range(seeds)]
            report["results"].append({
                "profile": profile,
                "horizon_seconds": horizon,
                "origins_per_seed": runs[0]["origins"],
                "mae_rps": {
                    "persistence": summarize([r["mae_persistence"] for r in runs]),
                    "model": summarize([r["mae_model"] for r in runs]),
                    "operated": summarize([r["mae_operated"] for r in runs]),
                },
                "trusted_fraction": summarize([r["trusted_fraction"] for r in runs]),
            })
    return report


def print_table(report: dict) -> None:
    cfg = report["config"]
    print(f"MAE of the load forecast, requests/s (peak {cfg['peak_rps']} rps, "
          f"{cfg['seeds']} seeds, mean +- std). Lower is better.\n")
    print(f"{'profile':<8} {'horizon':>8} {'persistence':>14} {'model':>14} {'operated':>14} {'trusted':>8}")
    for r in report["results"]:
        m = r["mae_rps"]
        cells = [f"{m[k]['mean']:.2f} +- {m[k]['std']:.2f}" for k in ("persistence", "model", "operated")]
        print(f"{r['profile']:<8} {str(r['horizon_seconds']) + 's':>8} {cells[0]:>14} {cells[1]:>14} "
              f"{cells[2]:>14} {r['trusted_fraction']['mean']:>8.0%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--peak", type=float, default=30)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--out", default="evaluation/forecast-accuracy.json")
    args = parser.parse_args()

    report = run(args.peak, args.seeds)
    print_table(report)
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()