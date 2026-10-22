"""The hourly odds step: match Kalshi games to reference games, look up
missing Kalshi games, and price the matched ones
(docs/specs/odds_matching_spec.md).
"""
import json
from collections import Counter
from datetime import timedelta

from . import config
from .matching import RULE_VERSION, check_outcomes, kalshi_game, match
from .odds import DevigRejected, TheOddsApi, devig_proportional
from .store import from_db_ts, to_db_ts
from .timeutil import DISPLAY_TZ, utcnow

MATCH_WINDOW_AFTER_HORIZON = timedelta(hours=8)
LIVE_LOOKBACK = timedelta(hours=8)
LOOKUP_HORIZON = timedelta(hours=24)
LOOKUP_SERIES_CAP = 20


def build_odds_source(store):
    key = config.odds_api_key()
    if not key:
        return None

    def record(**call):
        if call["endpoint"] == "odds" and call.get("credits_last") is None:
            call["credits_last"] = 1   # never let a missing header stop the budget counting
        store.append("odds_calls", [{"ts_utc": utcnow(), **call}])

    return TheOddsApi(key, on_call=record)


def games_from_db(store, start, end):
    """Kalshi moneyline games resolving in [start, end], rebuilt from the markets table."""
    rows = store.con.execute("""
        SELECT m.event_ticker, m.ticker, m.yes_sub_title, m.market_type, m.competition_scope,
               m.mutually_exclusive, m.resolve_by, m.status, m.sport, s.tags
        FROM markets m JOIN series s ON s.series_ticker = m.series_ticker
        WHERE m.competition_scope = 'Game' AND m.event_ticker IN (
            SELECT event_ticker FROM markets
            WHERE competition_scope = 'Game' AND resolve_by BETWEEN ? AND ?)
        ORDER BY m.event_ticker, m.ticker""", [to_db_ts(start), to_db_ts(end)]).fetchall()
    events = {}
    for ev, ticker, title, mtype, scope, mutex, rb, status, sport, tags in rows:
        e = events.setdefault(ev, {"event_ticker": ev, "mutually_exclusive": mutex,
                                   "product_metadata": {"competition_scope": scope},
                                   "markets": [], "_rb": [], "_status": set(), "_sport": sport,
                                   "_tags": json.loads(tags or "[]") or [sport]})
        e["markets"].append({"ticker": ticker, "yes_sub_title": title, "market_type": mtype})
        e["_rb"].append(from_db_ts(rb))
        e["_status"].add(status)
    games = []
    for e in events.values():
        if e["_status"] != {"active"}:
            continue
        game, _reason = kalshi_game(e, e["_tags"], min(t for t in e["_rb"] if t))
        if game:
            games.append((game, e["_sport"]))
    return games


def report_unmatched(store, unmatched, markets_per_event, run_id, now, err, prefix="UNMATCHED"):
    """Print and record each (event, reason) the first time only; return how many were new."""
    if not unmatched:
        return 0
    seen = set(store.con.execute(
        "SELECT event_ticker, reason FROM unmatched WHERE event_ticker IN (SELECT unnest(?))",
        [sorted({u["event_ticker"] for u in unmatched})]).fetchall())
    new = [u for u in unmatched if (u["event_ticker"], u["reason"]) not in seen]
    for u in new:
        label = u["event_ticker"].removeprefix("ref:")
        extra = "" if prefix != "UNMATCHED" else \
            f" markets={markets_per_event.get(u['event_ticker'], '?')}"
        err(f"{prefix} {label} sport={u['sport']} reason={u['reason']}{extra} detail={u['detail']}")
    store.append("unmatched", [{**u, "run_id": run_id, "ts_utc": now} for u in new])
    return len(new)


def targeted_lookups(store, client, orphans, upsert_events, now):
    """Fetch open events of the Kalshi series learned for each orphan's sport_key."""
    learned = {}
    for stub in orphans:
        if stub.sport_key not in learned:
            learned[stub.sport_key] = [s for (s,) in store.con.execute("""
                SELECT DISTINCT m.series_ticker FROM odds_matches o
                JOIN markets m ON m.ticker = o.market_ticker
                WHERE o.sport_key = ? ORDER BY 1""", [stub.sport_key]).fetchall()]
    series_order = []
    for stub in sorted(orphans, key=lambda s: s.commence_utc):
        for s in learned[stub.sport_key]:
            if s not in series_order:
                series_order.append(s)
    fetched = series_order[:LOOKUP_SERIES_CAP]
    for s in fetched:
        for fetched_at, events in client.open_events_for_series(s):
            upsert_events([(fetched_at, e) for e in events], now)
    return learned, fetched, len(series_order) > LOOKUP_SERIES_CAP


def odds_step(store, source, client, upsert_events, run_id, now, out, err):
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
    window = (now, horizon_end + MATCH_WINDOW_AFTER_HORIZON)
    relevant = games_from_db(store, *window)
    sports = source.list_sports()
    unmapped = sorted({s.group for s in sports if s.active and s.group not in group_map})
    if unmapped:
        out(f"UNMAPPED GROUP {', '.join(unmapped)}")
    # Every mapped sport is polled (free calls), so games Kalshi has not listed
    # yet can trigger a targeted lookup.
    stubs_by_tag, stub_by_id = {}, {}
    for sp in sports:
        tag = group_map.get(sp.group)
        if not sp.active or sp.has_outrights or tag is None:
            continue
        for stub in source.list_events(sp.key):
            if now - LIVE_LOOKBACK <= stub.commence_utc <= horizon_end:
                stubs_by_tag.setdefault(tag, []).append(stub)
                stub_by_id[stub.source_event_id] = stub
    result = match(relevant, stubs_by_tag, aliases)

    # Targeted Kalshi lookups for reference games within 24 h that no Kalshi game fits.
    orphans = [s for s in stub_by_id.values()
               if s.source_event_id not in result.claimed and now < s.commence_utc <= now + LOOKUP_HORIZON]
    looked_up, capped = [], False
    if orphans:
        learned, looked_up, capped = targeted_lookups(store, client, orphans, upsert_events, now)
        if capped:
            out(f"TARGETED LOOKUP CAPPED at {LOOKUP_SERIES_CAP} series this cycle")
        if looked_up:
            relevant = games_from_db(store, *window)
            retry = match(relevant, {t: [s for s in ss if s in orphans] for t, ss in stubs_by_tag.items()},
                          aliases)
            known = {(m["market_ticker"]) for m in result.matches}
            result.matches += [m for m in retry.matches if m["market_ticker"] not in known]
            result.claimed |= retry.claimed
        ref_unmatched = []
        for s in orphans:
            if s.source_event_id in result.claimed:
                continue
            reason = "no_kalshi_event" if learned.get(s.sport_key) else "no_kalshi_series_known"
            ref_unmatched.append({"event_ticker": f"ref:{s.source_event_id}", "sport": s.sport_key,
                                  "reason": reason,
                                  "detail": f"{s.away} at {s.home}, starts {s.commence_utc:%Y-%m-%dT%H:%MZ}"})
        report_unmatched(store, ref_unmatched, {}, run_id, now, err, prefix="UNMATCHED-REFERENCE")

    rows = [{**m, "source": source.name, "rule_version": RULE_VERSION} for m in result.matches]
    added, conflicts = store.record_matches(rows, now)
    for c in store.con.execute(
            "SELECT DISTINCT event_ticker, source_event_id FROM match_conflicts WHERE seen_utc = ?",
            [to_db_ts(now)]).fetchall():
        err(f"MATCH CONFLICT {c[0]} now fits {c[1]}; never compared again")
    markets_per_event = {g.event_ticker: len(g.teams) + (1 if g.tie_market else 0) for g, _ in relevant}
    report_unmatched(store, result.unmatched, markets_per_event, run_id, now, err)

    # Price matched games that have not started, soonest first, within the daily budget.
    outcomes_by_event = {}
    for m in result.matches:
        outcomes_by_event.setdefault(m["source_event_id"], []).append(m["ref_outcome"])
    by_key = {}
    for eid in outcomes_by_event:
        stub = stub_by_id[eid]
        if now < stub.commence_utc <= horizon_end:
            by_key.setdefault(stub.sport_key, []).append(stub)
    day_start = now.astimezone(DISPLAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    budget = config.odds_daily_credits()
    priced, rejected, budget_hit, mismatched = 0, 0, False, []
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
                for ev in sorted({m["event_ticker"] for m in result.matches
                                  if m["source_event_id"] == ref.source_event_id}):
                    mismatched.append({"event_ticker": ev, "sport": key, "reason": "outcome_count",
                                       "detail": problem})
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
    report_unmatched(store, mismatched, markets_per_event, run_id, now, err)
    return {"odds": "ran", "games_considered": len(relevant), "matches_added": added,
            "match_conflicts": conflicts, "events_matched": len(outcomes_by_event),
            "unmatched": len(result.unmatched) + len(mismatched),
            "unmatched_by_sport": dict(Counter(u["sport"] for u in result.unmatched)),
            "reference_orphans": len(orphans), "lookup_series": len(looked_up),
            "events_priced": priced, "devig_rejected": rejected, "budget_reached": budget_hit}
