"""The board: each game's latest check, its price gaps and its near misses
(docs/specs/slate_alerts_spec.md, "the board"). Reads only; writes nothing.

A near miss is GAP_v2's own arithmetic with the bar at 0 and no book walk. It is
never written, numbered, alerted or graded.
"""
from datetime import timedelta
from decimal import Decimal

from . import bets, checks
from .fees import FeeScheduleUnknown, load_table, resolve_multipliers, table_precision
from .matching import check_outcomes
from .shadow import evaluate, latest_reference, row_id, rule_id, rule_settings
from .store import from_db_ts
from .timeutil import iso_z, parse_utc

LOOKBACK = timedelta(hours=3)      # a check runs at most 60 min before its slot
NEAR_MAX = 5


def latest_checks(store, source, now, starts):
    """{source_event_id: check_log row} for games not yet started whose newest check that is not
    superseded has status checked."""
    latest = {}
    for grp in checks.recent_checks(store, source, now - LOOKBACK).values():
        for r in grp:
            if r["status"] != "superseded":
                prev = latest.get(r["source_event_id"])
                if prev is None or r["ts_utc"] >= prev["ts_utc"]:
                    latest[r["source_event_id"]] = r
    return {e: r for e, r in latest.items() if r["status"] == "checked"
            and (starts.get(e) is None or starts[e] > now)}


def postponed(eid, start_at_check, starts):
    later = starts.get(eid)
    return later is not None and later - start_at_check >= bets.POSTPONED_AFTER


def current_gaps(ledger_rows, latest, outcome_of, now, starts):
    """One row per result (the cheaper) written by each game's latest check, biggest gap first."""
    groups = {}
    for r in ledger_rows:
        check = latest.get(r["source_event_id"])
        if check is None or r["run_id"] != check["run_id"] \
                or postponed(r["source_event_id"], parse_utc(r["commence_utc"]), starts):
            continue
        groups.setdefault(outcome_of(r), []).append(r)
    return sorted((bets.cheaper(rs) for rs in groups.values()), key=lambda r: (-Decimal(r["edge"]), r["row_id"]))


def near_misses(store, source, latest, outcome_of, now, starts, exclude=()):
    """Up to 5 results whose gap at the latest check was at least 0 and under the bar, one per result
    (the cheaper), biggest first. exclude: result keys already shown as gaps."""
    settings = rule_settings()
    zero = {**settings, "threshold": Decimal(0)}
    table, refs, conflicted = load_table(), latest_reference(store, source), store.conflicted_events()
    groups = {}
    for eid, check in latest.items():
        ref = refs.get(eid)
        matches = store.con.execute("""
            SELECT o.market_ticker, o.event_ticker, o.ref_outcome, m.yes_sub_title, m.series_ticker, m.fee_type,
                   m.fee_multiplier, m.fee_override, s.status, s.ts_utc, s.yes_bid, s.yes_ask, s.no_bid, s.no_ask,
                   s.yes_bid_size, s.yes_ask_size
            FROM odds_matches o JOIN markets m ON m.ticker = o.market_ticker
            LEFT JOIN price_snapshots s ON s.ticker = o.market_ticker AND s.run_id = ?
            WHERE o.source = ? AND o.source_event_id = ?""", [check["run_id"], source, eid]).fetchall()
        if ref is None or check_outcomes([m[2] for m in matches], ref["fair"].keys()) \
                or postponed(eid, ref["commence_utc"], starts):
            continue
        for (ticker, event_ticker, ref_outcome, title, series_ticker, fee_type, fee_mult, override, status, ts,
             yes_bid, yes_ask, no_bid, no_ask, bid_size, ask_size) in matches:
            if event_ticker in conflicted or ts is None:
                continue
            try:
                m_taker, _ = resolve_multipliers(table, series_ticker, fee_type, fee_mult, bool(override))
                market = {"m_taker": m_taker, "precision": table_precision(table)}
            except FeeScheduleUnknown:
                continue
            snap = {"status": status, "ts_utc": from_db_ts(ts), "yes_bid": yes_bid, "yes_ask": yes_ask,
                    "no_bid": no_bid, "no_ask": no_ask, "yes_bid_size": bid_size, "yes_ask_size": ask_size}
            match = {"ref_outcome": ref_outcome}
            above = {r["side"] for r in evaluate(snap, market, match, ref, settings)[0]}
            for r in evaluate(snap, market, match, ref, zero)[0]:      # no book: the best ask only
                if r["side"] in above:
                    continue
                row = {**r, "row_id": row_id(check["run_id"], ticker, r["side"]), "rule_id": rule_id(),
                       "market_ticker": ticker, "event_ticker": event_ticker, "outcome": title,
                       "source_event_id": eid, "sport_key": check["sport_key"],
                       "commence_utc": iso_z(ref["commence_utc"])}
                if outcome_of(row) not in exclude:
                    groups.setdefault(outcome_of(row), []).append(row)
    near = sorted((bets.cheaper(rs) for rs in groups.values()), key=lambda r: (-r["edge"], r["row_id"]))
    return near[:NEAR_MAX]
