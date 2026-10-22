from datetime import timedelta
from decimal import Decimal as D

import pytest

from helpers import FakeKalshi, FakeOdds, fee_table, game_event, market, ref_sport, series, stub
from sports_edge import config, ledger
from sports_edge.snapshot import ScanEmpty, run_cycle, sweep_settlements
from sports_edge.store import Store
from sports_edge.timeutil import parse_utc

# 03:00 PDT is the daily scan; 04:00 PDT an hourly run; 04:15 PDT a near run.
DAILY_AT = parse_utc("2026-10-22T10:00:00Z")
HOURLY_AT = parse_utc("2026-10-22T11:00:00Z")
NEAR_AT = parse_utc("2026-10-22T11:15:00Z")
START = DAILY_AT + timedelta(hours=5)


def world(now=DAILY_AT):
    soon = game_event("KXNBAGAME-E1", "KXNBAGAME", ["Cleveland", "Philadelphia"], START)
    later = game_event("KXNBAGAME-E2", "KXNBAGAME", ["Boston", "Miami"], now + timedelta(days=5))
    soccer = game_event("KXEPLGAME-E3", "KXEPLGAME", ["Ipswich Town", "Nottingham Forest"],
                        now + timedelta(hours=10), tie=True)
    future = {"event_ticker": "KXNBAFUT-26", "series_ticker": "KXNBAFUT", "mutually_exclusive": True,
              "product_metadata": {"competition_scope": "Future"},
              "markets": [market("KXNBAFUT-26-CLE", "Cleveland", now + timedelta(days=200))]}
    other = {"event_ticker": "POL-1", "series_ticker": "POLITICS", "markets": [
        market("POL-1-A", "A", now + timedelta(hours=1))]}
    settled = game_event("KXNBAGAME-E0", "KXNBAGAME", ["Utah", "Denver"], now - timedelta(hours=8),
                         status="finalized", result="yes", settlement_value="1.0000")
    sers = [series("KXNBAGAME"), series("KXEPLGAME", ("Soccer",)), series("KXNBAFUT")]
    return sers, [[soon, later, other], [soccer, future, settled]]


def counts(s, sql, *a):
    return s.con.execute(sql, list(a)).fetchone()[0]


def lines():
    out, err = [], []
    return out, err, out.append, err.append


def quiet(_m):
    pass


def odds_world(store, now, priced=None, extra_stubs=None, sports=None):
    stubs = {"basketball_nba": [stub("R1", "Philadelphia 76ers", "Cleveland Cavaliers", START),
                                stub("R2", "Los Angeles Lakers", "Golden State Warriors", START)]}
    for key, ss in (extra_stubs or {}).items():
        stubs.setdefault(key, []).extend(ss)
    priced = priced or {"R1": {"Philadelphia 76ers": D("1.55"), "Cleveland Cavaliers": D("2.60")}}
    sports = sports or [ref_sport(), ref_sport("esports_x", "Esports")]
    return FakeOdds(store, sports, stubs, priced, now=now)


def daily_with_odds(client, **kw):
    made = {}

    def factory(store):
        made["src"] = odds_world(store, DAILY_AT, **kw)
        return made["src"]

    out, err, o, e = lines()
    summary = run_cycle(client, factory, lambda: DAILY_AT, o, e)
    return summary, made["src"], out, err


# Kinds ------------------------------------------------------------------------

def test_first_run_is_daily_and_snapshots_every_sports_market():
    sers, pages = world()
    out, err, o, e = lines()
    summary = run_cycle(FakeKalshi(sers, pages, NEAR_AT), lambda st: None, lambda: NEAR_AT, o, e)
    assert summary["tier"] == "daily"
    with Store.open() as s:
        tickers = {t for (t,) in s.con.execute("SELECT ticker FROM price_snapshots").fetchall()}
        assert "POL-1-A" not in tickers
        assert len(tickers) == 2 + 2 + 3 + 1 + 2
        assert counts(s, "SELECT count(*) FROM settlements") == 2
    assert any(m.startswith("ODDS NOT CONFIGURED") for m in out)
    assert any(m.startswith("SNAPSHOT OK") for m in out)
    assert "matched markets by sport: none yet" in out


def test_exchange_closed_writes_nothing_and_is_not_a_failure():
    sers, pages = world()
    fake = FakeKalshi(sers, pages, DAILY_AT, active=False)
    out, err, o, e = lines()
    summary = run_cycle(fake, lambda st: None, lambda: DAILY_AT, o, e)
    assert summary["tier"] == "exchange_closed" and not fake.scanned
    assert out[0].startswith("EXCHANGE CLOSED")
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM price_snapshots") == 0
        assert s.con.execute("SELECT status FROM job_runs WHERE phase='finish'").fetchall() == [
            ("exchange_closed",)]


def test_daily_catch_up_when_last_daily_is_old():
    sers, pages = world()
    with Store.open() as s:
        s.job_event("r0", "snapshot", "finish", NEAR_AT - timedelta(hours=25), "ok", {"tier": "daily"})
        s.upsert("series", [{"series_ticker": "KXNBAGAME", "sport": "Basketball", "tags": '["Basketball"]',
                             "fee_type": "quadratic", "fee_multiplier": 1, "first_seen_utc": NEAR_AT,
                             "last_seen_utc": NEAR_AT}], "series_ticker")
    assert run_cycle(FakeKalshi(sers, pages, NEAR_AT), lambda st: None, lambda: NEAR_AT,
                     quiet, quiet)["tier"] == "daily"


def test_empty_scan_fails_and_is_recorded():
    sers, _ = world()
    with pytest.raises(ScanEmpty):
        run_cycle(FakeKalshi(sers, [[]], DAILY_AT), lambda st: None, lambda: DAILY_AT, quiet, quiet)
    with Store.open() as s:
        assert s.con.execute("SELECT status FROM job_runs WHERE phase='finish'").fetchall() == [("failed",)]


def test_failure_mid_pagination_writes_no_snapshots():
    sers, pages = world()
    with pytest.raises(RuntimeError):
        run_cycle(FakeKalshi(sers, pages, DAILY_AT, fail_on_page=1), lambda st: None, lambda: DAILY_AT,
                  quiet, quiet)
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM price_snapshots") == 0
        assert counts(s, "SELECT count(*) FROM markets") == 0


# Matched-only fetches between daily scans --------------------------------------

def test_hourly_fetches_only_matched_events(tmp_path):
    fee_table(tmp_path)
    sers, pages = world()
    daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))
    fake = FakeKalshi(sers, pages, HOURLY_AT)
    out, err, o, e = lines()
    summary = run_cycle(fake, lambda st: odds_world(st, HOURLY_AT), lambda: HOURLY_AT, o, e)
    assert summary["tier"] == "hourly" and not fake.scanned
    assert fake.ticker_requests == [["KXNBAGAME-E1"]]
    with Store.open() as s:
        rows = s.con.execute("SELECT DISTINCT ticker FROM price_snapshots WHERE tier = 'hourly'").fetchall()
    assert {t for (t,) in rows} == {"KXNBAGAME-E1-CLE", "KXNBAGAME-E1-PHI"}
    assert "matched markets by sport: Basketball=2" in out


def test_near_only_matched_markets_resolving_within_48h(tmp_path):
    fee_table(tmp_path)
    sers, pages = world()
    daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))
    with Store.open() as s:   # a second matched event far in the future
        s.record_matches([{"market_ticker": "KXNBAGAME-E2-BOS", "event_ticker": "KXNBAGAME-E2",
                           "source": "the-odds-api", "source_event_id": "R9", "ref_outcome": "Boston Celtics",
                           "rule_version": "v", "sport_key": "basketball_nba"}], DAILY_AT)
    fake = FakeKalshi(sers, pages, NEAR_AT)
    summary = run_cycle(fake, lambda st: odds_world(st, NEAR_AT), lambda: NEAR_AT, quiet, quiet)
    assert summary["tier"] == "near" and fake.ticker_requests == [["KXNBAGAME-E1"]]
    fake2 = FakeKalshi(sers, pages, HOURLY_AT + timedelta(hours=1))
    run_cycle(fake2, lambda st: odds_world(st, HOURLY_AT + timedelta(hours=1)),
              lambda: HOURLY_AT + timedelta(hours=1), quiet, quiet)
    assert fake2.ticker_requests == [["KXNBAGAME-E1", "KXNBAGAME-E2"]]


def test_no_matches_means_no_fetch_and_says_why():
    sers, pages = world()
    run_cycle(FakeKalshi(sers, pages, DAILY_AT), lambda st: None, lambda: DAILY_AT, quiet, quiet)
    fake = FakeKalshi(sers, pages, NEAR_AT)
    out, err, o, e = lines()
    summary = run_cycle(fake, lambda st: None, lambda: NEAR_AT, o, e)
    assert summary["tier"] == "near" and summary["snapshot_rows"] == 0 and fake.ticker_requests == []
    assert any(m.startswith("matched events: 0 (no odds matches yet)") for m in out)


def test_matched_fetch_records_settlement(tmp_path):
    fee_table(tmp_path)
    sers, pages = world()
    daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))
    done = game_event("KXNBAGAME-E1", "KXNBAGAME", ["Cleveland", "Philadelphia"], START,
                      status="finalized", result="no", settlement_value="0.0000")
    fake = FakeKalshi(sers, [], HOURLY_AT, by_ticker={"KXNBAGAME-E1": done})
    summary = run_cycle(fake, lambda st: odds_world(st, HOURLY_AT), lambda: HOURLY_AT, quiet, quiet)
    assert summary["settlements_from_matched"] == 2


def test_settlement_sweep_only_for_known_markets():
    sers, pages = world()
    swept = [{"ticker": "KXNBAGAME-E1-CLE", "status": "finalized", "result": "no",
              "settlement_value_dollars": "0.0000", "settlement_ts": "2026-10-22T05:00:00Z"},
             {"ticker": "UNKNOWN-1", "status": "finalized", "result": "yes"}]
    summary = run_cycle(FakeKalshi(sers, pages, DAILY_AT, settled=swept), lambda st: None,
                        lambda: DAILY_AT, quiet, quiet)
    assert summary["settlements_swept"] == 1


def test_lookups_skip_markets_still_open():
    now = DAILY_AT
    past = now - timedelta(hours=12)
    rows = [{"ticker": t, "status": st, "resolve_by": past, "last_seen_utc": seen, "first_seen_utc": seen}
            for t, st, seen in (("OPEN-1", "active", now - timedelta(minutes=10)),
                                ("GONE-1", "active", now - timedelta(hours=5)),
                                ("CLOSED-1", "closed", now - timedelta(minutes=10)))]
    fake = FakeKalshi([], [], now, lookups={"GONE-1": {"ticker": "GONE-1", "status": "finalized",
                                                      "result": "no", "settlement_value_dollars": "0"}})
    with Store.open() as s:
        s.upsert("markets", rows, "ticker")
        res = sweep_settlements(s, fake, now, set())
        assert res["settlement_lookups"] == 2 and res["settlements_swept"] == 1


# Odds step -----------------------------------------------------------------------

def test_odds_step_matches_prices_and_logs_shadow_rows(tmp_path):
    fee_table(tmp_path)
    sers, pages = world()
    pages[0][0]["markets"][0].update({"yes_ask_dollars": "0.2000", "yes_bid_dollars": "0.1900"})
    pages[0][0]["markets"][1].update({"yes_ask_dollars": "0.6300", "no_ask_dollars": "0.3800"})
    summary, src, out, err = daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))
    assert summary["events_matched"] == 1 and summary["events_priced"] == 1
    assert src.priced_calls == [("basketball_nba", ["R1"])]
    assert any("UNMAPPED GROUP Esports" in m for m in out)
    rows = ledger.read_rows(config.shadow_ledger_path())
    assert [(r["market_ticker"], r["side"]) for r in rows] == [("KXNBAGAME-E1-CLE", "YES")]
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM odds_matches WHERE sport_key = 'basketball_nba'") == 2
        prop, power = s.con.execute("SELECT fair_prob, fair_prob_power FROM reference_odds "
                                    "WHERE outcome = 'Philadelphia 76ers'").fetchone()
        assert power > prop          # favourite: power de-vig gives it more than proportional


def test_without_fee_table_no_shadow_rows():
    sers, pages = world()
    pages[0][0]["markets"][0].update({"yes_ask_dollars": "0.2000", "yes_bid_dollars": "0.1900"})
    summary, src, out, err = daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))
    assert summary["shadow_rows"] == 0 and summary["skip_fee_unknown"] == 2
    assert any(m.startswith("FEE UNKNOWN") for m in out)


def test_unmatched_printed_once_per_event_and_reason():
    sers, pages = world()
    for m in pages[0][0]["markets"]:
        if m["yes_sub_title"] == "Cleveland":
            m["yes_sub_title"] = "Cleveland C"
    summary, _, out, err = daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))
    later = DAILY_AT + timedelta(hours=1, minutes=5)
    out2, err2, o2, e2 = lines()
    summary2 = run_cycle(FakeKalshi(sers, pages, later), lambda st: odds_world(st, later), lambda: later, o2, e2)
    first = [m for m in err if m.startswith("UNMATCHED KXNBAGAME-E1 ")]
    assert len(first) == 1 and "reason=name_mismatch markets=2" in first[0]
    assert not any(m.startswith("UNMATCHED KXNBAGAME-E1 ") for m in err2)
    assert summary2["unmatched"] >= 1


def test_budget_stops_paid_calls(monkeypatch):
    monkeypatch.setenv("ODDS_DAILY_CREDITS", "0")
    sers, pages = world()
    summary, src, out, err = daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))
    assert summary["budget_reached"] is True and src.priced_calls == []
    assert any(m.startswith("ODDS BUDGET REACHED") for m in out)


def test_priced_outcome_set_mismatch_is_unmatched(tmp_path):
    fee_table(tmp_path)
    sers, pages = world()
    priced = {"R1": {"Philadelphia 76ers": D("1.6"), "Cleveland Cavaliers": D("3.0"), "Draw": D("15")}}
    summary, src, out, err = daily_with_odds(FakeKalshi(sers, pages, DAILY_AT), priced=priced)
    assert any("reason=outcome_count" in m for m in err)
    assert ledger.read_rows(config.shadow_ledger_path()) == []


def test_odds_interval_not_due_skips_calls():
    sers, pages = world()
    daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))
    made = {}

    def factory(store):
        made["src"] = odds_world(store, NEAR_AT)
        return made["src"]

    later = DAILY_AT + timedelta(minutes=15)
    summary = run_cycle(FakeKalshi(sers, pages, later), factory, lambda: later, quiet, quiet)
    assert summary["odds"] == "interval_not_due" and made["src"].priced_calls == []


def test_game_in_progress_is_matched_not_priced():
    sers, _ = world()
    started = DAILY_AT - timedelta(hours=1)
    ev = game_event("KXNBAGAME-L1", "KXNBAGAME", ["Cleveland", "Philadelphia"], started)
    made = {}

    def factory(store):
        made["src"] = FakeOdds(store, [ref_sport()], {"basketball_nba": [
            stub("RL", "Philadelphia 76ers", "Cleveland Cavaliers", started)]}, {}, now=DAILY_AT)
        return made["src"]

    out, err, o, e = lines()
    summary = run_cycle(FakeKalshi(sers, [[ev]], DAILY_AT), factory, lambda: DAILY_AT, o, e)
    assert not any(m.startswith("UNMATCHED") for m in err)
    assert summary["events_matched"] == 1 and made["src"].priced_calls == []


def test_missing_cost_header_still_counts_against_budget(monkeypatch):
    from sports_edge.odds_step import build_odds_source
    monkeypatch.setenv("ODDS_API_KEY", "k")
    with Store.open() as s:
        src = build_odds_source(s)
        src.on_call(sport_key="x", endpoint="odds", http_status=200, credits_last=None,
                    credits_used=None, credits_remaining=None)
        src.on_call(sport_key="x", endpoint="events", http_status=200, credits_last=None,
                    credits_used=None, credits_remaining=None)
        assert s.credits_used_since(DAILY_AT - timedelta(days=3650)) == 1


# Targeted Kalshi lookups -------------------------------------------------------------

def test_reference_game_without_kalshi_match_triggers_series_lookup():
    sers, pages = world()
    daily_with_odds(FakeKalshi(sers, pages, DAILY_AT))          # learns KXNBAGAME <-> basketball_nba
    new_start = HOURLY_AT + timedelta(hours=6)
    new_event = game_event("KXNBAGAME-E5", "KXNBAGAME", ["Boston", "Miami"], new_start)
    extra = {"basketball_nba": [stub("R5", "Miami Heat", "Boston Celtics", new_start)]}
    fake = FakeKalshi(sers, [], HOURLY_AT, by_series={"KXNBAGAME": [new_event]},
                      by_ticker={**{e["event_ticker"]: e for p in pages for e in p}, "KXNBAGAME-E5": new_event})
    out, err, o, e = lines()
    run_cycle(fake, lambda st: odds_world(st, HOURLY_AT, extra_stubs=extra), lambda: HOURLY_AT, o, e)
    assert fake.series_requests == ["KXNBAGAME"]
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM odds_matches WHERE event_ticker = 'KXNBAGAME-E5'") == 2
    assert not any(m.startswith("UNMATCHED-REFERENCE R5") for m in err)
    assert ["KXNBAGAME-E1", "KXNBAGAME-E5"] in fake.ticker_requests   # snapshotted this run


def test_unknown_sport_key_is_unmatched_reference_once():
    sers, pages = world()
    hockey_start = DAILY_AT + timedelta(hours=4)
    extra = {"icehockey_nhl": [stub("H1", "Boston Bruins", "New York Rangers", hockey_start, "icehockey_nhl")]}
    sports = [ref_sport(), ref_sport("icehockey_nhl", "Ice Hockey")]
    summary, src, out, err = daily_with_odds(FakeKalshi(sers, pages, DAILY_AT), extra_stubs=extra, sports=sports)
    lines_h1 = [m for m in err if m.startswith("UNMATCHED-REFERENCE H1 ")]
    assert len(lines_h1) == 1 and "reason=no_kalshi_series_known" in lines_h1[0]
    later = DAILY_AT + timedelta(hours=1, minutes=5)
    out2, err2, o2, e2 = lines()
    run_cycle(FakeKalshi(sers, pages, later),
              lambda st: odds_world(st, later, extra_stubs=extra, sports=sports), lambda: later, o2, e2)
    assert not any(m.startswith("UNMATCHED-REFERENCE H1 ") for m in err2)


def test_budget_assumes_the_dearest_call_seen(monkeypatch):
    # cap 3; one earlier paid call cost 3 -> the next call (worst case 3) must not be made
    monkeypatch.setenv("ODDS_DAILY_CREDITS", "3")
    sers, pages = world()
    made = {}

    def factory(store):
        store.append("odds_calls", [{"ts_utc": DAILY_AT - timedelta(days=2), "sport_key": "x",
                                     "endpoint": "odds", "http_status": 200, "credits_last": 3}])
        made["src"] = odds_world(store, DAILY_AT)
        return made["src"]

    run_cycle(FakeKalshi(sers, pages, DAILY_AT), factory, lambda: DAILY_AT, quiet, quiet)
    assert made["src"].priced_calls == [("basketball_nba", ["R1"])]       # 0 + 3 <= 3: allowed
    later = DAILY_AT + timedelta(hours=1, minutes=5)
    made2 = {}

    def factory2(store):
        made2["src"] = odds_world(store, later)
        return made2["src"]

    run_cycle(FakeKalshi(sers, pages, later), factory2, lambda: later, quiet, quiet)
    assert made2["src"].priced_calls == []                                  # 1 + 3 > 3: refused
