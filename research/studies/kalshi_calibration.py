"""Kalshi calibration by price bucket and sport: a favorite-longshot check.

Pre-registration: research/registry/2026-10-kalshi-calibration/prereg.md. This
script does exactly what that file says; any change is a new study.

    python -m research.studies.kalshi_calibration --dataset <frozen dataset dir> --out <result.md>

The same dataset id gives byte-identical output.
"""
import argparse
import csv
import hashlib
import math
import sys
import tempfile
from collections import defaultdict
from datetime import timedelta, timezone
from pathlib import Path

from research import datasets, harness, stats
from sports_edge.timeutil import display_date

STUDY = "2026-10-kalshi-calibration"
SPORT_GROUPS = ("Football", "Basketball", "Hockey", "Baseball")
SPORTS_OFFSET = timedelta(hours=4)       # decision = expected expiration - 4 h (about 1 h before the start)
WEATHER_OFFSET = timedelta(hours=24)     # decision = close - 24 h (start of the measured day)
EXPLORATORY_OFFSETS = (timedelta(hours=3), timedelta(hours=6))
MAX_AGE = timedelta(hours=6)
MAX_SPREAD = 0.10
SEED = 20261022
N_BOOT = 2000
ALPHA = 0.05
Q = 0.05
SD_ASSUMED = 0.45
DELTA = 0.02
LEAK_SAMPLE = 200
MIN_UNITS = 20
EDGES_DOG = [0, .05, .10, .15, .20, .25, .30, .35, .40, .45, .50]
EDGES_ALL = [0, .05, .10, .20, .30, .40, .50, .60, .70, .80, .90, .95, 1.0]


# Decisions (markets table only: no outcomes) ------------------------------------------

def _utc(ts):
    """TIMESTAMP (naive UTC) -> aware UTC."""
    return None if ts is None else ts.replace(tzinfo=timezone.utc)


def markets(con):
    rows = con.execute("""
        SELECT ticker, event_ticker, grp, market_type, open_utc, close_utc, expected_expiration_utc
        FROM markets ORDER BY ticker""").fetchall()
    out = []
    for t, e, g, mt, o, c, x in rows:
        out.append({"ticker": t, "event": e, "grp": g, "market_type": mt, "open": _utc(o), "close": _utc(c),
                    "expected": _utc(x)})
    return out


def sports_decisions(ms, offset=SPORTS_OFFSET):
    """One market per two-market game: the smaller ticker. Returns (decisions, funnel)."""
    funnel = defaultdict(int)
    by_event = defaultdict(list)
    for m in ms:
        if m["grp"] in SPORT_GROUPS:
            by_event[m["event"]].append(m)
    funnel["games"] = len(by_event)
    out = []
    for event in sorted(by_event):
        group = by_event[event]
        if len(group) != 2 or any(m["market_type"] != "binary" for m in group):
            funnel["excluded: not a two-market binary game"] += 1
            continue
        m = min(group, key=lambda m: m["ticker"])
        if m["expected"] is None:
            funnel["excluded: no expected expiration"] += 1
            continue
        t = m["expected"] - offset
        if not (m["open"] <= t < m["close"]):
            funnel["excluded: decision time outside the market's open period"] += 1
            continue
        out.append({"ticker": m["ticker"], "event": event, "grp": m["grp"], "t": t})
    funnel["games with a decision time"] = len(out)
    return out, funnel


def weather_decisions(ms):
    funnel = defaultdict(int)
    out = []
    events = set()
    for m in ms:
        if m["grp"] != "Weather":
            continue
        events.add(m["event"])
        t = m["close"] - WEATHER_OFFSET
        if not (m["open"] <= t < m["close"]):
            funnel["excluded markets: decision time outside the market's open period"] += 1
            continue
        out.append({"ticker": m["ticker"], "event": m["event"], "grp": "Weather", "t": t})
    funnel["city-days"] = len(events)
    funnel["bracket markets with a decision time"] = len(out)
    return out, funnel


# Harness connection ----------------------------------------------------------------------

def harness_con(folder, manifest, decisions, tmp):
    files = {t: folder / e["file"] for t, e in manifest["tables"].items()}
    units = tmp / "units.csv"
    with open(units, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "unit_id", "cluster"])
        for d in sorted(decisions, key=lambda d: d["ticker"]):
            w.writerow([d["ticker"], d["event"], display_date(d["t"])])
    return harness.build({
        "quotes": f"SELECT ticker, end_period_utc AS ts_utc, yes_bid_close AS yes_bid, yes_ask_close AS yes_ask "
                  f"FROM read_parquet('{files['candles']}')",
        "outcomes": f"SELECT ticker, CASE result WHEN 'yes' THEN 1 ELSE 0 END AS y "
                    f"FROM read_parquet('{files['outcomes']}') WHERE result IN ('yes', 'no')",
        "units": f"SELECT ticker, unit_id, cluster FROM read_csv('{units}', header=true, "
                 f"columns={{'ticker': 'VARCHAR', 'unit_id': 'VARCHAR', 'cluster': 'VARCHAR'}})",
    })


def priced(con, decisions, funnel, label):
    """Attach p (mid), y; drop and count what the pre-registration excludes."""
    outcome = dict(con.execute("SELECT ticker, y FROM outcomes").fetchall())
    out = []
    for d in decisions:
        q = harness.kalshi_quote(con, d["ticker"], d["t"], MAX_AGE)
        if q is None:
            funnel[f"excluded {label}: no two-sided quote within 6 h"] += 1
            continue
        if q[1] - q[0] > MAX_SPREAD + 1e-9:
            funnel[f"excluded {label}: spread over 10 cents"] += 1
            continue
        if d["ticker"] not in outcome:
            funnel[f"excluded {label}: not settled yes or no"] += 1
            continue
        out.append({**d, "p": (q[0] + q[1]) / 2, "y": outcome[d["ticker"]], "cluster": display_date(d["t"])})
    return out


# Inference ----------------------------------------------------------------------------------

def cluster_test(values, clusters):
    """Mean, cluster-robust SE, z, two-sided p, cluster-bootstrap 95% interval."""
    n = len(values)
    m = sum(values) / n
    sums = defaultdict(float)
    for v, c in zip(values, clusters):
        sums[c] += v - m
    g = len(sums)
    se = math.sqrt(g / (g - 1) * sum(s * s for s in sums.values())) / n if g > 1 else float("nan")
    p = stats.z_test_p(m, se) if se and se > 0 else float("nan")
    lo, hi = stats.bootstrap_ci(values, lambda xs: sum(xs) / len(xs), groups=clusters, n_boot=N_BOOT, seed=SEED)
    return {"n": n, "clusters": g, "mean": m, "se": se, "z": m / se if se else float("nan"), "p": p,
            "lo": lo, "hi": hi}


def fold(r):
    return (r["p"], r["y"]) if r["p"] <= 0.5 else (1 - r["p"], 1 - r["y"])


def sports_units(rows):
    """One row per game already; value = y_dog - p_dog."""
    return [(fold(r)[1] - fold(r)[0], r["cluster"], r) for r in rows]


def weather_units(rows):
    by = defaultdict(list)
    for r in rows:
        if r["p"] < 0.5:
            by[r["event"]].append(r)
    out = []
    for event in sorted(by):
        rs = by[event]
        out.append((sum(r["y"] - r["p"] for r in rs) / len(rs), rs[0]["cluster"], rs[0]))
    return out


# Report --------------------------------------------------------------------------------------

def f(x, places=4, sign=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    s = f"{x:+.{places}f}" if sign else f"{x:.{places}f}"
    return s.replace("-0.0000", "+0.0000") if sign else s


def test_row(name, res):
    flag = " INSUFFICIENT (N < 20)" if res["n"] < MIN_UNITS else ""
    return (f"| {name} | {res['n']} | {res['clusters']} | {f(res['mean'])} | {f(res['se'], sign=False)} | "
            f"[{f(res['lo'])}, {f(res['hi'])}] | {f(res['p'], sign=False)} |{flag}")


def reliability_md(title, ps, ys, edges):
    lines = [f"**{title}**", "", "| bucket | n | mean price | observed | 95% interval (Wilson) |", "|---|---|---|---|---|"]
    for r in stats.reliability_table(ps, ys, edges):
        lines.append(f"| {r['lo']:.2f}–{r['hi']:.2f} | {r['n']} | {r['mean_p']:.4f} | {r['freq']:.4f} | "
                     f"[{r['lo95']:.4f}, {r['hi95']:.4f}] |")
    d = stats.brier_decomposition(ps, ys, edges)
    lines += ["", f"Brier {d['brier']:.5f} = reliability {d['reliability']:.5f} − resolution {d['resolution']:.5f} "
                  f"+ uncertainty {d['uncertainty']:.5f} + within-bin {d['within_bin']:+.5f}", ""]
    return lines


def score_md(title, r):
    lines = [f"**{title}** (harness score; {r['scored_rows']} rows, leakage check on {r['leakage_checked']} "
             f"decisions passed)", ""]
    for k, label in (("source", "Kalshi mid (the source)"), ("kalshi_mid", "Kalshi mid baseline"),
                     ("pinnacle_fair", "Pinnacle fair baseline")):
        b = r["by"][k]
        lines.append(f"- {label}: " + (f"{b['units']} units, Brier {b['brier']:.5f}, log loss {b['log_loss']:.5f}"
                                         if b["available"] else b["note"]))
    return lines + [""]


def run(folder, out_path):
    folder = Path(folder)
    manifest = datasets.verify(folder)
    con_ds = datasets.open_dataset(folder)
    ms = markets(con_ds)
    con_ds.close()
    sd, sfun = sports_decisions(ms)
    wd, wfun = weather_decisions(ms)
    with tempfile.TemporaryDirectory() as tmp:
        con = harness_con(folder, manifest, sd + wd, Path(tmp))
        leak = harness.check_no_peeking(con, harness.KalshiMidSource(MAX_AGE),
                                        [(d["ticker"], d["t"]) for d in sd + wd], sample=LEAK_SAMPLE, seed=SEED)
        srows = priced(con, sd, sfun, "games")
        wrows = priced(con, wd, wfun, "markets")
        s_score = harness.score(con, [(d["ticker"], d["t"]) for d in srows], harness.KalshiMidSource(MAX_AGE),
                                MAX_AGE, seed=SEED, n_boot=N_BOOT)
        w_score = harness.score(con, [(d["ticker"], d["t"]) for d in wrows], harness.KalshiMidSource(MAX_AGE),
                                MAX_AGE, seed=SEED, n_boot=N_BOOT)
        explore = {}
        for off in EXPLORATORY_OFFSETS:
            ed, efun = sports_decisions(ms, off)
            erows = priced(con, ed, efun, "games")
            u = sports_units(erows)
            explore[off] = cluster_test([v for v, _, _ in u], [c for _, c, _ in u]) if len(u) > 1 else None
        con.close()

    su = sports_units(srows)
    primary = cluster_test([v for v, _, _ in su], [c for _, c, _ in su])
    family = {}
    for g in SPORT_GROUPS:
        u = [x for x in su if x[2]["grp"] == g]
        family[g] = cluster_test([v for v, _, _ in u], [c for _, c, _ in u]) if len(u) > 1 else None
    wu = weather_units(wrows)
    family["Weather"] = cluster_test([v for v, _, _ in wu], [c for _, c, _ in wu]) if len(wu) > 1 else None
    names = [k for k in family if family[k] is not None]
    rejected, adjusted = stats.benjamini_hochberg([family[k]["p"] for k in names], Q)
    bh = dict(zip(names, zip(rejected, adjusted)))

    n_need = stats.n_for_effect(SD_ASSUMED, DELTA, ALPHA, 0.8)
    L = [f"# Result: Kalshi calibration by price bucket and sport", "",
         f"Study `{STUDY}`. Written by `research/studies/kalshi_calibration.py`; never edited by hand.", "",
         f"- Dataset: `{manifest['name']}` id `{manifest['dataset_id']}`",
         f"- Tables: " + ", ".join(f"{t} {e['rows']} rows" for t, e in sorted(manifest["tables"].items())),
         f"- Leakage test (poisoned and truncated data after each decision time): passed on {leak} sampled "
         f"decisions, and again inside each harness score.", "",
         "## Inclusion", ""]
    for k, v in sorted(sfun.items()):
        L.append(f"- Sports: {k}: {v}")
    for k, v in sorted(wfun.items()):
        L.append(f"- Weather: {k}: {v}")
    L += [f"- Sports games scored: {len(srows)}; weather city-days with a longshot bracket scored: {len(wu)} "
          f"({len(wrows)} bracket markets priced)", "",
          "## Primary test: pooled sports, underdog excess Δ = mean(y − p) on the underdog side", "",
          "Δ < 0 means underdogs win less often than their price says (favorite-longshot bias). "
          "Units are games; intervals resample Los Angeles dates.", "",
          "| group | units | dates | Δ | SE | 95% interval | p |", "|---|---|---|---|---|---|---|",
          test_row("Sports (pooled)", primary), ""]
    verdict = ("REJECT H0 (prices are not calibrated on the underdog side)" if primary["p"] < ALPHA
               else "DO NOT REJECT H0")
    L += [f"Decision at α = {ALPHA}: **{verdict}**.", "",
          f"Power: with the assumed sd {SD_ASSUMED}, detecting |Δ| = {DELTA} needs {n_need} games; "
          f"{primary['n']} were scored, so the smallest effect detectable at 80% power is "
          f"{stats.detectable_effect(SD_ASSUMED, primary['n']):.4f}.", "",
          "## Secondary family (Benjamini–Hochberg, q = 0.05)", "",
          "| group | units | dates | Δ | SE | 95% interval | p | BH adjusted p | rejected |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k in list(SPORT_GROUPS) + ["Weather"]:
        res = family[k]
        if res is None:
            L.append(f"| {k} | 0 | 0 | n/a | n/a | n/a | n/a | n/a | INSUFFICIENT (N < 20) |")
            continue
        rej, adj = bh[k]
        flag = "INSUFFICIENT (N < 20)" if res["n"] < MIN_UNITS else ("yes" if rej else "no")
        L.append(test_row(k, res).rsplit(" |", 1)[0] + f" | {f(adj, sign=False)} | {flag} |")
    L += ["", "Weather Δ is, per city-day, the mean of (y − p) over brackets priced under 0.50.", "",
          "## Calibration tables (descriptive)", ""]
    sp = [fold(r)[0] for r in srows]
    sy = [fold(r)[1] for r in srows]
    if srows:
        L += reliability_md("Sports, underdog side (pooled)", sp, sy, EDGES_DOG)
        for g in SPORT_GROUPS:
            rs = [r for r in srows if r["grp"] == g]
            if rs:
                L += reliability_md(f"{g}, underdog side", [fold(r)[0] for r in rs], [fold(r)[1] for r in rs],
                                    EDGES_DOG)
    if wrows:
        L += reliability_md("Weather, every bracket", [r["p"] for r in wrows], [r["y"] for r in wrows], EDGES_ALL)
    L += ["## Harness scores against the baselines", ""]
    L += score_md("Sports games", s_score)
    L += score_md("Weather brackets", w_score)
    L += ["## Exploratory (not in any test family)", ""]
    for off, res in explore.items():
        hours = int(off.total_seconds() // 3600)
        L.append(f"- Sports with the decision {hours} h before expected expiration: " +
                 (f"Δ {f(res['mean'])}, 95% interval [{f(res['lo'])}, {f(res['hi'])}], {res['n']} games"
                  if res else "no games"))
    L += ["", "## What this does and does not show", "",
          "- This measures calibration of Kalshi's mid price at one decision time. It is not a trading result: "
          "a taker pays the spread and a fee of 0.07 × p × (1 − p) per contract (about 1.3 cents at p = 0.25), "
          "so a miscalibration smaller than those costs cannot be traded.",
          "- No result here is an edge. A rejected H0 is a hypothesis for a prospective, fee-adjusted shadow test "
          "on data after this dataset.", ""]
    text = "\n".join(L)
    Path(out_path).write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="research.studies.kalshi_calibration")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    digest = run(args.dataset, args.out)
    print(f"STUDY OK {STUDY} out={args.out} sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
