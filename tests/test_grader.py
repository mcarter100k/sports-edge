import math
from datetime import timedelta
from decimal import Decimal as D

import pytest

from helpers import FakeKalshi
from sports_edge import config, ledger
from sports_edge.grader import brier, log_loss, run_grader
from sports_edge.shadow import HEADER
from sports_edge.store import Store
from sports_edge.timeutil import iso_z, parse_utc

NOW = parse_utc("2026-10-23T10:30:00Z")
COMMENCE = parse_utc("2026-10-22T23:00:00Z")
PRE = COMMENCE - timedelta(minutes=30)
SRC = "the-odds-api"


def kalshi():
    return FakeKalshi([], [], NOW)


def seed(s, result_cle="yes", with_ref=True, quote=("0.40", "0.42")):
    s.upsert("markets", [{"ticker": t, "event_ticker": "E1", "sport": "Basketball", "status": "finalized",
                          "first_seen_utc": PRE, "last_seen_utc": PRE}
                         for t in ("E1-CLE", "E1-PHI")], "ticker")
    s.record_matches([{"market_ticker": t, "event_ticker": "E1", "source": SRC, "source_event_id": "R1",
                       "ref_outcome": o, "rule_version": "v"}
                      for t, o in (("E1-CLE", "CLE"), ("E1-PHI", "PHI"))], PRE)
    s.append("price_snapshots", [
        {"run_id": "r", "ts_utc": PRE, "tier": "near", "ticker": "E1-CLE", "status": "active",
         "yes_bid": D(quote[0]), "yes_ask": D(quote[1])},
        {"run_id": "r", "ts_utc": COMMENCE + timedelta(minutes=5), "tier": "near", "ticker": "E1-CLE",
         "status": "active", "yes_bid": D("0.90"), "yes_ask": D("0.92")},   # in-play: must be ignored
        {"run_id": "r", "ts_utc": PRE, "tier": "near", "ticker": "E1-PHI", "status": "active",
         "yes_bid": D("0.58"), "yes_ask": D("0.60")}])
    if with_ref:
        s.append("reference_odds", [
            {"fetched_utc": PRE, "source": SRC, "bookmaker": "pinnacle", "sport_key": "nba",
             "source_event_id": "R1", "commence_utc": COMMENCE, "outcome": o, "fair_prob": p,
             "fair_prob_power": pp, "devig_method": "proportional"}
            for o, p, pp in (("CLE", 0.45, 0.44), ("PHI", 0.55, 0.56))])
    s.record_settlements([
        {"ticker": "E1-CLE", "result": result_cle, "settlement_value": D("1"), "settlement_ts": NOW, "source": "scan"},
        {"ticker": "E1-PHI", "result": "no" if result_cle == "yes" else "yes", "settlement_value": D("0"),
         "settlement_ts": NOW, "source": "scan"}], NOW)


def test_scores():
    assert brier(0.41, 1) == pytest.approx(0.3481)
    assert log_loss(0.41, 1) == pytest.approx(-math.log(0.41))
    assert log_loss(0.0, 1) == pytest.approx(-math.log(1e-6))   # clipped, finite


def test_calibration_uses_last_pre_game_quote_and_reference():
    with Store.open() as s:
        seed(s)
    out = []
    summary = run_grader(kalshi(), now_fn=lambda: NOW, out=out.append)
    rows = {r["market_ticker"]: r for r in ledger.read_rows(config.calibration_grades_path())}
    assert summary["calibration_graded"] == 2
    cle = rows["E1-CLE"]
    assert float(cle["p_kalshi"]) == pytest.approx(0.41)          # mid of 0.40/0.42, not in-play 0.91
    assert float(cle["p_ref"]) == pytest.approx(0.45)
    assert float(cle["brier_kalshi"]) == pytest.approx((0.41 - 1) ** 2)
    assert float(cle["brier_ref"]) == pytest.approx((0.45 - 1) ** 2)
    report = "\n".join(out)
    assert "independent events 1 (INSUFFICIENT: under 20)" in report


def test_idempotent():
    with Store.open() as s:
        seed(s)
    run_grader(kalshi(), now_fn=lambda: NOW, out=lambda m: None)
    assert run_grader(kalshi(), now_fn=lambda: NOW, out=lambda m: None)["calibration_graded"] == 0
    assert len(ledger.read_rows(config.calibration_grades_path())) == 2


def test_missing_reference_is_skipped_not_half_graded():
    with Store.open() as s:
        seed(s, with_ref=False)
    summary = run_grader(kalshi(), now_fn=lambda: NOW, out=lambda m: None)
    assert summary["calibration_graded"] == 0 and summary["cal_skip_no_reference"] == 2


def test_zero_case_says_why():
    out = []
    run_grader(kalshi(), now_fn=lambda: NOW, out=out.append)
    assert any("0 graded (no matched market has settled yet): matched markets=0" in m for m in out)


def shadow_row(rid, ticker, side, price, fee, snap_ts, event="E1", ref_outcome="CLE", fair="0.50"):
    base = {k: "" for k in HEADER}
    base.update({"row_id": rid, "market_ticker": ticker, "event_ticker": event, "side": side,
                 "price_paid": price, "fee_per_contract": fee, "rule_id": "GAP_v2",
                 "snapshot_utc": iso_z(snap_ts), "logged_utc": iso_z(snap_ts),
                 "ref_outcome": ref_outcome, "commence_utc": iso_z(COMMENCE), "odds_source": SRC,
                 "source_event_id": "R1", "fair_prob": fair})
    return base


def test_shadow_pnl_and_first_fire():
    with Store.open() as s:
        seed(s, result_cle="yes")
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [
        shadow_row("a", "E1-CLE", "YES", "0.42", "0.0171", PRE - timedelta(minutes=15)),
        shadow_row("b", "E1-CLE", "YES", "0.40", "0.0168", PRE),           # repeat: not first-fire
        shadow_row("c", "E1-PHI", "NO", "0.43", "0.0172", PRE, ref_outcome="PHI", fair="0.40"),
        shadow_row("d", "E9-XXX", "YES", "0.50", "0.0175", PRE, "E9"),       # not settled: ungraded
    ])
    out = []
    summary = run_grader(kalshi(), now_fn=lambda: NOW, out=out.append)
    graded = {r["row_id"]: r for r in ledger.read_rows(config.shadow_grades_path())}
    assert summary["shadow_graded"] == 3 and "d" not in graded
    assert D(graded["a"]["pnl_per_contract"]) == D("1") - D("0.42") - D("0.0171")
    assert D(graded["c"]["pnl_per_contract"]) == D("1") - D("0.43") - D("0.0172")   # PHI settled no
    report = "\n".join(out)
    assert "first-fire 2" in report and "independent events 1 (INSUFFICIENT" in report


def test_shadow_loss():
    with Store.open() as s:
        seed(s, result_cle="no")
    ledger.append_rows(config.shadow_ledger_path(), HEADER,
                       [shadow_row("a", "E1-CLE", "YES", "0.42", "0.0171", PRE)])
    run_grader(kalshi(), now_fn=lambda: NOW, out=lambda m: None)
    (row,) = ledger.read_rows(config.shadow_grades_path())
    assert D(row["pnl_per_contract"]) == -D("0.42") - D("0.0171")


def test_power_fair_is_scored_alongside_proportional():
    with Store.open() as s:
        seed(s)
    out = []
    run_grader(kalshi(), now_fn=lambda: NOW, out=out.append)
    cle = {r["market_ticker"]: r for r in ledger.read_rows(config.calibration_grades_path())}["E1-CLE"]
    assert float(cle["p_ref_power"]) == pytest.approx(0.44)
    assert float(cle["brier_ref_power"]) == pytest.approx((0.44 - 1) ** 2)
    assert float(cle["logloss_ref_power"]) == pytest.approx(-math.log(0.44))
    assert any("power vs proportional on the same 2 markets" in m for m in out)


def test_clv_per_row_from_last_reference_before_start():
    with Store.open() as s:
        seed(s, result_cle="yes")
        # a later in-play fetch must be ignored for the closing line
        s.append("reference_odds", [{"fetched_utc": COMMENCE + timedelta(minutes=10), "source": SRC,
                                     "source_event_id": "R1", "commence_utc": COMMENCE, "outcome": "CLE",
                                     "fair_prob": 0.99, "fair_prob_power": 0.99}])
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [
        shadow_row("a", "E1-CLE", "YES", "0.42", "0.0171", PRE, fair="0.50"),
        shadow_row("c", "E1-PHI", "NO", "0.43", "0.0172", PRE, ref_outcome="PHI", fair="0.60")])
    out = []
    run_grader(kalshi(), now_fn=lambda: NOW, out=out.append)
    g = {r["row_id"]: r for r in ledger.read_rows(config.shadow_grades_path())}
    assert D(g["a"]["closing_fair_prob"]) == D("0.45") and D(g["a"]["closing_side_fair"]) == D("0.45")
    assert D(g["a"]["clv"]) == D("0.45") - D("0.42") and D(g["a"]["fair_move"]) == D("0.45") - D("0.50")
    # NO on PHI: closing PHI fair 0.55 -> side fair 0.45; entry side fair 1 - 0.60 = 0.40
    assert D(g["c"]["closing_side_fair"]) == D("0.45")
    assert D(g["c"]["clv"]) == D("0.45") - D("0.43") and D(g["c"]["fair_move"]) == D("0.45") - D("0.40")
    assert any("first-fire CLV" in m and "record only" in m for m in out)


def test_clv_blank_without_pre_start_reference():
    with Store.open() as s:
        seed(s, with_ref=False)
    ledger.append_rows(config.shadow_ledger_path(), HEADER,
                       [shadow_row("a", "E1-CLE", "YES", "0.42", "0.0171", PRE)])
    run_grader(kalshi(), now_fn=lambda: NOW, out=lambda m: None)
    (row,) = ledger.read_rows(config.shadow_grades_path())
    assert row["clv"] == "" and row["pnl_per_contract"] != ""
