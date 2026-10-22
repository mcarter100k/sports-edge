"""Shadow ledger: fee-adjusted gaps between Kalshi and fair probability
(docs/specs/shadow_ledger_spec.md). Logs only. Never trades.
"""
import hashlib
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from . import config, ledger
from .fees import FeeScheduleUnknown, fee_per_contract
from .matching import check_outcomes
from .store import from_db_ts
from .timeutil import iso_z

SCHEMA_VERSION = "1"
HEADER = [
    "row_id", "schema_version", "rule_id", "logged_utc", "run_id", "snapshot_utc",
    "odds_fetched_utc", "book_last_update_utc", "commence_utc", "sport", "competition",
    "event_ticker", "market_ticker", "outcome", "side", "yes_bid", "yes_ask", "no_bid", "no_ask",
    "price_paid", "kalshi_implied_prob", "fair_prob", "fee_per_contract", "contracts_assumed",
    "edge", "threshold", "fee_type", "fee_multiplier", "fee_waiver_expiration_utc", "odds_source",
    "bookmaker", "source_event_id", "overround", "devig_method",
]
Q6 = Decimal("0.000001")


def row_id(run_id, market_ticker, side):
    return hashlib.sha1(f"{run_id}|{market_ticker}|{side}".encode()).hexdigest()


def latest_reference(store, source):
    """{source_event_id: {fetched_utc, commence_utc, book_last_update_utc, overround, devig_method,
    bookmaker, fair: {outcome: prob}}} from the most recent fetch of each event."""
    rows = store.con.execute("""
        SELECT r.source_event_id, r.fetched_utc, r.commence_utc, r.book_last_update_utc,
               r.overround, r.devig_method, r.bookmaker, r.outcome, r.fair_prob
        FROM reference_odds r
        JOIN (SELECT source_event_id, max(fetched_utc) AS f FROM reference_odds
              WHERE source = ? GROUP BY source_event_id) last
          ON r.source_event_id = last.source_event_id AND r.fetched_utc = last.f
        WHERE r.source = ?""", [source, source]).fetchall()
    refs = {}
    for eid, fetched, commence, book_upd, overround, method, book, outcome, fair in rows:
        ref = refs.setdefault(eid, {
            "fetched_utc": from_db_ts(fetched), "commence_utc": from_db_ts(commence),
            "book_last_update_utc": from_db_ts(book_upd), "overround": overround,
            "devig_method": method, "bookmaker": book, "fair": {}})
        ref["fair"][outcome] = fair
    return refs


def evaluate(snap, market, match, ref, settings):
    """One market at one snapshot. Returns (rows, skip_reason)."""
    if snap["status"] != "active":
        return [], "not_active"
    asks = (snap.get("yes_ask"), snap.get("no_ask"))
    if any(a is None or not (Decimal(0) < a < Decimal(1)) for a in asks):
        return [], "no_two_sided_quote"
    ts = snap["ts_utc"]
    if abs(ts - ref["fetched_utc"]) > timedelta(minutes=settings["max_age_min"]):
        return [], "reference_stale"
    if ts >= ref["commence_utc"]:
        return [], "not_pre_game"
    fair_f = ref["fair"].get(match["ref_outcome"])
    if fair_f is None:
        return [], "outcome_not_priced"
    fair = Decimal(str(fair_f)).quantize(Q6)
    rows = []
    for side, price, win_prob in (("YES", snap["yes_ask"], fair),
                                  ("NO", snap["no_ask"], Decimal(1) - fair)):
        try:
            fee = fee_per_contract(price, settings["contracts"], market["fee_type"],
                                   market["fee_multiplier"], "taker", settings["precision"])
        except FeeScheduleUnknown:
            return [], "fee_unknown"
        edge = (win_prob - price - fee).quantize(Q6)
        if edge > settings["threshold"]:
            rows.append({
                "side": side, "price_paid": price, "kalshi_implied_prob": price,
                "fair_prob": fair, "fee_per_contract": fee.quantize(Q6), "edge": edge})
    return rows, None


def run_shadow(store, run_id, snapshots, now, source_name, out):
    """snapshots: {ticker: snapshot dict} from this run. Appends qualifying rows; returns counts."""
    settings = {
        "threshold": config.shadow_threshold(), "contracts": config.shadow_contracts(),
        "precision": config.fee_precision(), "max_age_min": config.odds_max_age_min()}
    matches = store.con.execute("""
        SELECT o.market_ticker, o.event_ticker, o.source_event_id, o.ref_outcome,
               m.sport, m.competition, m.yes_sub_title, m.fee_type, m.fee_multiplier,
               m.fee_waiver_expiration_time
        FROM odds_matches o JOIN markets m ON m.ticker = o.market_ticker
        WHERE o.source = ?""", [source_name]).fetchall()
    conflicted = store.conflicted_events()
    refs = latest_reference(store, source_name)
    by_event = {}
    for row in matches:
        by_event.setdefault(row[2], []).append(row)
    skips, rows = Counter(), []
    for eid, event_matches in by_event.items():
        ref = refs.get(eid)
        if ref is None:
            skips["no_reference"] += len(event_matches)
            continue
        if check_outcomes([m[3] for m in event_matches], ref["fair"].keys()):
            skips["outcome_count"] += len(event_matches)
            continue
        for (ticker, event_ticker, _eid, ref_outcome, sport, competition, title, fee_type,
             fee_mult, waiver) in event_matches:
            if event_ticker in conflicted:
                skips["match_conflict"] += 1
                continue
            snap = snapshots.get(ticker)
            if snap is None:
                skips["no_snapshot_this_run"] += 1
                continue
            market = {"fee_type": fee_type, "fee_multiplier": fee_mult}
            found, reason = evaluate(snap, market, {"ref_outcome": ref_outcome}, ref, settings)
            if reason:
                skips[reason] += 1
            for r in found:
                rows.append({
                    **r, "row_id": row_id(run_id, ticker, r["side"]), "schema_version": SCHEMA_VERSION,
                    "rule_id": config.rule_id(), "logged_utc": iso_z(now), "run_id": run_id,
                    "snapshot_utc": iso_z(snap["ts_utc"]), "odds_fetched_utc": iso_z(ref["fetched_utc"]),
                    "book_last_update_utc": iso_z(ref["book_last_update_utc"]),
                    "commence_utc": iso_z(ref["commence_utc"]), "sport": sport,
                    "competition": competition, "event_ticker": event_ticker,
                    "market_ticker": ticker, "outcome": title, "yes_bid": snap.get("yes_bid"),
                    "yes_ask": snap.get("yes_ask"), "no_bid": snap.get("no_bid"),
                    "no_ask": snap.get("no_ask"), "contracts_assumed": settings["contracts"],
                    "threshold": settings["threshold"], "fee_type": fee_type,
                    "fee_multiplier": fee_mult,
                    "fee_waiver_expiration_utc": iso_z(from_db_ts(waiver)) if waiver else "",
                    "odds_source": source_name, "bookmaker": ref["bookmaker"],
                    "source_event_id": eid, "overround": ref["overround"],
                    "devig_method": ref["devig_method"]})
    written = ledger.append_rows(config.shadow_ledger_path(), HEADER, rows) if rows else 0
    if skips:
        out("shadow skips: " + ", ".join(f"{k}={v}" for k, v in sorted(skips.items())))
    return {"shadow_rows": written, "shadow_checked": sum(1 for _ in matches), **{
        f"skip_{k}": v for k, v in skips.items()}}
