"""The combo line (docs/specs/slate_alerts_spec.md, amendment 2026-10-23)."""
import json
from decimal import Decimal as D

import pytest

from helpers import fee_table
from sports_edge import fees, slate


def leg(event, fair, side="YES"):
    return {"event_ticker": event, "fair_prob": fair, "side": side}


def table_with_combo(tmp_path, entry=True, unconfirmed=None):
    path = fee_table(tmp_path, unconfirmed=unconfirmed)
    t = json.loads(path.read_text())
    if entry:
        t["combo"] = {"taker": "1", "cite": "p.2 Combos"}
    path.write_text(json.dumps(t))
    return fees.load_table()


def test_two_different_games_are_needed():
    assert slate.combo_line([(1, leg("E1", "0.6")), (2, leg("E1", "0.3", "NO"))], None) is None
    assert slate.combo_line([(1, leg("E1", "0.6"))], None) is None


def test_fee_unconfirmed_wording(tmp_path):
    assert slate.combo_line([(1, leg("E1", "0.6")), (3, leg("E2", "0.5"))], None) == \
        "Combo #1+#3: fee unconfirmed — no price."
    t = table_with_combo(tmp_path, entry=False)
    assert slate.combo_line([(1, leg("E1", "0.6")), (3, leg("E2", "0.5"))], t).endswith("fee unconfirmed — no price.")


def test_price_and_payout_pinned(tmp_path):
    t = table_with_combo(tmp_path)
    # fair 0.6 x 0.5 = 0.30. At 28c: 35 contracts, fee 0.07*35*0.28*0.72 = 0.49392 -> 0.0141 each, 0.2941 <= 0.30;
    # at 29c the cost passes 0.30. Pays 1 / 0.294112 = 3.40 -> 3.4x.
    line = slate.combo_line([(1, leg("E1", "0.6")), (3, leg("E2", "0.5"))], t, target=D("4"))
    assert line == "Combo #1+#3: accept a quote at 28¢ or less (pays 3.4× or more), else let it expire. " \
                   "Your 4× target: below."


def test_target_met_at_the_boundary(tmp_path):
    t = table_with_combo(tmp_path)
    picks = [(1, leg("E1", "0.6")), (3, leg("E2", "0.5"))]
    assert slate.combo_line(picks, t, target=D("3.4")).endswith("Your 3.4× target: met.")
    assert slate.combo_line(picks, t, target=D("3.5")).endswith("Your 3.5× target: below.")


def test_four_legs_reach_the_default_target(tmp_path, monkeypatch):
    monkeypatch.delenv("COMBO_TARGET_PAYOUT", raising=False)
    t = table_with_combo(tmp_path)
    picks = [(1, leg("E1", "0.6")), (2, leg("E2", "0.55")), (3, leg("E3", "0.7")), (4, leg("E4", "0.65"))]
    line = slate.combo_line(picks, t)
    assert line.startswith("Combo #1+#2+#3+#4: accept a quote at 14¢ or less") and line.endswith("Your 4× target: met.")


def test_target_comes_from_the_environment(tmp_path, monkeypatch):
    t = table_with_combo(tmp_path)
    monkeypatch.setenv("COMBO_TARGET_PAYOUT", "6")
    assert slate.combo_line([(1, leg("E1", "0.6")), (3, leg("E2", "0.5"))], t).endswith("Your 6× target: below.")


def test_no_price_works(tmp_path):
    t = table_with_combo(tmp_path)
    assert slate.combo_line([(1, leg("E1", "0.01")), (2, leg("E2", "0.5"))], t) == \
        "Combo #1+#2: no price works — let any quote expire."


def test_only_the_picks_are_legs_never_padded(tmp_path):
    t = table_with_combo(tmp_path)
    line = slate.combo_line([(2, leg("E1", "0.6")), (5, leg("E2", "0.5")), (7, leg("E2", "0.4", "NO"))], t)
    assert line.startswith("Combo #2+#5:")          # one leg per game, numbers only from the picks given


def test_combo_fee_fails_closed_until_the_table_confirms_it(tmp_path):
    with pytest.raises(fees.FeeScheduleUnknown, match="no fee table"):
        fees.combo_taker_multiplier(None)
    with pytest.raises(fees.FeeScheduleUnknown, match="no confirmed entry"):
        fees.combo_taker_multiplier(table_with_combo(tmp_path, entry=False))
    with pytest.raises(fees.FeeScheduleUnknown, match="unconfirmed"):
        fees.combo_taker_multiplier(table_with_combo(tmp_path, unconfirmed={"combo": "not checked"}))
    assert fees.combo_taker_multiplier(table_with_combo(tmp_path)) == D("1")


def test_combo_entry_needs_a_cite(tmp_path):
    path = fee_table(tmp_path)
    t = json.loads(path.read_text())
    t["combo"] = {"taker": "1"}
    path.write_text(json.dumps(t))
    with pytest.raises(fees.FeeTableInvalid, match="combo"):
        fees.load_table()


# On real runs: the check message and the board ------------------------------------------------

from datetime import timedelta  # noqa: E402

from helpers import FakeKalshi, FakeOdds, game_event, ref_sport, stub  # noqa: E402
from sports_edge import config, health  # noqa: E402
from sports_edge.snapshot import run_cycle  # noqa: E402
from sports_edge.store import Store  # noqa: E402
from test_slate import CHECK_AT, DAILY_AT, PRICED, START, alert, quiet, world  # noqa: E402


def two_game_run(tmp_path, combo=True, second_game=True):
    """Two NBA games with a gap each at the 30-minute check (the 'failed send partway' world)."""
    path = fee_table(tmp_path)
    if combo:
        t = json.loads(path.read_text())
        t["combo"] = {"taker": "1", "cite": "p.2 Combos"}
        path.write_text(json.dumps(t))
    sers, pages = world()
    stubs = [stub("R1", "Philadelphia 76ers", "Cleveland Cavaliers", START)]
    priced = dict(PRICED)
    if second_game:
        ev2 = game_event("KXNBAGAME-E8", "KXNBAGAME", ["Boston", "Miami"], START)
        ev2["markets"][0].update({"yes_ask_dollars": "0.2000", "yes_bid_dollars": "0.1900"})
        ev2["markets"][1].update({"yes_ask_dollars": "0.6300", "no_ask_dollars": "0.3800"})
        pages[0].append(ev2)
        stubs.append(stub("R8", "Miami Heat", "Boston Celtics", START))
        priced["R8"] = {"Miami Heat": D("1.55"), "Boston Celtics": D("2.60")}
    for at in (DAILY_AT, CHECK_AT):
        odds = lambda st, at=at: FakeOdds(st, [ref_sport()], {"basketball_nba": stubs}, priced, now=at)
        run_cycle(FakeKalshi(sers, pages, at), odds, lambda at=at: at, quiet, quiet)
    health.record_success("snapshot", CHECK_AT)


def board_at(now, cap=False):
    with Store.open() as s:
        ctx = slate.Context(s, now, ingest=False)
        ctx.cap_reached = cap
        return slate.board_lines(ctx, now)


def test_the_check_message_carries_the_combo(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    two_game_run(tmp_path)
    sent = alert(CHECK_AT + timedelta(minutes=1))
    combos = [m for m in sent if m.startswith("Combo #1+#2:")]
    assert len(combos) == 1 and "accept a quote at" in combos[0] and "target:" in combos[0]


def test_one_game_gives_no_combo(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    two_game_run(tmp_path, second_game=False)
    assert not [m for m in alert(CHECK_AT + timedelta(minutes=1)) if m.startswith("Combo")]


def test_the_board_shows_the_combo_for_two_current_games_but_not_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    two_game_run(tmp_path)
    lines = board_at(CHECK_AT + timedelta(minutes=2))
    assert [ln for ln in lines if ln.startswith("  Combo #")]
    assert not [ln for ln in board_at(CHECK_AT + timedelta(minutes=2), cap=True) if "Combo" in ln]


def test_no_combo_line_is_sent_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    two_game_run(tmp_path)
    real = slate.Context.__init__

    def capped(self, *a, **kw):
        real(self, *a, **kw)
        self.cap_reached = True

    monkeypatch.setattr(slate.Context, "__init__", capped)
    sent = alert(CHECK_AT + timedelta(minutes=1))
    assert sent and sent[0].startswith("Daily cap reached") and not [m for m in sent if "Combo" in m]
