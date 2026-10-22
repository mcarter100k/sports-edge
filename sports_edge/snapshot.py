"""The 15-minute snapshot cycle (docs/specs/snapshot_job_spec.md).

python -m sports_edge.snapshot

daily  (03:00 LA, or catch-up): full scan of open sports events; price rows for matched markets
hourly (other :00 runs):        matched events only, every market
near   (:15, :30, :45):         matched events only, markets resolving within 48 h
"""
import json
import secrets
import sys
from collections import Counter
from datetime import timedelta

from . import config
from .kalshi import KalshiClient, dec
from .odds_step import build_odds_source, odds_step
from .shadow import run_shadow
from .store import Store, to_db_ts
from .timeutil import DISPLAY_TZ, parse_utc, utcnow

JOB = "snapshot"
DAILY_HOUR_LA = 3
NEAR_WINDOW = timedelta(hours=48)
DAILY_CATCHUP = timedelta(hours=24, minutes=15)
MATCHED_STALE_AFTER = timedelta(days=2)
SWEEP_OVERLAP = timedelta(hours=2)
FIRST_SWEEP = timedelta(hours=26)
LOOKUP_AFTER = timedelta(hours=6)
LOOKUP_LIMIT = 200
LOOKUP_MAX_AGE = timedelta(days=14)
STILL_OPEN_WINDOW = timedelta(hours=2)   # active and seen this recently = still open


class ScanEmpty(Exception):
    pass


def record_failure(store, run_id, job, now, summary):
    """Best effort: the original exception is what must surface."""
    try:
        store.job_event(run_id, job, "finish", now, "failed", summary)
    except Exception as exc:  # noqa: BLE001
        print(f"could not record the failed run: {exc}", file=sys.stderr)


def new_run_id(job, now):
    return f"{job}-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"


def resolve_by(market):
    times = [t for t in (parse_utc(market.get("expected_expiration_time")),
                         parse_utc(market.get("close_time"))) if t]
    return min(times) if times else None


def decide_kind(store, now):
    la = now.astimezone(DISPLAY_TZ)
    last_daily = store.last_success(JOB, tier="daily")
    if (la.hour == DAILY_HOUR_LA and la.minute < 15) or last_daily is None \
            or now - last_daily > DAILY_CATCHUP or not series_index(store):
        return "daily"
    return "hourly" if la.minute < 15 else "near"


def series_index(store):
    rows = store.con.execute(
        "SELECT series_ticker, sport, tags, fee_type, fee_multiplier FROM series").fetchall()
    return {t: {"sport": sport, "tags": json.loads(tags or "[]"), "fee_type": ft, "fee_multiplier": fm}
            for t, sport, tags, ft, fm in rows}


def refresh_series(store, client, now):
    rows = []
    for s in client.sports_series():
        tags = s.get("tags") or []
        rows.append({"series_ticker": s["ticker"], "title": s.get("title"),
                     "sport": tags[0] if tags else "Untagged", "tags": json.dumps(tags),
                     "fee_type": s["fee_type"], "fee_multiplier": s["fee_multiplier"],
                     "first_seen_utc": now, "last_seen_utc": now})
    store.upsert("series", rows, "series_ticker")
    return len(rows)


def market_row(event, m, series, seen):
    meta = event.get("product_metadata") or {}
    override = event.get("fee_type_override") is not None or event.get("fee_multiplier_override") is not None
    return {
        "ticker": m["ticker"], "event_ticker": event["event_ticker"],
        "series_ticker": event["series_ticker"], "sport": series["sport"],
        "competition": meta.get("competition"), "competition_scope": meta.get("competition_scope"),
        "event_title": event.get("title"), "yes_sub_title": m.get("yes_sub_title"),
        "no_sub_title": m.get("no_sub_title"), "market_type": m.get("market_type"),
        "status": m["status"], "mutually_exclusive": bool(event.get("mutually_exclusive")),
        "open_time": parse_utc(m.get("open_time")), "close_time": parse_utc(m.get("close_time")),
        "expected_expiration_time": parse_utc(m.get("expected_expiration_time")),
        "occurrence_time": parse_utc(m.get("occurrence_datetime")), "resolve_by": resolve_by(m),
        "fee_type": series["fee_type"], "fee_multiplier": series["fee_multiplier"],
        "fee_waiver_expiration_time": parse_utc(m.get("fee_waiver_expiration_time")),
        "fee_override": override, "first_seen_utc": seen, "last_seen_utc": seen}


def snapshot_row(run_id, tier, m, ts):
    return {
        "run_id": run_id, "ts_utc": ts, "tier": tier, "ticker": m["ticker"], "status": m["status"],
        "yes_bid": dec(m.get("yes_bid_dollars")), "yes_ask": dec(m.get("yes_ask_dollars")),
        "no_bid": dec(m.get("no_bid_dollars")), "no_ask": dec(m.get("no_ask_dollars")),
        "last_price": dec(m.get("last_price_dollars")),
        "yes_bid_size": dec(m.get("yes_bid_size_fp")), "yes_ask_size": dec(m.get("yes_ask_size_fp")),
        "volume": dec(m.get("volume_fp")), "volume_24h": dec(m.get("volume_24h_fp")),
        "open_interest": dec(m.get("open_interest_fp"))}


def settlement_row(m, source):
    if m.get("status") != "finalized" or m.get("result") not in ("yes", "no", "scalar"):
        return None
    return {"ticker": m["ticker"], "result": m["result"],
            "settlement_value": dec(m.get("settlement_value_dollars")),
            "settlement_ts": parse_utc(m.get("settlement_ts")), "source": source}


def write_events(store, run_id, tier, events, sports, now, snapshot_if, snaps):
    """Upsert markets, append snapshot rows (where snapshot_if(row) holds), record settlements.

    events: [(fetched_utc, event)]. One transaction. Returns (markets, settlements added, conflicts).
    """
    market_rows, settles, new_snaps = [], [], {}
    for fetched, ev in events:
        series = sports.get(ev["series_ticker"])
        if series is None:
            continue
        for m in ev.get("markets") or []:
            row = market_row(ev, m, series, fetched)
            market_rows.append(row)
            if snapshot_if(row):
                new_snaps[m["ticker"]] = {**snapshot_row(run_id, tier, m, fetched), "_sport": series["sport"]}
            s = settlement_row(m, "scan" if tier == "daily" else "matched_fetch")
            if s:
                settles.append(s)
    with store.transaction():
        store.upsert("markets", market_rows, "ticker")
        store.append("price_snapshots", new_snaps.values())
        added, conflicts = store.record_settlements(settles, now)
    snaps.update(new_snaps)
    return len(market_rows), added, conflicts


def sweep_settlements(store, client, now, open_tickers):
    last_daily = store.last_success(JOB, tier="daily")
    since = (last_daily - SWEEP_OVERLAP) if last_daily else now - FIRST_SWEEP
    swept = [r for r in (settlement_row(m, "settled_sweep")
                         for m in client.settled_markets(int(since.timestamp()))) if r]
    known = {t for (t,) in store.con.execute(
        "SELECT ticker FROM markets WHERE ticker IN (SELECT unnest(?))",
        [[r["ticker"] for r in swept]]).fetchall()} if swept else set()
    added, conflicts = store.record_settlements([r for r in swept if r["ticker"] in known], now)
    pending = store.con.execute("""
        SELECT m.ticker FROM markets m
        LEFT JOIN settlements s ON s.ticker = m.ticker
        JOIN (SELECT DISTINCT market_ticker FROM odds_matches) o ON o.market_ticker = m.ticker
        WHERE s.ticker IS NULL AND m.resolve_by < ? AND m.resolve_by > ?
          AND (m.status <> 'active' OR m.last_seen_utc < ?)
        ORDER BY m.resolve_by DESC
        LIMIT ?""", [to_db_ts(now - LOOKUP_AFTER), to_db_ts(now - LOOKUP_MAX_AGE),
                     to_db_ts(now - STILL_OPEN_WINDOW), LOOKUP_LIMIT + len(open_tickers)]).fetchall()
    lookups = [t for (t,) in pending if t not in open_tickers][:LOOKUP_LIMIT]
    looked = [r for r in (settlement_row(m, "market_lookup") if m else None
                          for m in (client.market(t) for t in lookups)) if r]
    a2, c2 = store.record_settlements(looked, now)
    return {"settlements_swept": added + a2, "settlement_conflicts": conflicts + c2,
            "settlement_lookups": len(lookups)}


def matched_event_tickers(store, now, kind):
    params = [to_db_ts(now - MATCHED_STALE_AFTER)]
    near = ""
    if kind == "near":
        near = "AND m.resolve_by <= ?"
        params.append(to_db_ts(now + NEAR_WINDOW))
    return [e for (e,) in store.con.execute(f"""
        SELECT DISTINCT o.event_ticker FROM odds_matches o
        JOIN markets m ON m.ticker = o.market_ticker
        LEFT JOIN settlements s ON s.ticker = m.ticker
        WHERE s.ticker IS NULL AND m.resolve_by > ? {near}
          AND o.event_ticker NOT IN (SELECT event_ticker FROM match_conflicts)
        ORDER BY 1""", params).fetchall()]


def matched_tickers(store):
    """Markets with a reference match, excluding events with a match conflict."""
    return {t for (t,) in store.con.execute(
        "SELECT market_ticker FROM odds_matches "
        "WHERE event_ticker NOT IN (SELECT event_ticker FROM match_conflicts)").fetchall()}


def matched_by_sport(store, now):
    return dict(store.con.execute("""
        SELECT m.sport, count(DISTINCT m.ticker) FROM odds_matches o
        JOIN markets m ON m.ticker = o.market_ticker
        LEFT JOIN settlements s ON s.ticker = m.ticker
        WHERE s.ticker IS NULL AND m.resolve_by > ?
        GROUP BY 1 ORDER BY 1""", [to_db_ts(now - MATCHED_STALE_AFTER)]).fetchall())


def run_cycle(client=None, odds_source_factory=build_odds_source, now_fn=utcnow,
              out=print, err=lambda s: print(s, file=sys.stderr)):
    client = client or KalshiClient()
    started = now_fn()
    run_id = new_run_id(JOB, started)
    with Store.open() as store:
        store.job_event(run_id, JOB, "start", started, "running")
        summary = {}
        try:
            if not client.exchange_active():
                summary = {"tier": "exchange_closed", "kalshi_requests": client.requests}
                store.job_event(run_id, JOB, "finish", now_fn(), "exchange_closed", summary)
                out(f"EXCHANGE CLOSED run={run_id}: Kalshi reports exchange_active=false "
                    f"(scheduled maintenance); nothing fetched, no alert")
                return summary
            kind = decide_kind(store, started)
            summary["tier"] = kind
            snaps = {}

            def upsert_events(fetched_events, now):
                """Targeted lookups: refresh markets only; snapshots come from the matched fetch."""
                write_events(store, run_id, "lookup", fetched_events, series_index(store),
                             now, lambda row: False, {})

            if kind == "daily":
                summary["series"] = refresh_series(store, client, started)
                sports = series_index(store)
                events = [(f, ev) for f, page in client.open_events_with_markets()
                          for ev in page if ev["series_ticker"] in sports]
                n_markets = sum(len(ev.get("markets") or []) for _, ev in events)
                if not events or not n_markets:
                    raise ScanEmpty(f"SCAN EMPTY: {len(events)} sports events, {n_markets} markets")
                matched = matched_tickers(store)
                _, added, conflicts = write_events(store, run_id, "daily", events, sports, started,
                                                   lambda row: row["ticker"] in matched, snaps)
                open_tickers = {m["ticker"] for _, ev in events for m in ev.get("markets") or []
                                if m["status"] != "finalized"}
                summary.update({"events": len(events), "markets": n_markets,
                                "settlements_from_scan": added, "settlement_conflicts": conflicts})
                summary.update(sweep_settlements(store, client, started, open_tickers))

            source = odds_source_factory(store)
            checked = []
            if source is None:
                out("ODDS NOT CONFIGURED: ODDS_API_KEY is not set; no reference comparison this run")
                summary["odds"] = "not_configured"
            else:
                summary.update(odds_step(store, source, client, upsert_events, run_id, now_fn(), out, err))
                checked = summary.pop("checked_events", [])
                summary["checked_games"] = len(checked)

            if kind != "daily":
                tickers = matched_event_tickers(store, started, kind)
                events = [(fetched, ev) for fetched, page in
                          client.events_by_tickers(tickers, config.kalshi_ticker_batch()) for ev in page]
                cutoff = started + NEAR_WINDOW
                _, added, conflicts = write_events(
                    store, run_id, kind, events, series_index(store), started,
                    (lambda row: True) if kind == "hourly"
                    else (lambda row: row["resolve_by"] is not None and row["resolve_by"] <= cutoff), snaps)
                summary.update({"matched_events": len(tickers), "events": len(events),
                                "settlements_from_matched": added, "settlement_conflicts": conflicts})
                if not tickers:
                    out("matched events: 0 (no odds matches yet); unmatched markets are snapshotted daily")
            summary["snapshot_rows"] = len(snaps)
            summary["sports"] = len({s["_sport"] for s in snaps.values()})
            if source is not None:
                summary.update(run_shadow(store, run_id, snaps, now_fn(), source.name, out,
                                          set(checked), client.orderbook))
            summary["matched_by_sport"] = matched_by_sport(store, started)
            summary["kalshi_requests"] = client.requests
        except BaseException as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
            record_failure(store, run_id, JOB, now_fn(), summary)
            raise
        store.job_event(run_id, JOB, "finish", now_fn(), "ok", summary)
    parts = " ".join(f"{k}={v}" for k, v in summary.items() if not isinstance(v, dict))
    out(f"SNAPSHOT OK run={run_id} {parts}")
    mbs = summary["matched_by_sport"]
    out("matched markets by sport: " + (", ".join(f"{k}={v}" for k, v in mbs.items()) or "none yet"))
    if summary.get("unmatched_by_sport"):
        out("unmatched by sport: " + ", ".join(
            f"{k}={v}" for k, v in sorted(summary["unmatched_by_sport"].items())))
    return summary


def main():
    run_cycle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
