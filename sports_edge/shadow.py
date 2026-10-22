"""Shadow ledger: fee-adjusted gaps between Kalshi and fair probability
(docs/specs/shadow_ledger_spec.md). Logs only. Never trades.

Gating (decides whether a row is written) lives in evaluate() only.
Record-only fields (ages, minutes to start, one-hour moves, power fair) are
computed in record_fields() and never passed to evaluate().
"""
import hashlib
import os
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from . import config, ledger
from .fees import FeeScheduleUnknown, fee_per_contract, load_table, resolve_multipliers, table_precision
from .matching import check_outcomes
from .store import from_db_ts, to_db_ts
from .timeutil import iso_z

# The signal rule. Changing any constant is a new rule_id, never an edit in place.
# GAP_v1 (frozen, never written live): threshold 0.03, 100 contracts, fee
#   precision 0.01, reference fetch age <= 75 min, no depth check.
# GAP_v2: threshold 0.03, 100 contracts, fee precision from the cited fee table,
#   reference fetch age <= 15 min, depth >= contracts. The book last-update age
#   is recorded on every row but never gates (amended in place 2026-10-22,
#   before any live run).
RULE_ID = "GAP_v2"
RULE_DEFAULTS = {
    "SHADOW_EDGE_THRESHOLD": "0.03",
    "SHADOW_CONTRACTS": "100",
    "ODDS_MAX_AGE_MIN": "15",
}
MOVE_WINDOW = (timedelta(minutes=60), timedelta(minutes=120))

SCHEMA_VERSION = "2"
HEADER = [
    "row_id", "schema_version", "rule_id", "logged_utc", "run_id", "snapshot_utc",
    "odds_fetched_utc", "book_last_update_utc", "commence_utc", "sport", "competition",
    "event_ticker", "market_ticker", "outcome", "ref_outcome", "side", "yes_bid", "yes_ask", "no_bid",
    "no_ask", "yes_bid_size", "yes_ask_size", "depth_contracts", "price_paid", "kalshi_implied_prob",
    "fair_prob", "fee_per_contract", "fee_precision", "contracts_assumed", "edge", "threshold",
    "fee_type", "fee_multiplier", "fee_waiver_expiration_utc", "odds_source", "bookmaker",
    "source_event_id", "overround", "devig_method",
    # record-only from here on: never gate a row
    "odds_fetch_age_s", "book_update_age_s", "minutes_to_start", "kalshi_mid", "kalshi_move_1h",
    "ref_fair_1h_ago", "ref_move_1h", "fair_prob_power",
]
Q6 = Decimal("0.000001")


def _rule_value(name):
    return os.environ.get(name) or RULE_DEFAULTS[name]


def rule_settings():
    return {"threshold": Decimal(_rule_value("SHADOW_EDGE_THRESHOLD")),
            "contracts": int(_rule_value("SHADOW_CONTRACTS")),
            "max_age_min": int(_rule_value("ODDS_MAX_AGE_MIN"))}


def rule_id():
    """GAP_v2, or GAP_v2+custom(...) when any rule parameter is overridden."""
    custom = {k: os.environ[k] for k in RULE_DEFAULTS
              if os.environ.get(k) and os.environ[k] != RULE_DEFAULTS[k]}
    if not custom:
        return RULE_ID
    return f"{RULE_ID}+custom(" + ",".join(f"{k}={v}" for k, v in sorted(custom.items())) + ")"


def row_id(run_id, market_ticker, side):
    return hashlib.sha1(f"{run_id}|{market_ticker}|{side}".encode()).hexdigest()


def latest_reference(store, source):
    """{source_event_id: {fetched_utc, commence_utc, book_last_update_utc, overround, devig_method,
    bookmaker, fair: {outcome: prob}, fair_power: {outcome: prob}}} from each event's latest fetch."""
    rows = store.con.execute("""
        SELECT r.source_event_id, r.fetched_utc, r.commence_utc, r.book_last_update_utc,
               r.overround, r.devig_method, r.bookmaker, r.outcome, r.fair_prob, r.fair_prob_power
        FROM reference_odds r
        JOIN (SELECT source_event_id, max(fetched_utc) AS f FROM reference_odds
              WHERE source = ? GROUP BY source_event_id) last
          ON r.source_event_id = last.source_event_id AND r.fetched_utc = last.f
        WHERE r.source = ?""", [source, source]).fetchall()
    refs = {}
    for eid, fetched, commence, book_upd, overround, method, book, outcome, fair, power in rows:
        ref = refs.setdefault(eid, {
            "fetched_utc": from_db_ts(fetched), "commence_utc": from_db_ts(commence),
            "book_last_update_utc": from_db_ts(book_upd), "overround": overround,
            "devig_method": method, "bookmaker": book, "fair": {}, "fair_power": {}})
        ref["fair"][outcome] = fair
        ref["fair_power"][outcome] = power
    return refs


def depth_for(side, snap):
    """Contracts resting at the price this side buys at. NO fills against the YES bid."""
    return snap.get("yes_ask_size") if side == "YES" else snap.get("yes_bid_size")


def evaluate(snap, market, match, ref, settings):
    """The signal rule for one market at one snapshot.

    Returns (rows, skip_reason, depth_skips). Only the inputs here may gate a row.
    """
    if snap["status"] != "active":
        return [], "not_active", 0
    asks = (snap.get("yes_ask"), snap.get("no_ask"))
    if any(a is None or not (Decimal(0) < a < Decimal(1)) for a in asks):
        return [], "no_two_sided_quote", 0
    ts = snap["ts_utc"]
    max_age = timedelta(minutes=settings["max_age_min"])
    if abs(ts - ref["fetched_utc"]) > max_age:
        return [], "reference_stale", 0
    # The bookmaker's last-update age is record-only (record_fields), never a gate.
    if ts >= ref["commence_utc"]:
        return [], "not_pre_game", 0
    fair_f = ref["fair"].get(match["ref_outcome"])
    if fair_f is None:
        return [], "outcome_not_priced", 0
    if market.get("fee_error"):
        return [], "fee_unknown", 0
    fair = Decimal(str(fair_f)).quantize(Q6)
    rows, depth_skips = [], 0
    for side, price, win_prob in (("YES", snap["yes_ask"], fair),
                                  ("NO", snap["no_ask"], Decimal(1) - fair)):
        fee = fee_per_contract(price, settings["contracts"], market["m_taker"], "taker",
                               market["precision"])
        edge = (win_prob - price - fee).quantize(Q6)
        if edge <= settings["threshold"]:
            continue
        depth = depth_for(side, snap)
        if depth is None or depth < settings["contracts"]:
            depth_skips += 1
            continue
        rows.append({
            "side": side, "price_paid": price, "kalshi_implied_prob": price, "fair_prob": fair,
            "fee_per_contract": fee.quantize(Q6), "edge": edge, "depth_contracts": depth})
    return rows, None, depth_skips


def _mid(bid, ask):
    if bid is None or ask is None or not (Decimal(0) < bid <= ask < Decimal(1)):
        return None
    return (Decimal(bid) + Decimal(ask)) / 2


def record_fields(store, source, ticker, eid, ref_outcome, snap, ref):
    """Record-only fields for a written row. Never used to decide whether to write it."""
    ts = snap["ts_utc"]
    fields = {
        "odds_fetch_age_s": int((ts - ref["fetched_utc"]).total_seconds()),
        "book_update_age_s": (int((ts - ref["book_last_update_utc"]).total_seconds())
                              if ref.get("book_last_update_utc") else ""),
        "minutes_to_start": round((ref["commence_utc"] - ts).total_seconds() / 60, 1),
        "fair_prob_power": ref.get("fair_power", {}).get(ref_outcome),
    }
    mid_now = _mid(snap.get("yes_bid"), snap.get("yes_ask"))
    fields["kalshi_mid"] = mid_now if mid_now is not None else ""
    earlier = store.con.execute("""
        SELECT yes_bid, yes_ask FROM price_snapshots
        WHERE ticker = ? AND ts_utc BETWEEN ? AND ? AND yes_bid > 0 AND yes_ask < 1 AND yes_bid <= yes_ask
        ORDER BY ts_utc DESC LIMIT 1""", [ticker, to_db_ts(ts - MOVE_WINDOW[1]),
                                          to_db_ts(ts - MOVE_WINDOW[0])]).fetchone()
    mid_then = _mid(*earlier) if earlier else None
    fields["kalshi_move_1h"] = (mid_now - mid_then) if (mid_now is not None and mid_then is not None) else ""
    prior = store.con.execute("""
        SELECT fair_prob FROM reference_odds
        WHERE source = ? AND source_event_id = ? AND outcome = ? AND fetched_utc BETWEEN ? AND ?
        ORDER BY fetched_utc DESC LIMIT 1""", [source, eid, ref_outcome,
                                               to_db_ts(ref["fetched_utc"] - MOVE_WINDOW[1]),
                                               to_db_ts(ref["fetched_utc"] - MOVE_WINDOW[0])]).fetchone()
    now_fair = ref["fair"].get(ref_outcome)
    fields["ref_fair_1h_ago"] = prior[0] if prior else ""
    fields["ref_move_1h"] = round(now_fair - prior[0], 6) if prior and now_fair is not None else ""
    return fields


def run_shadow(store, run_id, snapshots, now, source_name, out):
    """snapshots: {ticker: snapshot dict} from this run. Appends qualifying rows; returns counts."""
    settings = rule_settings()
    table = load_table()
    matches = store.con.execute("""
        SELECT o.market_ticker, o.event_ticker, o.source_event_id, o.ref_outcome,
               m.sport, m.competition, m.yes_sub_title, m.fee_type, m.fee_multiplier,
               m.fee_waiver_expiration_time, m.series_ticker, m.fee_override
        FROM odds_matches o JOIN markets m ON m.ticker = o.market_ticker
        WHERE o.source = ?""", [source_name]).fetchall()
    fee_reasons = Counter()
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
             fee_mult, waiver, series_ticker, fee_override) in event_matches:
            if event_ticker in conflicted:
                skips["match_conflict"] += 1
                continue
            snap = snapshots.get(ticker)
            if snap is None:
                skips["no_snapshot_this_run"] += 1
                continue
            try:
                m_taker, _m_maker = resolve_multipliers(table, series_ticker, fee_type, fee_mult,
                                                        bool(fee_override))
                market = {"m_taker": m_taker, "precision": table_precision(table)}
            except FeeScheduleUnknown as exc:
                market = {"fee_error": str(exc)}
                fee_reasons[str(exc)] += 1
            found, reason, depth_skips = evaluate(snap, market, {"ref_outcome": ref_outcome}, ref, settings)
            if reason:
                skips[reason] += 1
            if depth_skips:
                skips["insufficient_depth"] += depth_skips
            for r in found:
                rows.append({
                    **r, **record_fields(store, source_name, ticker, eid, ref_outcome, snap, ref),
                    "row_id": row_id(run_id, ticker, r["side"]), "schema_version": SCHEMA_VERSION,
                    "rule_id": rule_id(), "logged_utc": iso_z(now), "run_id": run_id,
                    "snapshot_utc": iso_z(snap["ts_utc"]), "odds_fetched_utc": iso_z(ref["fetched_utc"]),
                    "book_last_update_utc": iso_z(ref["book_last_update_utc"]),
                    "commence_utc": iso_z(ref["commence_utc"]), "sport": sport,
                    "competition": competition, "event_ticker": event_ticker,
                    "market_ticker": ticker, "outcome": title, "ref_outcome": ref_outcome,
                    "yes_bid": snap.get("yes_bid"), "yes_ask": snap.get("yes_ask"),
                    "no_bid": snap.get("no_bid"), "no_ask": snap.get("no_ask"),
                    "yes_bid_size": snap.get("yes_bid_size"), "yes_ask_size": snap.get("yes_ask_size"),
                    "contracts_assumed": settings["contracts"], "threshold": settings["threshold"],
                    "fee_type": fee_type, "fee_multiplier": market["m_taker"],
                    "fee_precision": market["precision"],
                    "fee_waiver_expiration_utc": iso_z(from_db_ts(waiver)) if waiver else "",
                    "odds_source": source_name, "bookmaker": ref["bookmaker"],
                    "source_event_id": eid, "overround": ref["overround"],
                    "devig_method": ref["devig_method"]})
    written = ledger.append_rows(config.shadow_ledger_path(), HEADER, rows) if rows else 0
    if skips:
        out("shadow skips: " + ", ".join(f"{k}={v}" for k, v in sorted(skips.items())))
    for reason, n in sorted(fee_reasons.items()):
        out(f"FEE UNKNOWN ({n} market checks): {reason}")
    return {"shadow_rows": written, "shadow_checked": len(matches), **{
        f"skip_{k}": v for k, v in skips.items()}}
