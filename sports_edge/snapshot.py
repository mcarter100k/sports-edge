"""The 15-minute snapshot cycle (docs/specs/snapshot_job_spec.md).

python -m sports_edge.snapshot
"""
import json
import secrets
import sys
from collections import Counter
from datetime import timedelta

from . import config
from .kalshi import KalshiClient, dec
from .matching import RULE_VERSION, check_outcomes, kalshi_game, match
from .odds import DevigRejected, TheOddsApi, devig_proportional
from .shadow import run_shadow
from .store import Store, from_db_ts, to_db_ts
from .timeutil import DISPLAY_TZ, parse_utc, utcnow

JOB = "snapshot"
NEAR_WINDOW = timedelta(hours=48)
FULL_CATCHUP = timedelta(minutes=55)
SWEEP_OVERLAP = timedelta(hours=2)
FIRST_SWEEP = timedelta(hours=26)
LOOKUP_AFTER = timedelta(hours=6)
LOOKUP_LIMIT = 200
LOOKUP_MAX_AGE = timedelta(days=14)
MATCH_WINDOW_AFTER_HORIZON = timedelta(hours=8)


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


def decide_tier(store, now):
    if now.astimezone(DISPLAY_TZ).minute < 15:
        return "full"
    last = store.last_success(JOB, tier="full")
    if last is None or now - last > FULL_CATCHUP:
        return "full"
    return "near"


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
    fee_type = event.get("fee_type_override") or series["fee_type"]
    fee_mult = (event.get("fee_multiplier_override")
                if event.get("fee_type_override") else series["fee_multiplier"])
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
        "fee_type": fee_type, "fee_multiplier": fee_mult,
        "fee_waiver_expiration_time": parse_utc(m.get("fee_waiver_expiration_time")),
        "first_seen_utc": seen, "last_seen_utc": seen}


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


def scan(client, sports):
    """Collect every page before anything is written (all-or-nothing)."""
    events = []
    for fetched, page in client.open_events_with_markets():
        for ev in page:
            if ev["series_ticker"] in sports:
                events.append((fetched, ev))
    return events


def sweep_settlements(store, client, now, open_tickers):
    last_full = store.last_success(JOB, tier="full")
    since = (last_full - SWEEP_OVERLAP) if last_full else now - FIRST_SWEEP
    known = {t for (t,) in store.con.execute("SELECT ticker FROM markets").fetchall()}
    rows = [r for r in (settlement_row(m, "settled_sweep")
                        for m in client.settled_markets(int(since.timestamp())))
            if r and r["ticker"] in known]
    added, conflicts = store.record_settlements(rows, now)
    pending = store.con.execute("""
        SELECT m.ticker FROM markets m
        LEFT JOIN settlements s ON s.ticker = m.ticker
        LEFT JOIN (SELECT DISTINCT market_ticker FROM odds_matches) o ON o.market_ticker = m.ticker
        WHERE s.ticker IS NULL AND m.resolve_by < ? AND m.resolve_by > ?
        ORDER BY (o.market_ticker IS NOT NULL) DESC, m.resolve_by DESC
        LIMIT ?""", [to_db_ts(now - LOOKUP_AFTER), to_db_ts(now - LOOKUP_MAX_AGE),
                     LOOKUP_LIMIT + len(open_tickers)]).fetchall()
    lookups = [t for (t,) in pending if t not in open_tickers][:LOOKUP_LIMIT]
    looked = []
    for t in lookups:
        m = client.market(t)
        r = settlement_row(m, "market_lookup") if m else None
        if r:
            looked.append(r)
    a2, c2 = store.record_settlements(looked, now)
    return {"settlements_swept": added + a2, "settlement_conflicts": conflicts + c2,
            "settlement_lookups": len(lookups)}


def odds_step(store, source, games, run_id, now, out, err):
    """Match Kalshi games to reference events and price the matched ones."""
    last = store.con.execute(
        "SELECT max(ts_utc) FROM odds_calls WHERE endpoint = 'sports' AND http_status = 200").fetchone()[0]
    if last and now - from_db_ts(last) < timedelta(minutes=config.odds_interval_min()):
        return {"odds": "interval_not_due"}
    group_map = {k: v for k, v in config.load_json_config("sport_groups.json").items()
                 if not k.startswith("_")}
    aliases = {k: v for k, v in config.load_json_config("team_aliases.json").items()
               if not k.startswith("_")}
    horizon_end = now + timedelta(hours=config.odds_horizon_h())
    relevant = [(g, s) for g, s in games
                if now <= g.resolve_by <= horizon_end + MATCH_WINDOW_AFTER_HORIZON]
    tags_needed = {t for g, _ in relevant for t in g.sport_tags}
    sports = source.list_sports()
    unmapped = sorted({s.group for s in sports if s.active and s.group not in group_map})
    if unmapped:
        out(f"UNMAPPED GROUP {', '.join(unmapped)}")
    stubs_by_tag, stub_by_id = {}, {}
    for sp in sports:
        tag = group_map.get(sp.group)
        if not sp.active or sp.has_outrights or tag not in tags_needed:
            continue
        for stub in source.list_events(sp.key):
            if now < stub.commence_utc <= horizon_end:
                stubs_by_tag.setdefault(tag, []).append(stub)
                stub_by_id[stub.source_event_id] = stub
    result = match(relevant, stubs_by_tag, aliases)
    rows = [{**m, "source": source.name, "rule_version": RULE_VERSION} for m in result.matches]
    added, conflicts = store.record_matches(rows, now)
    for c in store.con.execute(
            "SELECT DISTINCT event_ticker, source_event_id FROM match_conflicts WHERE seen_utc = ?",
            [to_db_ts(now)]).fetchall():
        err(f"MATCH CONFLICT {c[0]} now fits {c[1]}; never compared again")
    for u in result.unmatched:
        err(f"UNMATCHED {u['event_ticker']} sport={u['sport']} reason={u['reason']} detail={u['detail']}")
    store.append("unmatched", [{**u, "run_id": run_id, "ts_utc": now} for u in result.unmatched])

    # Price matched events, soonest first, within the daily credit budget.
    outcomes_by_event = {}
    for m in result.matches:
        outcomes_by_event.setdefault(m["source_event_id"], []).append(m["ref_outcome"])
    by_key = {}
    for eid in outcomes_by_event:
        stub = stub_by_id[eid]
        by_key.setdefault(stub.sport_key, []).append(stub)
    day_start = now.astimezone(DISPLAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    budget = config.odds_daily_credits()
    priced, rejected, budget_hit, extra_unmatched = 0, 0, False, []
    for key, stubs in sorted(by_key.items(), key=lambda kv: min(s.commence_utc for s in kv[1])):
        if store.credits_used_since(day_start) + 1 > budget:
            out(f"ODDS BUDGET REACHED ({budget} credits/day); {key} and later sports not priced")
            budget_hit = True
            break
        ids = [s.source_event_id for s in sorted(stubs, key=lambda s: s.commence_utc)]
        ref_rows = []
        for ref in source.fetch_moneylines(key, ids, now):
            problem = check_outcomes(outcomes_by_event.get(ref.source_event_id, []), ref.outcomes)
            if problem:
                extra_unmatched.append({"event_ticker": ref.source_event_id, "sport": key,
                                        "reason": "outcome_count", "detail": problem})
            try:
                fair, overround = devig_proportional(ref.outcomes)
            except DevigRejected as exc:
                err(f"DEVIG REJECTED {key} {ref.source_event_id}: {exc}")
                rejected += 1
                continue
            for name, odds in ref.outcomes.items():
                ref_rows.append({
                    "fetched_utc": ref.fetched_utc, "source": source.name, "bookmaker": ref.bookmaker,
                    "sport_key": key, "source_event_id": ref.source_event_id,
                    "commence_utc": ref.commence_utc, "home": ref.home, "away": ref.away,
                    "outcome": name, "decimal_odds": odds, "fair_prob": fair[name],
                    "overround": overround, "devig_method": "proportional",
                    "book_last_update_utc": ref.book_last_update_utc})
            priced += 1
        store.append("reference_odds", ref_rows)
    for u in extra_unmatched:
        kalshi_events = sorted({m["event_ticker"] for m in result.matches
                                if m["source_event_id"] == u["event_ticker"]})
        for ev in kalshi_events:
            err(f"UNMATCHED {ev} sport={u['sport']} reason=outcome_count detail={u['detail']}")
            store.append("unmatched", [{"run_id": run_id, "ts_utc": now, "event_ticker": ev,
                                        "sport": u["sport"], "reason": "outcome_count",
                                        "detail": u["detail"]}])
    unmatched_by_sport = Counter(u["sport"] for u in result.unmatched)
    return {"odds": "ran", "games_considered": len(relevant), "matches_added": added,
            "match_conflicts": conflicts, "events_matched": len(outcomes_by_event),
            "unmatched": len(result.unmatched) + len(extra_unmatched),
            "unmatched_by_sport": dict(unmatched_by_sport), "events_priced": priced,
            "devig_rejected": rejected, "budget_reached": budget_hit}


def build_odds_source(store):
    key = config.odds_api_key()
    if not key:
        return None

    def record(**call):
        store.append("odds_calls", [{"ts_utc": utcnow(), **call}])

    return TheOddsApi(key, on_call=record)


def run_cycle(client=None, odds_source_factory=build_odds_source, now_fn=utcnow,
              out=print, err=lambda s: print(s, file=sys.stderr)):
    client = client or KalshiClient()
    started = now_fn()
    run_id = new_run_id(JOB, started)
    with Store.open() as store:
        store.job_event(run_id, JOB, "start", started, "running")
        summary = {}
        try:
            tier = decide_tier(store, started)
            summary["tier"] = tier
            if tier == "full" or not series_index(store):
                summary["series"] = refresh_series(store, client, started)
            sports = series_index(store)
            events = scan(client, sports)
            n_markets = sum(len(ev.get("markets") or []) for _, ev in events)
            if not events or not n_markets:
                raise ScanEmpty(f"SCAN EMPTY: {len(events)} sports events, {n_markets} markets")
            market_rows, snaps, settles, games = [], {}, [], []
            near_cutoff = started + NEAR_WINDOW
            for fetched, ev in events:
                series = sports[ev["series_ticker"]]
                game, _reason = kalshi_game(ev, series["tags"] or [series["sport"]],
                                            min((t for t in (resolve_by(m) for m in ev.get("markets") or [])
                                                 if t), default=None))
                if game:
                    games.append((game, series["sport"]))
                for m in ev.get("markets") or []:
                    row = market_row(ev, m, series, fetched)
                    market_rows.append(row)
                    if tier == "full" or (row["resolve_by"] and row["resolve_by"] <= near_cutoff):
                        snaps[m["ticker"]] = snapshot_row(run_id, tier, m, fetched)
                        snaps[m["ticker"]]["_sport"] = series["sport"]
                    s = settlement_row(m, "scan")
                    if s:
                        settles.append(s)
            with store.transaction():
                store.upsert("markets", market_rows, "ticker")
                store.append("price_snapshots", snaps.values())
                added, conflicts = store.record_settlements(settles, started)
            summary.update({
                "events": len(events), "markets": n_markets, "snapshot_rows": len(snaps),
                "sports": len({s["_sport"] for s in snaps.values()}),
                "settlements_from_scan": added, "settlement_conflicts": conflicts,
                "moneyline_games": len(games)})
            if tier == "full":
                open_tickers = {m["ticker"] for _, ev in events for m in ev.get("markets") or []
                                if m["status"] != "finalized"}
                summary.update(sweep_settlements(store, client, started, open_tickers))
            source = odds_source_factory(store)
            if source is None:
                out("ODDS NOT CONFIGURED: ODDS_API_KEY is not set; no reference comparison this run")
                summary["odds"] = "not_configured"
            else:
                summary.update(odds_step(store, source, games, run_id, now_fn(), out, err))
                summary.update(run_shadow(store, run_id, snaps, now_fn(), source.name, out))
            summary["kalshi_requests"] = client.requests
        except BaseException as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
            record_failure(store, run_id, JOB, now_fn(), summary)
            raise
        store.job_event(run_id, JOB, "finish", now_fn(), "ok", summary)
    parts = " ".join(f"{k}={v}" for k, v in summary.items() if not isinstance(v, dict))
    out(f"SNAPSHOT OK run={run_id} {parts}")
    if summary.get("unmatched_by_sport"):
        out("unmatched by sport: " + ", ".join(
            f"{k}={v}" for k, v in sorted(summary["unmatched_by_sport"].items())))
    return summary


def main():
    run_cycle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
