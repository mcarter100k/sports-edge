"""Match coverage per Kalshi series (docs/specs/research_spec.md). Read-only; report only.

    python -m research.studies.match_coverage [--mismatches N]

A series counts as in scope once any of its games has paired with a
reference game, which also tells us its reference sport key. For those
series: games matched, games never matched by their latest reason, and the
match rate. Name mismatches are listed with the reference games that were in
the time window, so an alias can be checked by eye. An alias is never
inferred here.
"""
import argparse
import sys
from collections import defaultdict
from datetime import timedelta

from sports_edge.store import Store, from_db_ts
from sports_edge.timeutil import iso_z

WINDOW_BEFORE = timedelta(hours=8)       # the matching rule's time window (docs/specs/odds_matching_spec.md)


def coverage(con):
    series_key = dict(con.execute("""
        SELECT m.series_ticker, min(o.sport_key) FROM odds_matches o JOIN markets m ON m.ticker = o.market_ticker
        GROUP BY 1""").fetchall())
    matched = defaultdict(set)
    for series, event in con.execute("""
            SELECT DISTINCT m.series_ticker, o.event_ticker FROM odds_matches o
            JOIN markets m ON m.ticker = o.market_ticker""").fetchall():
        matched[series].add(event)
    latest = con.execute("""
        SELECT u.event_ticker, u.sport, u.reason, u.detail, s.series_ticker
        FROM unmatched u
        JOIN (SELECT event_ticker, max(ts_utc) AS t FROM unmatched GROUP BY 1) l
          ON l.event_ticker = u.event_ticker AND l.t = u.ts_utc
        LEFT JOIN (SELECT DISTINCT event_ticker, series_ticker FROM markets) s ON s.event_ticker = u.event_ticker
        WHERE u.event_ticker NOT LIKE 'ref:%'""").fetchall()
    unmatched = defaultdict(lambda: defaultdict(set))
    out_of_scope = defaultdict(set)
    details = []
    all_matched = set().union(*matched.values()) if matched else set()
    for event, sport, reason, detail, series in latest:
        if event in all_matched:
            continue
        if series in series_key:
            unmatched[series][reason].add(event)
            if reason == "name_mismatch":
                details.append((series, event, detail))
        else:
            out_of_scope[(sport, series or "?")].add(event)
    ref_orphans = dict(con.execute("""
        SELECT sport, count(DISTINCT event_ticker) FROM unmatched WHERE event_ticker LIKE 'ref:%' GROUP BY 1
        ORDER BY 1""").fetchall())
    return series_key, matched, unmatched, out_of_scope, details, ref_orphans


def candidates_for(con, sport_key, event):
    """Reference games in the matching window of a Kalshi event, from the stored listings."""
    row = con.execute("""
        SELECT min(least(coalesce(expected_expiration_time, close_time), close_time)) FROM markets
        WHERE event_ticker = ?""", [event]).fetchone()
    if not row or row[0] is None:
        return []
    resolve_by = row[0]
    return con.execute("""
        SELECT DISTINCT home, away, commence_utc FROM reference_events
        WHERE sport_key = ? AND commence_utc BETWEEN ? AND ? ORDER BY commence_utc, home""",
                       [sport_key, resolve_by - WINDOW_BEFORE, resolve_by]).fetchall()


def report(con, mismatches=40):
    series_key, matched, unmatched, out_of_scope, details, ref_orphans = coverage(con)
    lines = ["MATCH COVERAGE (series that have paired at least once)", ""]
    lines.append(f"{'series':22s} {'reference key':32s} {'matched':>7s} {'unmatched':>9s} {'rate':>6s}  reasons")
    tot_m = tot_u = 0
    for series in sorted(series_key):
        m = len(matched[series])
        reasons = {r: len(e) for r, e in sorted(unmatched[series].items())}
        u = sum(reasons.values())
        tot_m, tot_u = tot_m + m, tot_u + u
        rate = f"{m / (m + u):.0%}" if m + u else "-"
        lines.append(f"{series:22s} {series_key[series]:32s} {m:7d} {u:9d} {rate:>6s}  "
                     + (", ".join(f"{r}={n}" for r, n in reasons.items()) or "-"))
    rate = f"{tot_m / (tot_m + tot_u):.0%}" if tot_m + tot_u else "-"
    lines.append(f"{'TOTAL':22s} {'':32s} {tot_m:7d} {tot_u:9d} {rate:>6s}")
    lines += ["", "Kalshi games in series that have never paired (no reference key known), by sport:"]
    by_sport = defaultdict(int)
    for (sport, _series), events in out_of_scope.items():
        by_sport[sport] += len(events)
    lines += [f"  {s}: {n}" for s, n in sorted(by_sport.items())] or ["  none"]
    lines += ["", "Reference games with no Kalshi game, by reference sport key:"]
    lines += [f"  {k}: {n}" for k, n in ref_orphans.items()] or ["  none"]
    lines += ["", f"NAME MISMATCHES in paired series (first {mismatches}, by series):"]
    for series, event, detail in sorted(details)[:mismatches]:
        lines.append(f"  {event}  kalshi {detail}")
        for home, away, commence in candidates_for(con, series_key[series], event):
            lines.append(f"      reference in window: {home} vs {away} at {iso_z(from_db_ts(commence))}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="research.studies.match_coverage")
    ap.add_argument("--mismatches", type=int, default=40)
    args = ap.parse_args(argv)
    with Store.open(read_only=True) as store:
        print(report(store.con, args.mismatches))
    return 0


if __name__ == "__main__":
    sys.exit(main())
