"""research/stats.py with pinned values (docs/specs/research_spec.md)."""
import math
import random

import pytest

from research import stats


def test_brier_and_log_loss_pinned():
    p, y = [0.9, 0.2, 0.6, 0.5], [1, 0, 0, 1]
    assert stats.brier(p, y) == pytest.approx((0.01 + 0.04 + 0.36 + 0.25) / 4)
    expected = -(math.log(0.9) + math.log(0.8) + math.log(0.4) + math.log(0.5)) / 4
    assert stats.log_loss(p, y) == pytest.approx(expected)
    assert stats.log_loss([0.0], [1]) == pytest.approx(-math.log(1e-6))


@pytest.mark.parametrize("p,y", [([0.5], [2]), ([1.2], [1]), ([0.5, 0.5], [1]), ([], [])])
def test_bad_inputs_raise(p, y):
    with pytest.raises(ValueError):
        stats.brier(p, y)


def test_decomposition_by_distinct_values_is_exact_and_pinned():
    # Two forecast values. f=0.2: outcomes 0,0,1 (freq 1/3); f=0.8: outcomes 1,1,0,1 (freq 3/4).
    p = [0.2, 0.2, 0.2, 0.8, 0.8, 0.8, 0.8]
    y = [0, 0, 1, 1, 1, 0, 1]
    d = stats.brier_decomposition(p, y)
    base = 4 / 7
    rel = (3 * (0.2 - 1 / 3) ** 2 + 4 * (0.8 - 0.75) ** 2) / 7
    res = (3 * (1 / 3 - base) ** 2 + 4 * (0.75 - base) ** 2) / 7
    assert d["reliability"] == pytest.approx(rel)
    assert d["resolution"] == pytest.approx(res)
    assert d["uncertainty"] == pytest.approx(base * (1 - base))
    assert d["within_bin"] == pytest.approx(0, abs=1e-12)
    assert d["brier"] == pytest.approx(rel - res + base * (1 - base))


def test_decomposition_with_coarse_bins_reports_the_within_bin_term():
    p = [0.1, 0.3, 0.6, 0.9]
    y = [0, 1, 1, 1]
    d = stats.brier_decomposition(p, y, edges=[0, 0.5, 1])
    assert d["within_bin"] != pytest.approx(0, abs=1e-6)
    assert d["brier"] == pytest.approx(d["reliability"] - d["resolution"] + d["uncertainty"] + d["within_bin"])


def test_wilson_pinned():
    lo, hi = stats.wilson_interval(8, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.4902, 0.9433)
    lo, hi = stats.wilson_interval(0, 20)
    assert lo == 0.0 and hi == pytest.approx(0.16113, abs=1e-5)
    with pytest.raises(ValueError):
        stats.wilson_interval(3, 2)


def test_reliability_table_bins_and_top_edge():
    p = [0.05, 0.15, 0.15, 1.0]
    y = [0, 1, 0, 1]
    rows = stats.reliability_table(p, y, [0, 0.1, 0.2, 1.0])
    assert [(r["lo"], r["n"], r["freq"]) for r in rows] == [(0, 1, 0.0), (0.1, 2, 0.5), (0.2, 1, 1.0)]
    assert rows[1]["mean_p"] == pytest.approx(0.15)
    assert (rows[1]["lo95"], rows[1]["hi95"]) == stats.wilson_interval(1, 2)


def test_bootstrap_is_seeded_and_cluster_aware():
    values = [1.0] * 10 + [0.0] * 10
    a = stats.bootstrap_ci(values, lambda s: sum(s) / len(s), seed=7, n_boot=500)
    b = stats.bootstrap_ci(values, lambda s: sum(s) / len(s), seed=7, n_boot=500)
    assert a == b
    assert a[0] < 0.5 < a[1]
    # Two clusters, each constant: resampling clusters gives only 0, 0.5 or 1.
    groups = ["d1"] * 10 + ["d2"] * 10
    lo, hi = stats.bootstrap_ci(values, lambda s: sum(s) / len(s), groups=groups, seed=1, n_boot=500)
    assert (lo, hi) == (0.0, 1.0)


def test_bootstrap_coverage_is_roughly_nominal():
    rng = random.Random(3)
    hits = 0
    for trial in range(60):
        xs = [rng.gauss(0, 1) for _ in range(80)]
        lo, hi = stats.bootstrap_ci(xs, lambda s: sum(s) / len(s), seed=trial, n_boot=400)
        hits += lo <= 0 <= hi
    assert 50 <= hits <= 60


def test_power_pinned():
    assert stats.n_for_effect(1.0, 0.1) == 785
    assert stats.n_for_effect(0.46, 0.02) == 4153
    assert stats.detectable_effect(1.0, 785) == pytest.approx(0.1, rel=1e-3)
    assert stats.power_at(1.0, 0.1, 785) == pytest.approx(0.8, abs=0.002)
    assert stats.power_at(1.0, 0.0, 100) == pytest.approx(0.025, abs=1e-6)


def test_z_test_p_pinned():
    assert stats.z_test_p(1.959964, 1.0) == pytest.approx(0.05, abs=1e-6)
    assert stats.z_test_p(-2.575829, 1.0) == pytest.approx(0.01, abs=1e-6)


def test_benjamini_hochberg_pinned_on_the_1995_example():
    p = [0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344, 0.0459,
         0.3240, 0.4262, 0.5719, 0.6528, 0.7590, 1.000]
    rejected, adjusted = stats.benjamini_hochberg(p, 0.05)
    assert rejected == [True] * 4 + [False] * 11
    assert adjusted[0] == pytest.approx(0.0015)
    assert adjusted[3] == pytest.approx(0.0095 * 15 / 4)
    assert all(a <= b + 1e-12 for a, b in zip(adjusted, adjusted[1:]))


def test_benjamini_hochberg_keeps_input_order_and_step_up():
    rejected, adjusted = stats.benjamini_hochberg([0.04, 0.01, 0.03, 0.005], 0.05)
    assert rejected == [True, True, True, True]
    assert adjusted == pytest.approx([0.04, 0.02, 0.04, 0.02])
