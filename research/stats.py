"""Scoring and inference for studies (docs/specs/research_spec.md).

Standard library only. Every function is pinned by tests.
"""
import math
import random
from statistics import NormalDist

_N = NormalDist()


def _pairs(p, y):
    p, y = list(p), list(y)
    if len(p) != len(y):
        raise ValueError(f"{len(p)} forecasts but {len(y)} outcomes")
    if not p:
        raise ValueError("no forecasts")
    for q, o in zip(p, y):
        if not (0.0 <= q <= 1.0):
            raise ValueError(f"forecast {q} outside [0, 1]")
        if o not in (0, 1):
            raise ValueError(f"outcome {o!r} is not 0 or 1")
    return p, y


def brier(p, y):
    p, y = _pairs(p, y)
    return sum((q - o) ** 2 for q, o in zip(p, y)) / len(p)


def log_loss(p, y, clip=1e-6):
    p, y = _pairs(p, y)
    total = 0.0
    for q, o in zip(p, y):
        q = min(max(q, clip), 1 - clip)
        total -= o * math.log(q) + (1 - o) * math.log(1 - q)
    return total / len(p)


def _bin_index(q, edges):
    """Bins are [e0, e1), [e1, e2), ..., [e_{k-1}, e_k]; the last bin includes its top edge."""
    if q < edges[0] or q > edges[-1]:
        raise ValueError(f"forecast {q} outside the bin edges")
    for i in range(len(edges) - 1):
        if q < edges[i + 1]:
            return i
    return len(edges) - 2


def _groups(p, y, edges):
    groups = {}
    for q, o in zip(p, y):
        key = q if edges is None else _bin_index(q, edges)
        groups.setdefault(key, []).append((q, o))
    return groups


def brier_decomposition(p, y, edges=None):
    """Murphy decomposition. Bins are `edges`, or each distinct forecast value when None.

    Returns brier, reliability, resolution, uncertainty and within_bin =
    brier - (reliability - resolution + uncertainty), which is 0 for distinct-value bins.
    """
    p, y = _pairs(p, y)
    n = len(p)
    base = sum(y) / n
    rel = res = 0.0
    for members in _groups(p, y, edges).values():
        nk = len(members)
        fk = sum(q for q, _ in members) / nk
        ok = sum(o for _, o in members) / nk
        rel += nk * (fk - ok) ** 2
        res += nk * (ok - base) ** 2
    rel, res, unc = rel / n, res / n, base * (1 - base)
    b = brier(p, y)
    return {"brier": b, "reliability": rel, "resolution": res, "uncertainty": unc,
            "within_bin": b - (rel - res + unc)}


def wilson_interval(k, n, conf=0.95):
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= k <= n:
        raise ValueError(f"k={k} outside [0, {n}]")
    z = _N.inv_cdf(1 - (1 - conf) / 2)
    phat = k / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def reliability_table(p, y, edges):
    """One row per bin with forecasts: lo, hi, n, mean_p, freq, lo95, hi95 (Wilson)."""
    p, y = _pairs(p, y)
    rows = []
    groups = _groups(p, y, edges)
    for i in range(len(edges) - 1):
        members = groups.get(i)
        if not members:
            continue
        n = len(members)
        k = sum(o for _, o in members)
        lo95, hi95 = wilson_interval(k, n)
        rows.append({"lo": edges[i], "hi": edges[i + 1], "n": n,
                     "mean_p": sum(q for q, _ in members) / n, "freq": k / n, "lo95": lo95, "hi95": hi95})
    return rows


def bootstrap_ci(values, stat, groups=None, n_boot=2000, seed=0, conf=0.95):
    """Percentile interval of stat(sample). With groups (one label per value), whole groups are
    resampled with replacement. Seeded: the same input gives the same interval."""
    values = list(values)
    if not values:
        raise ValueError("no values")
    rng = random.Random(seed)
    if groups is None:
        clusters = [[v] for v in values]
    else:
        groups = list(groups)
        if len(groups) != len(values):
            raise ValueError("one group label per value")
        by = {}
        for g, v in zip(groups, values):
            by.setdefault(g, []).append(v)
        clusters = [by[g] for g in sorted(by)]
    k = len(clusters)
    stats = []
    for _ in range(n_boot):
        sample = []
        for _ in range(k):
            sample.extend(clusters[rng.randrange(k)])
        stats.append(stat(sample))
    stats.sort()
    alpha = (1 - conf) / 2
    lo = stats[int(math.floor(alpha * n_boot))]
    hi = stats[min(n_boot - 1, int(math.ceil((1 - alpha) * n_boot)) - 1)]
    return lo, hi


def _z(alpha, power):
    return _N.inv_cdf(1 - alpha / 2) + _N.inv_cdf(power)


def n_for_effect(sd, delta, alpha=0.05, power=0.8):
    """Sample size for a two-sided z test of a mean against 0."""
    if sd <= 0 or delta == 0:
        raise ValueError("sd must be positive and delta nonzero")
    return math.ceil((_z(alpha, power) * sd / abs(delta)) ** 2)


def detectable_effect(sd, n, alpha=0.05, power=0.8):
    if sd <= 0 or n <= 0:
        raise ValueError("sd and n must be positive")
    return _z(alpha, power) * sd / math.sqrt(n)


def power_at(sd, delta, n, alpha=0.05):
    if sd <= 0 or n <= 0:
        raise ValueError("sd and n must be positive")
    return _N.cdf(abs(delta) * math.sqrt(n) / sd - _N.inv_cdf(1 - alpha / 2))


def z_test_p(mean, se):
    if se <= 0:
        raise ValueError("se must be positive")
    return 2 * (1 - _N.cdf(abs(mean) / se))


def benjamini_hochberg(pvalues, q=0.05):
    """(rejected, adjusted) in input order (Benjamini & Hochberg 1995, step-up)."""
    p = list(pvalues)
    m = len(p)
    if m == 0:
        return [], []
    if any(not (0 <= x <= 1) for x in p):
        raise ValueError("p values must be in [0, 1]")
    order = sorted(range(m), key=lambda i: p[i])
    k_max = 0
    for rank, i in enumerate(order, start=1):
        if p[i] <= rank * q / m:
            k_max = rank
    rejected = [False] * m
    for rank, i in enumerate(order, start=1):
        rejected[i] = rank <= k_max
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, p[i] * m / rank)
        adjusted[i] = running
    return rejected, adjusted
