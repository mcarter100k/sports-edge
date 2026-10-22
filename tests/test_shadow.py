from datetime import timedelta
from decimal import Decimal as D

import pytest

from sports_edge import config, ledger
from sports_edge.shadow import HEADER, evaluate, run_shadow
from sports_edge.store import Store
from sports_edge.timeutil import parse_utc

NOW = parse_utc("2026-10-22T18:00:00Z")
COMMENCE = parse_utc("2026-10-22T23:00:00Z")
SETTINGS = {"threshold": D("0.03"), "contracts": 100, "precision": D("0.01"), "max_age_min": 75}
MARKET = {"fee_type": "quadratic", "fee_multiplier": 1}


def snap(yes_ask="0.42", no_ask="0.59", status="active", ts=NOW):
    return {"status": status, "yes_ask": D(yes_ask), "no_ask": D(no_ask), "yes_bid": D("0.41"),
            "no_bid": D("0.58"), "ts_utc": ts}


def ref(fair, fetched=NOW, commence=COMMENCE):
    return {"fetched_utc": fetched, "commence_utc": commence, "fair": {"A": fair}}


def test_yes_side_logged_when_fair_well_above_ask():
    rows, reason = evaluate(snap(), MARKET, {"ref_outcome": "A"}, ref(0.50), SETTINGS)
    # fee per contract at 0.42: 0.07*100*0.42*0.58 = 1.7052 -> 1.71 -> 0.0171
    assert reason is None and [r["side"] for r in rows] == ["YES"]
    assert rows[0]["edge"] == D("0.500000") - D("0.42") - D("0.0171")


def test_no_side_logged_when_fair_well_below():
    rows, _ = evaluate(snap(yes_ask="0.42", no_ask="0.59"), MARKET, {"ref_outcome": "A"}, ref(0.30),
                       SETTINGS)
    assert [r["side"] for r in rows] == ["NO"]   # 0.70 - 0.59 - 0.0171


def test_threshold_is_strict():
    # fair chosen so the YES edge is exactly 0.03 after the 0.0171 fee
    rows, _ = evaluate(snap(), MARKET, {"ref_outcome": "A"}, ref(0.4671), SETTINGS)
    assert rows == []
    rows, _ = evaluate(snap(), MARKET, {"ref_outcome": "A"}, ref(0.4672), SETTINGS)
    assert [r["side"] for r in rows] == ["YES"]


@pytest.mark.parametrize("s,r,reason", [
    (snap(status="closed"), ref(0.9), "not_active"),
    (snap(yes_ask="0"), ref(0.9), "no_two_sided_quote"),
    (snap(no_ask="1"), ref(0.9), "no_two_sided_quote"),
    (snap(), ref(0.9, fetched=NOW - timedelta(minutes=76)), "reference_stale"),
    (snap(ts=COMMENCE), ref(0.9, fetched=COMMENCE - timedelta(minutes=10)), "not_pre_game"),
])
def test_skip_conditions(s, r, reason):
    assert evaluate(s, MARKET, {"ref_outcome": "A"}, r, SETTINGS) == ([], reason)


def test_unknown_fee_type_skips():
    assert evaluate(snap(), {"fee_type": "flat", "fee_multiplier": 1}, {"ref_outcome": "A"},
                    ref(0.9), SETTINGS) == ([], "fee_unknown")


def seed(store, fair_a=0.60, fair_b=0.40, ref_outcomes=("Cleveland Cavaliers", "Philadelphia 76ers")):
    store.upsert("markets", [
        {"ticker": t, "event_ticker": "E1", "series_ticker": "S", "sport": "Basketball",
         "competition": "Pro Basketball (M)", "yes_sub_title": title, "status": "active",
         "fee_type": "quadratic", "fee_multiplier": 1, "first_seen_utc": NOW, "last_seen_utc": NOW}
        for t, title in (("E1-CLE", "Cleveland"), ("E1-PHI", "Philadelphia"))], "ticker")
    store.record_matches([
        {"market_ticker": "E1-CLE", "event_ticker": "E1", "source": "the-odds-api",
         "source_event_id": "R1", "ref_outcome": "Cleveland Cavaliers", "rule_version": "v"},
        {"market_ticker": "E1-PHI", "event_ticker": "E1", "source": "the-odds-api",
         "source_event_id": "R1", "ref_outcome": "Philadelphia 76ers", "rule_version": "v"}], NOW)
    store.append("reference_odds", [
        {"fetched_utc": NOW, "source": "the-odds-api", "bookmaker": "pinnacle", "sport_key": "nba",
         "source_event_id": "R1", "commence_utc": COMMENCE, "home": "Philadelphia 76ers",
         "away": "Cleveland Cavaliers", "outcome": o, "decimal_odds": D("2"), "fair_prob": p,
         "overround": 1.02, "devig_method": "proportional", "book_last_update_utc": NOW}
        for o, p in zip(ref_outcomes, (fair_a, fair_b))])


def test_run_shadow_writes_complete_rows():
    with Store.open() as s:
        seed(s)
        snaps = {"E1-CLE": snap(yes_ask="0.42", no_ask="0.59"), "E1-PHI": snap(yes_ask="0.58", no_ask="0.43")}
        res = run_shadow(s, "run-1", snaps, NOW, "the-odds-api", out=lambda m: None)
    rows = ledger.read_rows(config.shadow_ledger_path())
    assert res["shadow_rows"] == len(rows) == 2
    assert {(r["market_ticker"], r["side"]) for r in rows} == {("E1-CLE", "YES"), ("E1-PHI", "NO")}
    assert list(rows[0].keys()) == HEADER
    r = rows[0]
    assert r["rule_id"] == "GAP_v1" and r["snapshot_utc"].endswith("Z") and r["commence_utc"].endswith("Z")
    assert all(r[k] != "" for k in ("fair_prob", "edge", "price_paid", "fee_per_contract", "row_id",
                                    "source_event_id", "bookmaker"))


def test_outcome_set_mismatch_never_compared():
    with Store.open() as s:
        seed(s, ref_outcomes=("Cleveland Cavaliers", "Draw"))
        res = run_shadow(s, "run-1", {"E1-CLE": snap(), "E1-PHI": snap()}, NOW, "the-odds-api",
                         out=lambda m: None)
    assert res["shadow_rows"] == 0 and res["skip_outcome_count"] == 2


def test_conflicted_event_never_compared():
    with Store.open() as s:
        seed(s)
        s.record_matches([{"market_ticker": "E1-CLE", "event_ticker": "E1", "source": "the-odds-api",
                           "source_event_id": "R9", "ref_outcome": "X", "rule_version": "v"}], NOW)
        res = run_shadow(s, "run-1", {"E1-CLE": snap(), "E1-PHI": snap()}, NOW, "the-odds-api",
                         out=lambda m: None)
    assert res["shadow_rows"] == 0 and res["skip_match_conflict"] == 2


def test_rule_override_is_labelled(monkeypatch):
    monkeypatch.setenv("SHADOW_EDGE_THRESHOLD", "0.01")
    assert config.rule_id() == "GAP_v1+custom(SHADOW_EDGE_THRESHOLD=0.01)"
    monkeypatch.setenv("SHADOW_EDGE_THRESHOLD", "0.03")
    assert config.rule_id() == "GAP_v1"
