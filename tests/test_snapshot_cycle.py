from datetime import timedelta
from decimal import Decimal as D

import pytest

from helpers import FakeKalshi, FakeOdds, game_event, market, ref_sport, series, stub
from sports_edge import config, ledger
from sports_edge.snapshot import ScanEmpty, run_cycle
from sports_edge.store import Store
from sports_edge.timeutil import parse_utc

# 18:00 PDT = :00 -> full tier; 18:15 PDT -> near tier.
FULL_AT = parse_utc("2026-10-22T01:00:00Z")
NEAR_AT = parse_utc("2026-10-22T01:15:00Z")


def world(now, odds_start_h=5):
    start = now + timedelta(hours=odds_start_h)
    soon = game_event("KXNBAGAME-E1", "KXNBAGAME", ["Cleveland", "Philadelphia"], start)
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


def counts(s, sql):
    return s.con.execute(sql).fetchone()[0]


def lines():
    out, err = [], []
    return out, err, (lambda m: out.append(m)), (lambda m: err.append(m))


def test_full_tier_snapshots_every_sports_market_and_ignores_other_categories():
    sers, pages = world(FULL_AT)
    out, err, o, e = lines()
    summary = run_cycle(FakeKalshi(sers, pages, FULL_AT), lambda st: None, lambda: FULL_AT, o, e)
    assert summary["tier"] == "full"
    with Store.open() as s:
        tickers = {t for (t,) in s.con.execute("SELECT ticker FROM price_snapshots").fetchall()}
        assert "POL-1-A" not in tickers
        assert len(tickers) == 2 + 2 + 3 + 1 + 2   # soon, later, soccer, future, settled
        assert counts(s, "SELECT count(*) FROM settlements") == 2
        assert counts(s, "SELECT count(DISTINCT sport) FROM markets") == 2
    assert any(m.startswith("ODDS NOT CONFIGURED") for m in out)
    assert any(m.startswith("SNAPSHOT OK") for m in out)


def test_near_tier_only_markets_resolving_within_48h():
    sers, pages = world(NEAR_AT)
    with Store.open() as s:   # a recent successful full run exists
        s.job_event("r0", "snapshot", "finish", NEAR_AT - timedelta(minutes=15), "ok", {"tier": "full"})
        s.upsert("series", [{"series_ticker": x["ticker"], "sport": x["tags"][0],
                             "tags": '["%s"]' % x["tags"][0], "fee_type": "quadratic",
                             "fee_multiplier": 1, "first_seen_utc": NEAR_AT,
                             "last_seen_utc": NEAR_AT} for x in sers], "series_ticker")
    out, err, o, e = lines()
    summary = run_cycle(FakeKalshi(sers, pages, NEAR_AT), lambda st: None, lambda: NEAR_AT, o, e)
    assert summary["tier"] == "near"
    with Store.open() as s:
        tickers = {t for (t,) in s.con.execute("SELECT ticker FROM price_snapshots").fetchall()}
    assert {"KXNBAGAME-E1-CLE", "KXEPLGAME-E3-TIE", "KXNBAGAME-E0-UTA"} <= tickers
    assert not any(t.startswith("KXNBAGAME-E2") or t.startswith("KXNBAFUT") for t in tickers)


def test_resolve_by_uses_expected_expiration_not_late_close_time():
    # close_time is 45 h after expected expiration; a game 30 h out must be "near"
    sers, _ = world(NEAR_AT)
    ev = game_event("KXNBAGAME-E9", "KXNBAGAME", ["Cleveland", "Philadelphia"], NEAR_AT + timedelta(hours=30))
    with Store.open() as s:
        s.job_event("r0", "snapshot", "finish", NEAR_AT - timedelta(minutes=15), "ok", {"tier": "full"})
    o = lambda m: None
    run_cycle(FakeKalshi(sers, [[ev]], NEAR_AT), lambda st: None, lambda: NEAR_AT, o, o)
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM price_snapshots") == 2


def test_catch_up_full_when_no_recent_full_run():
    sers, pages = world(NEAR_AT)
    o = lambda m: None
    assert run_cycle(FakeKalshi(sers, pages, NEAR_AT), lambda st: None, lambda: NEAR_AT, o, o)["tier"] == "full"


def test_empty_scan_fails_and_is_recorded():
    sers, _ = world(FULL_AT)
    o = lambda m: None
    with pytest.raises(ScanEmpty):
        run_cycle(FakeKalshi(sers, [[]], FULL_AT), lambda st: None, lambda: FULL_AT, o, o)
    with Store.open() as s:
        assert s.con.execute("SELECT status FROM job_runs WHERE phase='finish'").fetchall() == [("failed",)]


def test_failure_mid_pagination_writes_no_snapshots():
    sers, pages = world(FULL_AT)
    o = lambda m: None
    with pytest.raises(RuntimeError):
        run_cycle(FakeKalshi(sers, pages, FULL_AT, fail_on_page=1), lambda st: None, lambda: FULL_AT, o, o)
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM price_snapshots") == 0
        assert counts(s, "SELECT count(*) FROM markets") == 0


def test_settlement_sweep_and_lookup_only_for_known_markets():
    sers, pages = world(FULL_AT)
    swept = [{"ticker": "KXNBAGAME-E1-CLE", "status": "finalized", "result": "no",
              "settlement_value_dollars": "0.0000", "settlement_ts": "2026-10-22T05:00:00Z"},
             {"ticker": "UNKNOWN-1", "status": "finalized", "result": "yes"}]
    o = lambda m: None
    summary = run_cycle(FakeKalshi(sers, pages, FULL_AT, settled=swept), lambda st: None,
                        lambda: FULL_AT, o, o)
    assert summary["settlements_swept"] == 1
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM settlements WHERE ticker = 'UNKNOWN-1'") == 0


def odds_world(store, now, priced=None):
    start = now + timedelta(hours=5)
    stubs = {"basketball_nba": [stub("R1", "Philadelphia 76ers", "Cleveland Cavaliers", start),
                                stub("R2", "Los Angeles Lakers", "Golden State Warriors", start)]}
    priced = priced or {"R1": {"Philadelphia 76ers": D("1.55"), "Cleveland Cavaliers": D("2.60")}}
    return FakeOdds(store, [ref_sport(), ref_sport("esports_x", "Esports")], stubs, priced)


def test_odds_step_matches_prices_and_logs_shadow_rows(monkeypatch):
    sers, pages = world(FULL_AT)
    # make Cleveland cheap on Kalshi: fair ~0.38 (2.60) vs ask 0.20
    pages[0][0]["markets"][0].update({"yes_ask_dollars": "0.2000", "yes_bid_dollars": "0.1900"})
    # Philadelphia priced close to fair (0.6265) on both sides: no row
    pages[0][0]["markets"][1].update({"yes_ask_dollars": "0.6300", "no_ask_dollars": "0.3800"})
    out, err, o, e = lines()
    made = {}

    def factory(store):
        made["src"] = odds_world(store, FULL_AT)
        return made["src"]

    summary = run_cycle(FakeKalshi(sers, pages, FULL_AT), factory, lambda: FULL_AT, o, e)
    assert summary["events_matched"] == 1 and summary["events_priced"] == 1
    assert made["src"].priced_calls == [("basketball_nba", ["R1"])]
    assert any("UNMAPPED GROUP Esports" in m for m in out)
    rows = ledger.read_rows(config.shadow_ledger_path())
    assert [(r["market_ticker"], r["side"]) for r in rows] == [("KXNBAGAME-E1-CLE", "YES")]
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM odds_matches") == 2
        assert counts(s, "SELECT count(*) FROM reference_odds") == 2


def test_unmatched_is_loud_and_recorded():
    sers, pages = world(FULL_AT)
    # rename a Kalshi team so the in-window reference no longer fits
    for m in pages[0][0]["markets"]:
        if m["yes_sub_title"] == "Cleveland":
            m["yes_sub_title"] = "Cleveland C"
    out, err, o, e = lines()
    summary = run_cycle(FakeKalshi(sers, pages, FULL_AT), lambda st: odds_world(st, FULL_AT),
                        lambda: FULL_AT, o, e)
    assert summary["unmatched"] >= 1
    assert any(m.startswith("UNMATCHED KXNBAGAME-E1 sport=Basketball reason=name_mismatch") for m in err)
    with Store.open() as s:
        assert counts(s, "SELECT count(*) FROM unmatched WHERE reason = 'name_mismatch'") == 1


def test_budget_stops_paid_calls(monkeypatch):
    monkeypatch.setenv("ODDS_DAILY_CREDITS", "0")
    sers, pages = world(FULL_AT)
    out, err, o, e = lines()
    made = {}

    def factory(store):
        made["src"] = odds_world(store, FULL_AT)
        return made["src"]

    summary = run_cycle(FakeKalshi(sers, pages, FULL_AT), factory, lambda: FULL_AT, o, e)
    assert summary["budget_reached"] is True and made["src"].priced_calls == []
    assert any(m.startswith("ODDS BUDGET REACHED") for m in out)


def test_priced_outcome_set_mismatch_is_unmatched():
    sers, pages = world(FULL_AT)
    out, err, o, e = lines()
    priced = {"R1": {"Philadelphia 76ers": D("1.6"), "Cleveland Cavaliers": D("3.0"), "Draw": D("15")}}
    run_cycle(FakeKalshi(sers, pages, FULL_AT), lambda st: odds_world(st, FULL_AT, priced),
              lambda: FULL_AT, o, e)
    assert any("reason=outcome_count" in m for m in err)
    assert ledger.read_rows(config.shadow_ledger_path()) == []


def test_odds_interval_not_due_skips_calls_but_still_compares():
    sers, pages = world(FULL_AT)
    o = lambda m: None
    run_cycle(FakeKalshi(sers, pages, FULL_AT), lambda st: odds_world(st, FULL_AT), lambda: FULL_AT, o, o)
    later = FULL_AT + timedelta(minutes=15)
    made = {}

    def factory(store):
        made["src"] = odds_world(store, later)
        return made["src"]

    sers, pages = world(later)
    summary = run_cycle(FakeKalshi(sers, pages, later), factory, lambda: later, o, o)
    assert summary["odds"] == "interval_not_due" and made["src"].priced_calls == []
    assert "shadow_checked" in summary
