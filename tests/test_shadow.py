from datetime import timedelta
from decimal import Decimal as D

import pytest

from helpers import fee_table
from sports_edge import config, ledger, shadow
from sports_edge.fees import fee_for_order
from sports_edge.shadow import HEADER, Q6, evaluate, run_shadow
from sports_edge.store import Store
from sports_edge.timeutil import parse_utc

NOW = parse_utc("2026-10-22T18:00:00Z")
COMMENCE = parse_utc("2026-10-22T23:00:00Z")
SETTINGS = {"threshold": D("0.03"), "stake": D("10"), "max_age_min": 15}
FEE_42 = D("0.3922") / 23        # 23 contracts at 0.42: 0.07*23*0.42*0.58 = 0.392196, debit rounded up to 0.0001
MARKET = {"m_taker": D("1"), "precision": D("0.0001")}


def snap(yes_ask="0.42", no_ask="0.59", status="active", ts=NOW, yes_ask_size="500", yes_bid_size="500"):
    return {"status": status, "yes_ask": D(yes_ask), "no_ask": D(no_ask), "yes_bid": D("0.41"),
            "no_bid": D("0.58"), "ts_utc": ts,
            "yes_ask_size": D(yes_ask_size) if yes_ask_size is not None else None,
            "yes_bid_size": D(yes_bid_size) if yes_bid_size is not None else None}


def ref(fair, fetched=NOW, commence=COMMENCE, book=None):
    return {"fetched_utc": fetched, "commence_utc": commence,
            "book_last_update_utc": book if book is not None else fetched - timedelta(minutes=2),
            "fair": {"A": fair}, "fair_power": {"A": fair}}


def ev(s, r, market=MARKET, settings=SETTINGS, book=None):
    return evaluate(s, market, {"ref_outcome": "A"}, r, settings, book)


# Gating -------------------------------------------------------------------------------

def test_yes_side_logged_when_fair_well_above_ask():
    rows, reason, depth = ev(snap(), ref(0.50))
    # $10 at 0.42 buys floor(10 / 0.42) = 23 contracts; the fee is for that order size
    assert reason is None and depth == 0 and [r["side"] for r in rows] == ["YES"]
    assert rows[0]["contracts_assumed"] == 23 and rows[0]["fill_detail"] == "0.42x23"
    assert rows[0]["fee_per_contract"] == FEE_42.quantize(Q6) == D("0.017052")
    assert rows[0]["edge"] == (D("0.500000") - D("0.42") - FEE_42).quantize(Q6)
    assert rows[0]["depth_contracts"] == D("500")


@pytest.mark.parametrize("price,contracts", [("0.42", 23), ("0.05", 200), ("0.99", 10), ("0.34", 29)])
def test_contracts_are_the_stake_divided_by_the_price_rounded_down(price, contracts):
    assert shadow.contracts_for(D("10"), D(price)) == contracts


def test_no_side_logged_when_fair_well_below():
    rows, _, _ = ev(snap(), ref(0.30))
    assert [r["side"] for r in rows] == ["NO"]


def test_threshold_is_inclusive_at_three_cents():
    assert [r["side"] for r in ev(snap(), ref(0.467052))[0]] == ["YES"]   # gap rounds to exactly 0.030000
    assert ev(snap(), ref(0.467052))[0][0]["edge"] == D("0.030000")
    assert ev(snap(), ref(0.467051))[0] == []                                # 0.029999


@pytest.mark.parametrize("s,r,reason", [
    (snap(status="closed"), ref(0.9), "not_active"),
    (snap(yes_ask="0"), ref(0.9), "no_two_sided_quote"),
    (snap(no_ask="1"), ref(0.9), "no_two_sided_quote"),
    (snap(), ref(0.9, fetched=NOW - timedelta(minutes=16)), "reference_stale"),
    (snap(), ref(0.9, fetched=NOW + timedelta(minutes=16)), "reference_stale"),
    (snap(ts=COMMENCE), ref(0.9, fetched=COMMENCE - timedelta(minutes=5)), "not_pre_game"),
])
def test_skip_conditions(s, r, reason):
    assert ev(s, r) == ([], reason, 0)


def test_fetch_age_at_exactly_fifteen_minutes_still_passes():
    assert ev(snap(), ref(0.9, fetched=NOW - timedelta(minutes=15)))[1] is None


@pytest.mark.parametrize("book", [NOW - timedelta(hours=6), NOW - timedelta(days=3), None])
def test_book_last_update_age_never_gates(book):
    r = ref(0.50)
    r["book_last_update_utc"] = book
    rows, reason, _ = ev(snap(), r)
    assert reason is None and [x["side"] for x in rows] == ["YES"]


def test_fee_error_skips():
    assert ev(snap(), ref(0.9), market={"fee_error": "no fee table yet"}) == ([], "fee_unknown", 0)


@pytest.mark.parametrize("ask_size,bid_size,fair,expect_rows,expect_skips", [
    ("22", "500", 0.50, [], 1),          # YES needs 23 at 0.42; 22 at the ask and no book
    ("23", "500", 0.50, ["YES"], 0),     # exactly enough
    ("500", "15", 0.30, [], 1),          # NO needs floor(10 / 0.59) = 16, against the YES bid size
    ("500", "16", 0.30, ["NO"], 0),
    (None, "500", 0.50, [], 1),          # unknown depth is not enough
])
def test_depth_gates_each_side_on_the_right_size(ask_size, bid_size, fair, expect_rows, expect_skips):
    rows, reason, skips = ev(snap(yes_ask_size=ask_size, yes_bid_size=bid_size), ref(fair))
    assert [r["side"] for r in rows] == expect_rows and skips == expect_skips


def test_thin_book_without_edge_is_not_counted_as_depth_skip():
    assert ev(snap(yes_ask_size="1", yes_bid_size="1"), ref(0.45)) == ([], None, 0)


# Walking the book ---------------------------------------------------------------------

def book_of(no_bids=(), yes_bids=()):
    book = {"yes": [(D(p), D(n)) for p, n in yes_bids], "no": [(D(p), D(n)) for p, n in no_bids]}
    calls = []

    def side_levels(side):
        calls.append(side)
        return shadow.asks_from_book(book, side)
    return side_levels, calls


def test_short_best_level_walks_the_book_and_prices_the_average():
    book, calls = book_of(no_bids=[("0.57", "50"), ("0.58", "10")])   # Yes asks 0.42 x10, then 0.43
    rows, reason, skips = ev(snap(yes_ask_size="10"), ref(0.50), book=book)
    fee = (fee_for_order(D("0.42"), 10, 1, "taker", D("0.0001"))
           + fee_for_order(D("0.43"), 13, 1, "taker", D("0.0001"))) / 23
    avg = (D("0.42") * 10 + D("0.43") * 13) / 23
    assert calls == ["YES"] and skips == 0 and reason is None
    r = rows[0]
    assert r["fill_detail"] == "0.42x10;0.43x13" and r["contracts_assumed"] == 23
    assert r["price_paid"] == avg.quantize(Q6) and r["fee_per_contract"] == fee.quantize(Q6)
    assert r["edge"] == (D("0.5") - avg - fee).quantize(Q6)


def test_book_too_thin_to_fill_the_stake_is_a_depth_skip():
    book, _ = book_of(no_bids=[("0.58", "10"), ("0.57", "12")])       # 22 of 23
    assert ev(snap(yes_ask_size="10"), ref(0.50), book=book) == ([], None, 1)


def test_walk_that_pushes_the_gap_under_three_cents_writes_nothing():
    book, _ = book_of(no_bids=[("0.58", "1"), ("0.40", "100")])       # 1 at 0.42, then 0.60
    assert ev(snap(yes_ask_size="1"), ref(0.47), book=book) == ([], None, 1)


def test_book_is_never_read_when_the_best_price_has_no_gap():
    book, calls = book_of(no_bids=[("0.58", "1")])
    assert ev(snap(yes_ask_size="1", yes_bid_size="1"), ref(0.45), book=book) == ([], None, 0)
    assert calls == []


def test_no_side_walks_against_yes_bids():
    book, calls = book_of(yes_bids=[("0.41", "5"), ("0.40", "100")])  # No asks 0.59 x5, then 0.60
    rows, _, _ = ev(snap(yes_bid_size="5"), ref(0.30), book=book)
    assert calls == ["NO"] and rows[0]["side"] == "NO" and rows[0]["fill_detail"] == "0.59x5;0.6x11"


# Ledger rows -----------------------------------------------------------------------

def seed(store, fair_a=0.60, fair_b=0.40, ref_outcomes=("Cleveland Cavaliers", "Philadelphia 76ers"),
         fetched=NOW, earlier=None):
    store.upsert("markets", [
        {"ticker": t, "event_ticker": "E1", "series_ticker": "S", "sport": "Basketball",
         "competition": "Pro Basketball (M)", "yes_sub_title": title, "status": "active",
         "fee_type": "quadratic", "fee_multiplier": 1, "fee_override": False,
         "first_seen_utc": NOW, "last_seen_utc": NOW}
        for t, title in (("E1-CLE", "Cleveland"), ("E1-PHI", "Philadelphia"))], "ticker")
    store.record_matches([
        {"market_ticker": "E1-CLE", "event_ticker": "E1", "source": "the-odds-api",
         "source_event_id": "R1", "ref_outcome": "Cleveland Cavaliers", "rule_version": "v"},
        {"market_ticker": "E1-PHI", "event_ticker": "E1", "source": "the-odds-api",
         "source_event_id": "R1", "ref_outcome": "Philadelphia 76ers", "rule_version": "v"}], NOW)
    fetches = [(fetched, (fair_a, fair_b))] + ([earlier] if earlier else [])
    for when, (fa, fb) in fetches:
        store.append("reference_odds", [
            {"fetched_utc": when, "source": "the-odds-api", "bookmaker": "pinnacle", "sport_key": "nba",
             "source_event_id": "R1", "commence_utc": COMMENCE, "home": "Philadelphia 76ers",
             "away": "Cleveland Cavaliers", "outcome": o, "decimal_odds": D("2"), "fair_prob": p,
             "fair_prob_power": p - 0.01, "overround": 1.02, "devig_method": "proportional",
             "book_last_update_utc": when - timedelta(minutes=3)}
            for o, p in zip(ref_outcomes, (fa, fb))])


def both_snaps():
    return {"E1-CLE": snap(yes_ask="0.42", no_ask="0.59"), "E1-PHI": snap(yes_ask="0.58", no_ask="0.43")}


def test_run_shadow_writes_complete_v2_rows(tmp_path):
    fee_table(tmp_path)
    with Store.open() as s:
        seed(s)
        res = run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out=lambda m: None,
                         checked_events={"R1"})
    rows = ledger.read_rows(config.shadow_ledger_path())
    assert res["shadow_rows"] == len(rows) == 2
    assert {(r["market_ticker"], r["side"]) for r in rows} == {("E1-CLE", "YES"), ("E1-PHI", "NO")}
    assert list(rows[0].keys()) == HEADER
    r = next(r for r in rows if r["side"] == "YES")
    assert r["rule_id"] == "GAP_v2" and r["schema_version"] == "3"
    assert r["stake_dollars"] == "10" and r["contracts_assumed"] == "23" and r["fill_detail"] == "0.42x23"
    assert r["odds_fetch_age_s"] == "0" and r["book_update_age_s"] == "180"
    assert r["fee_precision"] == "0.0001" and r["ref_outcome"] == "Cleveland Cavaliers"
    assert r["depth_contracts"] == "500" and r["minutes_to_start"] == "300.0"
    assert float(r["fair_prob_power"]) == pytest.approx(0.59)
    no = next(r for r in rows if r["side"] == "NO")
    assert no["depth_contracts"] == "500" and no["yes_bid_size"] == "500"


def test_one_hour_moves_recorded(tmp_path):
    fee_table(tmp_path)
    with Store.open() as s:
        seed(s, earlier=(NOW - timedelta(minutes=70), (0.55, 0.45)))
        s.append("price_snapshots", [{"run_id": "r0", "ts_utc": NOW - timedelta(minutes=65), "tier": "hourly",
                                      "ticker": "E1-CLE", "status": "active", "yes_bid": D("0.35"),
                                      "yes_ask": D("0.37")}])
        run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out=lambda m: None, checked_events={"R1"})
    r = next(r for r in ledger.read_rows(config.shadow_ledger_path()) if r["market_ticker"] == "E1-CLE")
    assert D(r["kalshi_mid"]) == D("0.415") and D(r["kalshi_move_1h"]) == D("0.415") - D("0.36")
    assert float(r["ref_fair_1h_ago"]) == pytest.approx(0.55) and float(r["ref_move_1h"]) == pytest.approx(0.05)


def test_moves_blank_without_an_earlier_point(tmp_path):
    fee_table(tmp_path)
    with Store.open() as s:
        seed(s, earlier=(NOW - timedelta(minutes=30), (0.55, 0.45)))   # too recent for the 60-120 min window
        run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out=lambda m: None, checked_events={"R1"})
    r = next(r for r in ledger.read_rows(config.shadow_ledger_path()) if r["market_ticker"] == "E1-CLE")
    assert r["kalshi_move_1h"] == "" and r["ref_move_1h"] == "" and r["ref_fair_1h_ago"] == ""


def test_old_book_line_still_writes_and_records_its_age(tmp_path):
    fee_table(tmp_path)
    with Store.open() as s:
        seed(s)
        s.con.execute("UPDATE reference_odds SET book_last_update_utc = ?",
                      [(NOW - timedelta(hours=5)).replace(tzinfo=None)])
        res = run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out=lambda m: None, checked_events={"R1"})
    rows = ledger.read_rows(config.shadow_ledger_path())
    assert res["shadow_rows"] == 2 and "skip_book_line_stale" not in res
    assert {r["book_update_age_s"] for r in rows} == {str(5 * 3600)}


def test_record_only_fields_never_gate(tmp_path, monkeypatch):
    """Same signal inputs, wildly different record-only inputs: identical rows written."""
    fee_table(tmp_path)

    def written(**seed_kw):
        p = config.shadow_ledger_path()
        if p.exists():
            p.unlink()
        with Store.open() as s:
            s.con.execute("DELETE FROM reference_odds"); s.con.execute("DELETE FROM price_snapshots")
            s.con.execute("DELETE FROM odds_matches"); s.con.execute("DELETE FROM markets")
            seed(s, **seed_kw)
            if seed_kw.get("_snap_history"):
                s.append("price_snapshots", [{"run_id": "r0", "ts_utc": NOW - timedelta(minutes=90),
                                              "tier": "hourly", "ticker": "E1-CLE", "status": "active",
                                              "yes_bid": D("0.01"), "yes_ask": D("0.02")}])
            run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out=lambda m: None, checked_events={"R1"})
        return sorted((r["market_ticker"], r["side"], r["edge"]) for r in ledger.read_rows(p))

    seed_kw_base = {}
    base = written(**seed_kw_base)
    assert base
    huge_move = written(earlier=(NOW - timedelta(minutes=90), (0.01, 0.99)))
    import sports_edge.shadow as sh
    monkeypatch.setattr(sh, "MOVE_WINDOW", (timedelta(0), timedelta(days=30)))
    wide = written(earlier=(NOW - timedelta(minutes=90), (0.99, 0.01)))
    assert base == huge_move == wide


def test_no_fee_table_writes_nothing_and_says_why():
    out = []
    with Store.open() as s:
        seed(s)
        res = run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out.append, checked_events={"R1"})
    assert res["shadow_rows"] == 0 and res["skip_fee_unknown"] == 2
    assert any(m.startswith("FEE UNKNOWN (2 market checks): no fee table yet") for m in out)


def test_outcome_set_mismatch_never_compared(tmp_path):
    fee_table(tmp_path)
    with Store.open() as s:
        seed(s, ref_outcomes=("Cleveland Cavaliers", "Draw"))
        res = run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out=lambda m: None, checked_events={"R1"})
    assert res["shadow_rows"] == 0 and res["skip_outcome_count"] == 2


def test_conflicted_event_never_compared(tmp_path):
    fee_table(tmp_path)
    with Store.open() as s:
        seed(s)
        s.record_matches([{"market_ticker": "E1-CLE", "event_ticker": "E1", "source": "the-odds-api",
                           "source_event_id": "R9", "ref_outcome": "X", "rule_version": "v"}], NOW)
        res = run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out=lambda m: None, checked_events={"R1"})
    assert res["shadow_rows"] == 0 and res["skip_match_conflict"] == 2


def test_rule_identity(monkeypatch):
    assert shadow.rule_id() == "GAP_v2"
    assert shadow.RULE_DEFAULTS["ODDS_MAX_AGE_MIN"] == "15"
    monkeypatch.setenv("ODDS_MAX_AGE_MIN", "75")
    assert shadow.rule_id() == "GAP_v2+custom(ODDS_MAX_AGE_MIN=75)"


def test_config_holds_no_signal_parameter():
    src = (config.REPO_ROOT / "sports_edge" / "config.py").read_text()
    for name in ("THRESHOLD", "SHADOW_CONTRACTS", "SHADOW_STAKE", "RULE_ID", "ODDS_MAX_AGE"):
        assert name not in src


def test_only_games_checked_in_this_run_are_compared(tmp_path):
    fee_table(tmp_path)
    with Store.open() as s:
        seed(s)
        res = run_shadow(s, "run-1", both_snaps(), NOW, "the-odds-api", out=lambda m: None,
                         checked_events={"R-other"})
    assert res["shadow_rows"] == 0 and res["skip_not_checked_this_run"] == 2
    assert ledger.read_rows(config.shadow_ledger_path()) == []


def test_run_shadow_reads_the_book_only_for_a_short_level(tmp_path):
    fee_table(tmp_path)
    asked = []

    def book_fn(ticker, depth):
        asked.append((ticker, depth))
        return {"yes": [], "no": [(D("0.58"), D("10")), (D("0.57"), D("50"))]}

    snaps = both_snaps()
    snaps["E1-CLE"]["yes_ask_size"] = D("10")
    with Store.open() as s:
        seed(s)
        run_shadow(s, "run-1", snaps, NOW, "the-odds-api", out=lambda m: None, checked_events={"R1"},
                   book_fn=book_fn)
    assert asked == [("E1-CLE", shadow.BOOK_DEPTH)]
    r = next(r for r in ledger.read_rows(config.shadow_ledger_path()) if r["market_ticker"] == "E1-CLE")
    assert r["fill_detail"] == "0.42x10;0.43x13"
