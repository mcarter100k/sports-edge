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
import re
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta, timezone
from pathlib import Path

from research import datasets, harness, stats
from sports_edge.timeutil import display_date, wall_time_to_utc

STUDY = "2026-10-kalshi-calibration"
SPORT_GROUPS = ("Football", "Basketball", "Hockey", "Baseball")
FAMILY = SPORT_GROUPS + ("Weather",)
SPORTS_OFFSET = timedelta(hours=4)       # decision = expected expiration - 4 h (about 1 h before the start)
EXPLORATORY_OFFSETS = (timedelta(hours=5), timedelta(hours=8))   # further from the start, never closer
# Weather decision: local midnight at the start of the measured date (the ticker's date), in the city's zone.
WEATHER_TZ = {"KXHIGHNY": "America/New_York", "KXHIGHCHI": "America/Chicago", "KXHIGHMIA": "America/New_York",
              "KXHIGHAUS": "America/Chicago", "KXHIGHLAX": "America/Los_Angeles", "KXHIGHDEN": "America/Denver",
              "KXHIGHPHIL": "America/New_York"}
MAX_AGE = timedelta(hours=6)
MAX_SPREAD = 0.10
# Timing guard: the market must close at least this long after the decision time.
MIN_CLOSE_GAP = {"Football": timedelta(hours=3.5), "Basketball": timedelta(hours=3),
                 "Hockey": timedelta(hours=3), "Baseball": timedelta(hours=3)}
SEED = 20261022
N_BOOT = 2000
ALPHA = 0.05
Q = 0.05
SD_ASSUMED = 0.50                        # the upper bound of sqrt(p(1 - p))
WEATHER_SD = 0.0709                      # from prices at t (pre-registration section 6)
DELTA = 0.02
FEE_AT_HALF = 0.07 * 0.25                # taker fee per contract at p = 0.5, multiplier assumed 1
FOLLOWUP = 0.0225                        # registered: FEE_AT_HALF + the pre-run median half-spread (0.0050)
MIN_UNITS = 20
EDGES_DOG = [0, .05, .10, .15, .20, .25, .30, .35, .40, .45, .50]
EDGES_ALL = [0, .05, .10, .20, .30, .40, .50, .60, .70, .80, .90, .95, 1.0]


# Decisions (markets table only: no outcomes) ------------------------------------------

TICKER_DATE = re.compile(r"^[^-]+-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})")
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def ticker_date(event_ticker):
    """The measured date in a weather event ticker (KXHIGHNY-26OCT21 -> 2026-10-21), or None."""
    m = TICKER_DATE.match(event_ticker or "")
    if not m:
        return None
    try:
        return date(2000 + int(m.group(1)), MONTHS.index(m.group(2)) + 1, int(m.group(3)))
    except ValueError:
        return None


def _utc(ts):
    """TIMESTAMP (naive UTC) -> aware UTC."""
    return None if ts is None else ts.replace(tzinfo=timezone.utc)


def markets(con):
    rows = con.execute("""
        SELECT ticker, event_ticker, series_ticker, grp, market_type, open_utc, close_utc, expected_expiration_utc
        FROM markets ORDER BY ticker""").fetchall()
    return [{"ticker": t, "event": e, "series": s, "grp": g, "market_type": mt, "open": _utc(o), "close": _utc(c),
             "expected": _utc(x)} for t, e, s, g, mt, o, c, x in rows]


def sports_decisions(ms, offset=SPORTS_OFFSET):
    """One market per two-market game: the smaller ticker. Returns (decisions, funnel, rule-4 exclusions)."""
    funnel = defaultdict(int)
    by_event = defaultdict(list)
    for m in ms:
        if m["grp"] in SPORT_GROUPS:
            by_event[m["event"]].append(m)
    funnel["games"] = len(by_event)
    out, excluded = [], []
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
        d = {"ticker": m["ticker"], "event": event, "grp": m["grp"], "t": t, "cluster": display_date(t)}
        if not (m["open"] <= t < m["close"]):
            funnel["excluded: decision time outside the market's open period"] += 1
            excluded.append(d)
            continue
        if m["close"] - t < MIN_CLOSE_GAP[m["grp"]]:
            # A game market closes when the winner is known; closing this soon after t means the
            # game may already have started at t.
            funnel["excluded: market closed too soon after the decision (game may have started)"] += 1
            excluded.append(d)
            continue
        out.append(d)
    funnel["games with a decision time"] = len(out)
    return out, funnel, excluded


def weather_decisions(ms):
    """t = local midnight at the start of the measured date (the ticker's date). close_utc is not read."""
    funnel = defaultdict(int)
    out, events = [], set()
    for m in ms:
        if m["grp"] != "Weather":
            continue
        events.add(m["event"])
        day = ticker_date(m["event"])
        if day is None:
            funnel["excluded markets: no measured date in the ticker"] += 1
            continue
        t = wall_time_to_utc(day, 0, WEATHER_TZ[m["series"]])
        if m["open"] > t:
            funnel["excluded markets: not yet listed at the decision time"] += 1
            continue
        out.append({"ticker": m["ticker"], "event": m["event"], "grp": "Weather", "t": t, "cluster": day.isoformat()})
    funnel["city-days"] = len(events)
    funnel["bracket markets with a decision time"] = len(out)
    return out, funnel


# Harness connection and prices -----------------------------------------------------------

def harness_con(folder, manifest, decisions, tmp):
    files = {t: folder / e["file"] for t, e in manifest["tables"].items()}
    units = tmp / "units.csv"
    with open(units, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "unit_id", "cluster"])
        seen = set()
        for d in sorted(decisions, key=lambda d: d["ticker"]):
            if d["ticker"] not in seen:
                seen.add(d["ticker"])
                w.writerow([d["ticker"], d["event"], d["cluster"]])
    return harness.build({
        "quotes": f"SELECT ticker, end_period_utc AS ts_utc, yes_bid_close AS yes_bid, yes_ask_close AS yes_ask "
                  f"FROM read_parquet('{files['candles']}')",
        "outcomes": f"SELECT ticker, CASE result WHEN 'yes' THEN 1 ELSE 0 END AS y "
                    f"FROM read_parquet('{files['outcomes']}') WHERE result IN ('yes', 'no')",
        "units": f"SELECT ticker, unit_id, cluster FROM read_csv('{units}', header=true, "
                 f"columns={{'ticker': 'VARCHAR', 'unit_id': 'VARCHAR', 'cluster': 'VARCHAR'}})",
    })


class FilteredMid:
    """The tested price: the mid of the latest two-sided quote within 6 h (one-sided quotes skipped), if its
    spread is at most 10 cents; else None. Rules 5 and 6 of the pre-registration."""
    name = "kalshi_mid(two-sided, max_age=6h, spread<=0.10)"

    def predict(self, con, ticker, t):
        q = harness.kalshi_quote(con, ticker, t, MAX_AGE)
        if q is None or q[1] - q[0] > MAX_SPREAD + 1e-9:
            return None
        return (q[0] + q[1]) / 2


def priced(con, decisions, funnel, label):
    """Attach p and y; drop and count what rules 5-7 exclude."""
    source = FilteredMid()
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
        out.append({**d, "p": source.predict(con, d["ticker"], d["t"]), "half_spread": (q[1] - q[0]) / 2,
                    "y": outcome[d["ticker"]]})
    return out


# Inference ----------------------------------------------------------------------------------

def cluster_test(values, clusters):
    """Mean, cluster-robust SE, z, two-sided p, cluster-bootstrap 95% interval. p is None if not testable."""
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    sums = defaultdict(float)
    for v, c in zip(values, clusters):
        sums[c] += v - m
    g = len(sums)
    se = math.sqrt(g / (g - 1) * sum(s * s for s in sums.values())) / n if g > 1 else None
    p = stats.z_test_p(m, se) if se else None
    lo, hi = stats.bootstrap_ci(values, lambda xs: sum(xs) / len(xs), groups=clusters, n_boot=N_BOOT, seed=SEED)
    return {"n": n, "clusters": g, "mean": m, "se": se, "p": p, "lo": lo, "hi": hi}


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
    return [(sum(r["y"] - r["p"] for r in by[e]) / len(by[e]), by[e][0]["cluster"], by[e][0]) for e in sorted(by)]


def exclusion_bound(n_in, mean, excluded_ps):
    """Largest shift of the mean if every rule-4-excluded game that has a tested price were added with the
    worst possible outcome: sum(1 - p_dog + |mean|) / (n_in + n_excluded)."""
    if not excluded_ps:
        return 0.0
    dogs = [min(p, 1 - p) for p in excluded_ps]
    return sum(1 - d + abs(mean) for d in dogs) / (n_in + len(dogs))


# Report --------------------------------------------------------------------------------------

def f(x, places=4, sign=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    s = f"{x:+.{places}f}" if sign else f"{x:.{places}f}"
    return s.replace("-0.0000", "+0.0000") if sign else s


def reliability_md(title, ps, ys, edges, n_label="n"):
    lines = [f"**{title}**", "",
             f"| bucket | {n_label} | mean price | observed | 95% interval (Wilson; unadjusted, not a test) |",
             "|---|---|---|---|---|"]
    for r in stats.reliability_table(ps, ys, edges):
        lines.append(f"| {r['lo']:.2f}–{r['hi']:.2f} | {r['n']} | {r['mean_p']:.4f} | {r['freq']:.4f} | "
                     f"[{r['lo95']:.4f}, {r['hi95']:.4f}] |")
    d = stats.brier_decomposition(ps, ys, edges)
    lines += ["", f"Brier {d['brier']:.5f} = reliability {d['reliability']:.5f} − resolution {d['resolution']:.5f} "
                  f"+ uncertainty {d['uncertainty']:.5f} + within-bin {d['within_bin']:+.5f}", ""]
    return lines


def score_md(title, r):
    lines = [f"**{title}** (harness score, {'registered' if r['registered'] else 'development'} run; "
             f"{r['scored_rows']} rows; leakage check on all {r['leakage_checked']} decisions passed)", ""]
    for k, label in (("source", "Kalshi mid (the source)"), ("kalshi_mid", "Kalshi mid baseline"),
                     ("pinnacle_fair", "Pinnacle fair baseline")):
        b = r["by"][k]
        lines.append(f"- {label}: " + (f"{b['units']} units, Brier {b['brier']:.5f}, log loss {b['log_loss']:.5f}"
                                         if b["available"] else b["note"]))
    return lines + [""]


def survives(res, bound):
    """A rejection is claimed only if the 95% interval, widened by the exclusion bound, excludes 0."""
    return res["lo"] - bound > 0 or res["hi"] + bound < 0


def claim(res, bound, rejected):
    """(claimed, reason) for a test that the z test (primary) or BH (secondary) did or did not reject."""
    if not rejected:
        return False, "not rejected"
    if survives(res, bound):
        return True, "claimed"
    if res["lo"] <= 0 <= res["hi"]:
        return False, "rejected by the z test, not claimed: the bootstrap interval itself includes 0"
    return False, "rejected, not claimed: the interval widened by the exclusion bound B includes 0"


def primary_verdict(res, bound):
    rejected = res["p"] is not None and res["p"] < ALPHA
    claimed, reason = claim(res, bound, rejected)
    if claimed:
        return "REJECT H0, claimed: the 95% interval widened by B excludes 0"
    if rejected:
        return f"H0 {reason}"
    return (f"DO NOT REJECT H0. A pooled underdog excess outside [{f(res['lo'] - bound)}, {f(res['hi'] + bound)}] "
            f"(the 95% interval widened by B) is ruled out; this does not show calibration bucket by bucket (see "
            f"the descriptive tables)")


def followup_met(res, bound):
    rejected = res["p"] is not None and res["p"] < ALPHA
    return claim(res, bound, rejected)[0] and abs(res["mean"]) >= FOLLOWUP


def run(folder, out_path):
    folder = Path(folder)
    manifest = datasets.verify(folder)
    con_ds = datasets.open_dataset(folder)
    ms = markets(con_ds)
    con_ds.close()
    sd, sfun, sex = sports_decisions(ms)
    wd, wfun = weather_decisions(ms)
    explore_sets = {off: sports_decisions(ms, off) for off in EXPLORATORY_OFFSETS}
    every = {(d["ticker"], d["t"]) for d in sd + wd + sex}
    for ed, _, _ in explore_sets.values():
        every |= {(d["ticker"], d["t"]) for d in ed}
    with tempfile.TemporaryDirectory() as tmp:
        con = harness_con(folder, manifest, sd + wd + sex + [d for ed, _, _ in explore_sets.values() for d in ed],
                          Path(tmp))
        # Every tested price, exploratory and bound prices included, must equal the same prediction made on a
        # separate connection holding only data at or before t, with no outcomes. Any difference stops the run.
        checked = harness.check_no_peeking(con, FilteredMid(), sorted(every), sample=None)
        srows = priced(con, sd, sfun, "games")
        wrows = priced(con, wd, wfun, "markets")
        excluded_ps = {}
        for d in sex:
            p = FilteredMid().predict(con, d["ticker"], d["t"])
            if p is not None:
                excluded_ps.setdefault(d["grp"], []).append(p)
        s_score = harness.score(con, [(d["ticker"], d["t"]) for d in srows], FilteredMid(), MAX_AGE,
                                seed=SEED, n_boot=N_BOOT)
        w_score = harness.score(con, [(d["ticker"], d["t"]) for d in wrows], FilteredMid(), MAX_AGE,
                                seed=SEED, n_boot=N_BOOT)
        explore = {}
        for off, (ed, efun, _) in explore_sets.items():
            u = sports_units(priced(con, ed, efun, "games"))
            explore[off] = cluster_test([v for v, _, _ in u], [c for _, c, _ in u])
        con.close()

    su = sports_units(srows)
    primary = cluster_test([v for v, _, _ in su], [c for _, c, _ in su])
    all_ex = [p for ps in excluded_ps.values() for p in ps]
    bound = {"pooled": exclusion_bound(primary["n"], primary["mean"], all_ex)}
    family = {}
    for g in SPORT_GROUPS:
        u = [x for x in su if x[2]["grp"] == g]
        family[g] = cluster_test([v for v, _, _ in u], [c for _, c, _ in u])
        bound[g] = exclusion_bound(family[g]["n"], family[g]["mean"], excluded_ps.get(g, [])) if family[g] else 0.0
    wu = weather_units(wrows)
    family["Weather"] = cluster_test([v for v, _, _ in wu], [c for _, c, _ in wu])
    bound["Weather"] = 0.0
    testable = {k: (r is not None and r["p"] is not None and r["n"] >= MIN_UNITS) for k, r in family.items()}
    # The family is always the registered five; an untestable group enters with p = 1 (INSUFFICIENT).
    rejected, adjusted = stats.benjamini_hochberg([family[k]["p"] if testable[k] else 1.0 for k in FAMILY], Q)
    bh = dict(zip(FAMILY, zip(rejected, adjusted)))
    half = statistics.median(r["half_spread"] for r in srows)

    n_need = stats.n_for_effect(SD_ASSUMED, DELTA, ALPHA, 0.8)
    L = [f"# Result: Kalshi calibration by price bucket and sport", "",
         f"Study `{STUDY}`. Written by `research/studies/kalshi_calibration.py`; never edited by hand.", "",
         f"- Dataset: `{manifest['name']}` id `{manifest['dataset_id']}`",
         f"- Tables: " + ", ".join(f"{t} {e['rows']} rows" for t, e in sorted(manifest["tables"].items())),
         f"- Leakage check (registered run): every tested price — {checked} decisions, including excluded and "
         f"exploratory ones — was also predicted on a separate connection holding only quotes at or before its "
         f"decision time, with no outcomes, and all were identical. This checks the quotes used at t, not the "
         f"choice of t; t comes from the timing rules (pre-registration section 3).", "",
         "## Inclusion", ""]
    for k, v in sorted(sfun.items()):
        L.append(f"- Sports: {k}: {v}")
    for k, v in sorted(wfun.items()):
        L.append(f"- Weather: {k}: {v}")
    L += [f"- Sports games scored: {len(srows)}; weather city-days with a bracket under 0.50 scored: {len(wu)} "
          f"({len(wrows)} bracket markets priced)",
          f"- Rule-4 exclusions with a tested price (used for the exclusion bound): "
          + ", ".join(f"{g} {len(excluded_ps.get(g, []))}" for g in SPORT_GROUPS), "",
          "## Primary test: pooled sports, underdog excess Δ = mean(y − p) on the underdog side", "",
          "Δ < 0 means underdogs win less often than their price says (favorite-longshot bias). "
          "Units are games; intervals resample Los Angeles dates of the decision.", "",
          "| group | units | dates | Δ | SE | 95% interval | p | exclusion bound B |",
          "|---|---|---|---|---|---|---|---|",
          f"| Sports (pooled) | {primary['n']} | {primary['clusters']} | {f(primary['mean'])} | "
          f"{f(primary['se'], sign=False)} | [{f(primary['lo'])}, {f(primary['hi'])}] | {f(primary['p'], sign=False)} "
          f"| {f(bound['pooled'], sign=False)} |", ""]
    verdict = primary_verdict(primary, bound["pooled"])
    L += [f"Decision: **{verdict}**.", "",
          f"Power (stated before the run): with sd {SD_ASSUMED}, |Δ| = {DELTA} needs {n_need} games; {primary['n']} "
          f"were scored, so the smallest effect detectable at 80% power is "
          f"{stats.detectable_effect(SD_ASSUMED, primary['n']):.4f}, before any design effect from clustering.", "",
          f"Follow-up threshold (registered before the run): |Δ| ≥ {FOLLOWUP:.4f} (fee at p = 0.5 with multiplier 1, "
          f"{FEE_AT_HALF:.4f}, plus the pre-run median half-spread, 0.0050). "
          + ("Met." if followup_met(primary, bound["pooled"]) else "Not met.")
          + f" Run-time median half-spread of the scored games, for reference: {half:.4f}.", "",
          "## Secondary family (Benjamini–Hochberg, q = 0.05, always five tests)", "",
          "| group | units | dates | Δ | SE | 95% interval | p | BH adjusted p | exclusion bound B | rejected and claimed |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for k in FAMILY:
        res = family[k]
        rej, adj = bh[k]
        if not testable[k]:
            n = res["n"] if res else 0
            cl = res["clusters"] if res else 0
            L.append(f"| {k} | {n} | {cl} | {f(res['mean']) if res else 'n/a'} | n/a | n/a | n/a | {f(adj, sign=False)} "
                     f"| n/a | INSUFFICIENT (not testable or N < 20) |")
            continue
        claimed, reason = claim(res, bound[k], rej)
        L.append(f"| {k} | {res['n']} | {res['clusters']} | {f(res['mean'])} | {f(res['se'], sign=False)} | "
                 f"[{f(res['lo'])}, {f(res['hi'])}] | {f(res['p'], sign=False)} | {f(adj, sign=False)} | "
                 f"{f(bound[k], sign=False)} | {'yes' if claimed else reason} |")
    L += ["",
          "Weather Δ is, per city-day, the mean of (y − p) over brackets priced under 0.50 at local midnight. It is "
          "driven mostly by whether the winning bracket is one of them: when it is, the value is (1 − Σp) / k. "
          "Weather clusters are measured dates." + (
              f" Weather power (stated before the run): sd {WEATHER_SD} from prices, so the smallest effect "
              f"detectable at 80% power with {family['Weather']['n']} city-days is "
              f"{stats.detectable_effect(WEATHER_SD, family['Weather']['n']):.4f}." if family["Weather"] else ""), "",
          "## Calibration tables (descriptive)", ""]
    if srows:
        L += reliability_md("Sports, underdog side (pooled)", [fold(r)[0] for r in srows], [fold(r)[1] for r in srows],
                            EDGES_DOG, "games")
        for g in SPORT_GROUPS:
            rs = [r for r in srows if r["grp"] == g]
            if rs:
                L += reliability_md(f"{g}, underdog side", [fold(r)[0] for r in rs], [fold(r)[1] for r in rs],
                                    EDGES_DOG, "games")
    if wrows:
        L += reliability_md("Weather, every bracket", [r["p"] for r in wrows], [r["y"] for r in wrows], EDGES_ALL,
                            "brackets (not independent)")
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
          "a taker pays half the spread and a fee of 0.07 × p × (1 − p) per contract (multiplier assumed 1), "
          "so a miscalibration smaller than those costs cannot be traded.",
          "- No result here is an edge. A claimed rejection is a hypothesis for a prospective, fee-adjusted shadow "
          "test on data after this dataset.", ""]
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
