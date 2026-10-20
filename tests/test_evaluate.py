import numpy as np

from forecasting import evaluate


def test_ramp_profile_reaches_peak_and_returns_to_baseline():
    rate = evaluate.true_rate(evaluate.profile_stages("ramp", peak=30))

    assert rate.max() == 30
    assert rate[0] == 2
    assert rate[-1] < 6  # last sample is one step before the profile ends


def test_daily_profile_covers_24_stages():
    stages = evaluate.profile_stages("daily", peak=30)

    assert len(stages) == 24
    assert max(target for target, _ in stages) == 29  # 28.5 rounds up, like Math.round in traffic.js


def test_observed_series_is_reproducible_for_a_seed():
    rate = evaluate.true_rate(evaluate.profile_stages("ramp", peak=30))

    a = evaluate.observed_series(rate, np.random.default_rng(7))
    b = evaluate.observed_series(rate, np.random.default_rng(7))

    assert np.array_equal(a, b)


def test_evaluate_run_reports_finite_errors():
    result = evaluate.evaluate_run("ramp", peak=30, seed=0, horizon_seconds=60)

    assert result["origins"] > 10
    for key in ("mae_persistence", "mae_model", "mae_operated"):
        assert np.isfinite(result[key])
    assert 0.0 <= result["trusted_fraction"] <= 1.0