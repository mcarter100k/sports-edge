"""Post-result descriptive figures for 2026-10-kalshi-calibration (result_note.md). Report only; not a test.

    python -m research.studies.kalshi_calibration_postresult --dataset <frozen dataset dir>

Computed after the result was read. Nothing here is evidence for an edge.
"""
import argparse
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from research import datasets, harness
from research.studies import kalshi_calibration as k

EDGES = [0, .05, .10, .20, .30, .40, .50]


def fee(p):
    """Taker fee per contract at multiplier 1 (the multiplier for these series is not in a cited table)."""
    return 0.07 * p * (1 - p)


def run(folder, out=print):
    folder = Path(folder)
    manifest = datasets.verify(folder)
    con_ds = datasets.open_dataset(folder)
    ms = k.markets(con_ds)
    con_ds.close()
    close = {m["ticker"]: m["close"] for m in ms}
    sd, _, _ = k.sports_decisions(ms)
    wd, _ = k.weather_decisions(ms)
    with tempfile.TemporaryDirectory() as tmp:
        con = k.harness_con(folder, manifest, sd + wd, Path(tmp))
        no_quote = [d for d in sd if harness.kalshi_quote(con, d["ticker"], d["t"], k.MAX_AGE) is None]
        for label, gap in (("> 14 d", timedelta(days=14)), ("> 14 d - 6 h", timedelta(days=14, hours=-6))):
            far = [d for d in no_quote if close[d["ticker"]] - d["t"] > gap]
            out(f"rule-5 sports exclusions {len(no_quote)}; market closed {label} after t: {len(far)} "
                f"{dict(sorted(Counter(d['grp'] for d in far).items()))}")
        rows = k.priced(con, wd, defaultdict(int), "markets")
        con.close()
    sub = [r for r in rows if r["p"] < 0.5]
    out(f"weather brackets under 0.50: {len(sub)}; per bracket: mean (y - mid) "
        f"{statistics.fmean(r['y'] - r['p'] for r in sub):+.4f}, mean (y - bid) "
        f"{statistics.fmean(r['y'] - (r['p'] - r['half_spread']) for r in sub):+.4f}")
    out("bucket | n | sum(y - mid) | sum(y - bid) | median half-spread | share bid 0.01 | seller at bid after fee, per bracket")
    for lo, hi in zip(EDGES, EDGES[1:]):
        b = [r for r in sub if lo <= r["p"] < hi]
        if not b:
            continue
        bid = [r["p"] - r["half_spread"] for r in b]
        seller = statistics.fmean((bd - r["y"]) - fee(bd) for r, bd in zip(b, bid))
        out(f"{lo:.2f}-{hi:.2f} | {len(b)} | {sum(r['y'] - r['p'] for r in b):+.1f} | "
            f"{sum(r['y'] - bd for r, bd in zip(b, bid)):+.1f} | {statistics.median(r['half_spread'] for r in b):.4f} | "
            f"{sum(1 for bd in bid if abs(bd - 0.01) < 1e-9) / len(b):.2f} | {seller:+.4f}")
    for name, sel in (("all under 0.50", sub), ("under 0.30 (post-hoc slice)", [r for r in sub if r["p"] < 0.30])):
        bid = [r["p"] - r["half_spread"] for r in sel]
        before = statistics.fmean(bd - r["y"] for r, bd in zip(sel, bid))
        after = statistics.fmean((bd - r["y"]) - fee(bd) for r, bd in zip(sel, bid))
        out(f"seller at the bid, {name}: {before:+.4f} per bracket before fees, {after:+.4f} after the fee at "
            f"multiplier 1 ({len(sel)} brackets)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="research.studies.kalshi_calibration_postresult")
    ap.add_argument("--dataset", required=True)
    run(ap.parse_args(argv).dataset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
