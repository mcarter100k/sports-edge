from datetime import timedelta
from decimal import Decimal as D

import pytest

from helpers import FakeKalshi, FakeOdds, fee_table, game_event, ref_sport, series, stub
from sports_edge import bets, config, grader, health, ledger, shadow, slate
from sports_edge.bets import BET_LOG_HEADER
from sports_edge.shadow import HEADER
from sports_edge.snapshot import run_cycle
from sports_edge.store import Store
from sports_edge.timeutil import iso_z, parse_utc

DAILY_AT = parse_utc("2026-10-22T10:00:00Z")      # 03:00 PDT
START = DAILY_AT + timedelta(hours=5)              # 08:00 PDT
CHECK_AT = START - timedelta(minutes=25)
JARGON = ("clv", "brier", "de-vig", "devig", "overround", " ev ", "edge", "lock", "value")
PRICED = {"R1": {"Philadelphia 76ers": D("1.55"), "Cleveland Cavaliers": D("2.60")}}


def quiet(_m):
    pass


def world(cheap=True):
    ev = game_event("KXNBAGAME-E1", "KXNBAGAME", ["Cleveland", "Philadelphia"], START)
    cle, phi = ev["markets"]          # fair: Cleveland 37.3%, Philadelphia 62.7%
    phi.update({"yes_bid_dollars": "0.6200", "yes_ask_dollars": "0.6300", "no_bid_dollars": "0.3700",
                "no_ask_dollars": "0.3800"})
    cle.update({"yes_bid_dollars": "0.3700", "yes_ask_dollars": "0.3800", "no_bid_dollars": "0.6200",
                "no_ask_dollars": "0.6300"})
    if cheap:   # Cleveland Yes at 20c against a fair 37.3c; nothing else has a gap
        cle.update({"yes_ask_dollars": "0.2000", "yes_bid_dollars": "0.1900"})
    return [series("KXNBAGAME")], [[ev]]


def run_at(now, sers, pages, priced=PRICED):
    odds = lambda st: FakeOdds(st, [ref_sport()], {"basketball_nba": [
        stub("R1", "Philadelphia 76ers", "Cleveland Cavaliers", START)]}, priced, now=now)
    return run_cycle(FakeKalshi(sers, pages, now), odds, lambda: now, quiet, quiet)


@pytest.fixture
def checked(tmp_path):
    """A daily run that matches R1, then the check run 25 minutes before the start."""
    fee_table(tmp_path)
    sers, pages = world()
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages)
    for job in ("snapshot", "grader"):
        health.record_success(job, CHECK_AT)
    return sers, pages


def alert(now):
    sent = []
    slate.run_alert(now, sent.append, quiet)
    return sent


def no_jargon(text):
    low = text.lower().replace("sports-edge/", "")
    return [w for w in JARGON if w in low]


# Formatting and wording -------------------------------------------------------------------

def test_cents():
    assert [slate.cents(D(x)) for x in ("0.42", "0.4235", "0.4", "0.05")] == ["42¢", "42.4¢", "40¢", "5¢"]


def test_pay_up_to_is_the_highest_cent_that_still_leaves_three_cents():
    # fair 0.50: at 45c, 22 contracts, fee 0.3812/22 -> gap 0.0327; at 46c, 21 contracts -> gap 0.0226
    assert shadow.pay_up_to(D("0.5"), D("1"), D("0.0001"), D("10"), D("0.03")) == 45
    assert shadow.pay_up_to(D("0.02"), D("1"), D("0.0001"), D("10"), D("0.03")) is None


TEAMS2 = {"titles": ["Cleveland", "Philadelphia"], "tie": False}
TEAMS3 = {"titles": ["Arsenal", "Chelsea"], "tie": True}


@pytest.mark.parametrize("outcome,side,teams,who", [
    ("Cleveland", "YES", TEAMS2, "Cleveland over Philadelphia (Yes on Cleveland)"),
    ("Cleveland", "NO", TEAMS2, "Philadelphia over Cleveland (No on Cleveland)"),
    ("Arsenal", "NO", TEAMS3, "Chelsea or a draw vs Arsenal (No on Arsenal)"),
    ("Tie", "YES", TEAMS3, "Draw: Arsenal vs Chelsea (Yes on Tie)"),
    ("Tie", "NO", TEAMS3, "No draw: Arsenal vs Chelsea (No on Tie)"),
])
def test_pick_wording_by_side(outcome, side, teams, who):
    assert slate.wording({"outcome": outcome, "side": side}, teams)[0] == who


@pytest.mark.parametrize("move,side,label", [
    ("", "YES", "no earlier Kalshi price"), ("0.019", "YES", "Kalshi price steady"),
    ("-0.019", "NO", "Kalshi price steady"), ("0.02", "YES", "Kalshi price rising"),
    ("-0.02", "YES", "Kalshi price falling"),
    ("-0.03", "NO", "Kalshi price rising"),                    # Yes price falling = No price rising
])
def test_one_read_of_the_sharp_book_only_describes_the_kalshi_price(move, side, label):
    assert slate.line_label({"kalshi_move_1h": move, "side": side, "fair_prob": "0.5"}, "Cleveland") == label
    assert "news" not in label and "skip" not in label


@pytest.mark.parametrize("fair,side,earlier,label", [
    ("0.50", "YES", "0.481", "line steady"), ("0.50", "YES", "0.48", "line moved toward Cleveland, check news"),
    ("0.50", "YES", "0.52", "line moved against, skip"), ("0.40", "NO", "0.58", "line moved toward Cleveland, check news"),
])
def test_two_reads_describe_the_sportsbook_line(fair, side, earlier, label):
    row = {"kalshi_move_1h": "0.10", "side": side, "fair_prob": fair}
    assert slate.line_label(row, "Cleveland", D(earlier)) == label


# Stage line -------------------------------------------------------------------------------

def grades(n_games, clv="0.02", pnl="0.05", price="0.45", fee="0.017"):
    return [{"row_id": f"r{i}", "market_ticker": f"M{i}", "side": "YES", "rule_id": "GAP_v2", "event_ticker": f"E{i}",
             "snapshot_utc": f"2026-10-{1 + i // 100:02d}T00:00:{i % 60:02d}.{i:03d}Z", "clv": clv if i % 2 else
             str(D(clv) * 2), "pnl_per_contract": pnl if i % 3 else str(-D(pnl)), "price_paid": price,
             "fee_per_contract": fee} for i in range(n_games)]


def test_stage_practice_until_a_hundred_games():
    assert grader.stage([]) == ("Practice", "Practice · 0 of 100 games with a closing price")
    assert grader.stage(grades(99))[1] == "Practice · 99 of 100 games with a closing price"


def test_stage_early_signal_supported_and_confirmed():
    assert grader.stage(grades(100)) == ("Early signal", "Early signal · 100 games with a closing price · Supported needs 500")
    assert grader.stage(grades(500))[0] == "Supported"
    assert grader.stage(grades(500, pnl="-0.05"))[0] == "Early signal"      # beating the close, no profit
    assert grader.stage(grades(1000)) == ("Confirmed", "Confirmed · 1000 games with a closing price")


def test_games_without_a_closing_price_never_promote():
    """Owner review: single-pull games (blank clv) do not count toward beating the final price."""
    rows = grades(150)
    for r in rows:
        r["clv"] = ""                                        # every game read once
    assert grader.stage(rows)[1] == "Practice · 0 of 100 games with a closing price"
    for r in rows[:2]:
        r["clv"] = "0.05"
    assert grader.stage(rows)[1] == "Practice · 2 of 100 games with a closing price"
    assert grader.stage(rows, closes_possible=False)[1] == \
        "Practice · 2 of 100 games with a closing price (free plan adds none)"
    for r in rows[:2]:
        r["clv"] = "-0.05"                                   # 2 losing closes among 150 graded games
    assert grader.stage(rows)[1] == "Practice · 2 of 100 games with a closing price"   # not demoted


def test_stage_demotes_when_not_beating_the_final_price():
    assert grader.stage(grades(150, clv="-0.01"))[1] == \
        "Practice (demoted: not beating the final price) · 150 games"


def test_stage_demotes_on_a_twenty_bet_losing_stretch():
    rows = grades(30, pnl="-0.462", price="0.45", fee="0.012")                # each loss = 1 stake
    for r in rows:
        r["pnl_per_contract"] = "-0.462"
    assert grader.stage(rows)[1] == "Practice (demoted: 20-bet losing stretch) · 30 games"
    assert grader.stage(rows[:20])[1] == "Practice (demoted: 20-bet losing stretch) · 20 games"   # exactly 20
    assert grader.stage(rows[:19])[1] == "Practice · 19 of 100 games with a closing price"


def test_long_names_still_fit_in_one_message():
    row = {"outcome": "Wolverhampton Wanderers", "side": "NO", "fair_prob": "0.31", "fee_multiplier": "1",
           "fee_precision": "0.0001", "stake_dollars": "10", "threshold": "0.03", "price_paid": "0.6534",
           "fill_detail": "0.65x10;0.66x5", "snapshot_utc": iso_z(CHECK_AT), "commence_utc": iso_z(START),
           "kalshi_move_1h": "-0.035"}
    teams = {"titles": ["Wolverhampton Wanderers", "Brighton and Hove Albion"], "tie": True}
    # the longest stage line that can occur (69 characters), plus the unreadable bet log line
    footer = "Practice · 99 of 100 games with a closing price (free plan adds none)\nBet log unreadable: cap not tracked"
    full = slate.pick_text(row, 12, True, teams, "Conference League", CHECK_AT, several=True)
    short = slate.pick_text(row, 12, True, teams, "Conference League", CHECK_AT, several=True, compact=True)
    assert len(f"{full}\n{footer}") > slate.MESSAGE_MAX >= len(f"{short}\n{footer}")
    assert 'Reply "took 12 65.3" or "skip 12"' in short


# Alerts ------------------------------------------------------------------------------------

def test_check_sends_each_pick_once_with_the_stage_line(checked):
    sent = alert(CHECK_AT + timedelta(minutes=2))
    assert len(sent) == 1
    msg = sent[0]
    assert msg.startswith("#1 Cleveland over Philadelphia (Yes on Cleveland) · NBA · 8:00 AM PT · Pay up to ")
    assert "now 20¢ · fair 37" in msg and "no earlier Kalshi price · price 2 min old" in msg
    assert '\nReply "took 20" or "skip"\n' in msg
    assert msg.endswith("Practice · 0 of 100 games with a closing price (free plan adds none)\nBet log unreadable: cap not tracked")
    assert len(msg) <= slate.MESSAGE_MAX and no_jargon(msg) == []
    assert alert(CHECK_AT + timedelta(minutes=4)) == []                       # never sent twice
    sheet = (config.sheet_dir() / "sheet.txt").read_text()
    assert "#1 Cleveland over Philadelphia" in sheet and no_jargon(sheet) == []


def test_two_ways_to_back_one_team_show_once_the_cheaper(tmp_path):
    """Owner ruling 5: display only; both rows stay in the ledger."""
    fee_table(tmp_path)
    sers, pages = world()
    pages[0][0]["markets"][1].update({"no_ask_dollars": "0.1900", "no_bid_dollars": "0.1800"})
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages)
    health.record_success("snapshot", CHECK_AT)
    sent = alert(CHECK_AT + timedelta(minutes=1))
    assert [m.split(" · ")[0] for m in sent] == ["#1 Cleveland over Philadelphia (No on Philadelphia)"]
    assert "now 19¢" in sent[0] and '"took 19" or "skip"' in sent[0]
    assert sorted((r["market_ticker"], r["side"]) for r in ledger.read_rows(config.shadow_ledger_path())) == [
        ("KXNBAGAME-E1-CLE", "YES"), ("KXNBAGAME-E1-PHI", "NO")]
    sheet = (config.sheet_dir() / "sheet.txt").read_text()
    assert sheet.count("Cleveland over Philadelphia") == 1


def test_picks_are_exactly_the_check_runs_ledger_rows(checked):
    sent = alert(CHECK_AT + timedelta(minutes=2))
    rows = ledger.read_rows(config.shadow_ledger_path())
    assert len(rows) == len([m for m in sent if m.startswith("#")]) == 1


def test_late_message_never_shows_old_picks(checked):
    sent = alert(CHECK_AT + timedelta(minutes=20))
    assert sent == ["Don't bet now: prices not checked (prices are 20 min old). 8:00 AM PT · NBA 1\n"
                    "Practice · 0 of 100 games with a closing price (free plan adds none)\nBet log unreadable: cap not tracked"]


def test_nothing_is_sent_once_the_slot_has_started(checked):
    assert alert(START + timedelta(minutes=1)) == []


def test_failing_system_says_dont_bet(checked):
    health.record_failure("snapshot", "boom", CHECK_AT + timedelta(minutes=1))
    sent = alert(CHECK_AT + timedelta(minutes=2))
    assert sent[0].startswith("Don't bet now: prices not checked (system problem). 8:00 AM PT · NBA 1")


def test_missed_check_on_a_failing_system_says_dont_bet(tmp_path):
    fee_table(tmp_path)
    sers, pages = world()
    run_at(DAILY_AT, sers, pages)
    health.record_failure("snapshot", "boom", CHECK_AT)
    sent = alert(CHECK_AT + timedelta(minutes=2))
    assert len(sent) == 1 and sent[0].startswith("Don't bet now: prices not checked (system problem).")
    assert alert(CHECK_AT + timedelta(minutes=4)) == []


def test_checked_without_gaps_says_so(tmp_path):
    fee_table(tmp_path)
    sers, pages = world(cheap=False)
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages)
    health.record_success("snapshot", CHECK_AT)
    assert alert(CHECK_AT + timedelta(minutes=1))[0].startswith(
        "8:00 AM PT · NBA 1: checked, no price gaps · prices 1 min old")


def test_no_fee_table_is_said_once_a_day():
    sers, pages = world()
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages)
    health.record_success("snapshot", CHECK_AT)
    sent = alert(CHECK_AT + timedelta(minutes=1))
    assert [m.split("\n")[0] for m in sent] == ["Don't bet today: prices not checked (no fee table)."]
    assert alert(CHECK_AT + timedelta(minutes=3)) == []


def test_out_of_credits_is_said_once(tmp_path, monkeypatch):
    fee_table(tmp_path)
    monkeypatch.setenv("ODDS_DAILY_CREDITS", "0")
    sers, pages = world()
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages)
    health.record_success("snapshot", CHECK_AT)
    sent = alert(CHECK_AT + timedelta(minutes=1))
    assert [m.split("\n")[0] for m in sent] == [
        "Out of odds credits today: 1 later time slots not checked — don't bet them."]


def taken(i, logged, result=None):
    """A taken bet today on market OLD-i, settled per `result` ('yes' wins, 'no' loses, None open)."""
    row = {k: "" for k in HEADER}
    row.update({"row_id": f"L{i}", "market_ticker": f"OLD-{i}", "event_ticker": f"OLD{i}", "side": "YES",
                "rule_id": "GAP_v2", "run_id": f"old-{i}", "snapshot_utc": iso_z(logged), "logged_utc": iso_z(logged),
                "fee_multiplier": "1", "fee_precision": "0.0001", "commence_utc": iso_z(logged + timedelta(minutes=30)),
                "price_paid": "0.50", "fee_per_contract": "0.0175", "edge": "0.03"})
    bet = {"logged_utc": iso_z(logged), "message_guid": f"g{i}", "message_utc": iso_z(logged),
           "pick_date": "2026-10-22", "pick_no": i + 1, "row_id": f"L{i}", "market_ticker": f"OLD-{i}",
           "event_ticker": f"OLD{i}", "side": "YES", "action": "took", "price": "0.50", "contracts": "20",
           "raw_text": "took 50"}
    return row, bet, result


@pytest.mark.parametrize("results,capped", [
    ((None, None, None), True),          # three open bets: $30 at risk
    ((None, None), False),               # $20
    ((None, None, "yes"), False),        # a win counts 0
    ((None, None, "no"), True),          # a loss counts its $10
])
def test_daily_cap_is_money_at_risk(checked, results, capped):
    bets.replies_path().parent.mkdir(parents=True, exist_ok=True)
    bets.replies_path().write_text("")                                      # readable, no new replies
    earlier = CHECK_AT - timedelta(hours=3)
    made = [taken(i, earlier, r) for i, r in enumerate(results)]
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [m[0] for m in made])
    ledger.append_rows(config.bet_log_path(), BET_LOG_HEADER, [m[1] for m in made])
    with Store.open() as s:
        s.record_settlements([{"ticker": f"OLD-{i}", "result": r, "settlement_value": D("0"),
                               "settlement_ts": CHECK_AT, "source": "scan"}
                              for i, (_, _, r) in enumerate(made) if r], CHECK_AT)
    sent = alert(CHECK_AT + timedelta(minutes=2))
    if capped:
        assert sent == ["Daily cap reached — no more bets today. (1 price gaps logged for the record.)\n"
                        "Practice · 0 of 100 games with a closing price (free plan adds none)"]
    else:
        assert sent[0].startswith("#")
    sheet = (config.sheet_dir() / "sheet.txt").read_text()
    assert "money at risk today $" in sheet


# Morning, show, wake ------------------------------------------------------------------------

def test_morning_message(checked):
    alert(CHECK_AT + timedelta(minutes=2))
    next_morning = parse_utc("2026-10-23T15:00:00Z")          # 8:00 AM PDT
    sent = []
    text = slate.run_morning(next_morning, sent.append, quiet)
    assert sent == [text] and len(text) <= slate.MORNING_MAX and no_jargon(text) == []
    lines = text.split("\n")
    assert lines[0] == "Kalshi slate · Fri Oct 23" and lines[1] == "Practice · 0 of 100 games with a closing price (free plan adds none)"
    assert lines[2] == "Today: no matched games (odds not set up)"
    assert lines[3] == "Yesterday: 1 checks, 1 price gaps in 1 games · results: 0 won, 0 lost, 1 open"
    assert lines[4] == "Your bets: bet log unreadable"
    assert lines[5] == "Unchecked yesterday: none (all 1 games checked)"
    assert lines[6].startswith("System: ") and "bet log unreadable" in lines[6]
    assert not list(config.logs_dir().glob("digest_*.txt"))           # cut A: no digest report
    assert "BOARD" in (config.sheet_dir() / "sheet.txt").read_text()


def test_unchecked_games_are_counted_by_reason(tmp_path, monkeypatch):
    fee_table(tmp_path)
    monkeypatch.setenv("ODDS_DAILY_CREDITS", "0")
    sers, pages = world()
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages)
    sent = []
    text = slate.run_morning(parse_utc("2026-10-23T15:00:00Z"), sent.append, quiet)
    assert "Unchecked yesterday: 1 games in 1 time slots (out of credits 1)" in text


def test_show_writes_nothing(checked):
    out = []
    slate.run_show(CHECK_AT + timedelta(minutes=2), out.append)
    assert out[0].startswith("Slate sheet · built ") and any("#1 Cleveland" in ln for ln in out)
    assert not (config.sheet_dir() / "sheet.txt").exists() and not (config.state_dir() / "slate_sent.json").exists()


def test_an_alert_that_cannot_fit_fails_loudly_and_is_not_marked_sent(checked, monkeypatch):
    monkeypatch.setattr(slate, "MESSAGE_MAX", 120)
    with pytest.raises(ValueError, match="over 120"):
        alert(CHECK_AT + timedelta(minutes=2))
    monkeypatch.setattr(slate, "MESSAGE_MAX", 300)
    assert len(alert(CHECK_AT + timedelta(minutes=3))) == 1          # retried on the next run


def test_morning_names_why_nothing_could_be_checked():
    text = slate.run_morning(parse_utc("2026-10-23T15:00:00Z"), lambda t: None, quiet)
    assert "Yesterday: nothing checked (odds not set up)" in text
    assert "Unchecked yesterday: no matched games yesterday (odds not set up)" in text
    assert "0 checks" not in text


def test_morning_names_a_missing_fee_table(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    text = slate.run_morning(parse_utc("2026-10-23T15:00:00Z"), lambda t: None, quiet)
    assert "Yesterday: nothing checked (no fee table)" in text


def test_shortened_names_that_would_collide_are_shown_in_full():
    """Review finding 5."""
    teams = {"titles": ["Los Angeles L", "Los Angeles C"], "tie": False}
    who, backed = slate.wording({"outcome": "Los Angeles L", "side": "YES"}, teams, limit=slate.NAME_MIN)
    assert who == "Los Angeles L over Los Angeles C (Yes on Los Angeles L)" and backed == "Los Angeles L"
    who, _ = slate.wording({"outcome": "Manchester City", "side": "NO"},
                           {"titles": ["Manchester City", "Manchester United"], "tie": True}, limit=slate.NAME_MIN)
    assert who == "Manchester United or a draw vs Manchester City (No on Manchester City)"


def test_a_checked_game_that_could_not_be_compared_is_not_called_gap_free(tmp_path):
    """Review finding 7: an outcome mismatch is fetched (checked) but never compared."""
    fee_table(tmp_path)
    sers, pages = world()
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages, priced={"R1": {"Philadelphia 76ers": D("1.6"), "Cleveland Cavaliers": D("3.0"),
                                                 "Draw": D("15")}})
    health.record_success("snapshot", CHECK_AT)
    sent = alert(CHECK_AT + timedelta(minutes=1))
    assert [m.split("\n")[0] for m in sent] == [
        "Don't bet now: prices not checked (could not compare). 8:00 AM PT · NBA 1"]


def test_a_failed_send_partway_never_resends_what_already_went_out(tmp_path):
    """Review, lower priority: per-text send state."""
    fee_table(tmp_path)
    sers, pages = world()
    ev2 = game_event("KXNBAGAME-E8", "KXNBAGAME", ["Boston", "Miami"], START)
    ev2["markets"][0].update({"yes_ask_dollars": "0.2000", "yes_bid_dollars": "0.1900"})
    ev2["markets"][1].update({"yes_ask_dollars": "0.6300", "no_ask_dollars": "0.3800"})
    pages[0].append(ev2)
    two = {**PRICED, "R8": {"Miami Heat": D("1.55"), "Boston Celtics": D("2.60")}}
    for at in (DAILY_AT, CHECK_AT):
        odds = lambda st, at=at: FakeOdds(st, [ref_sport()], {"basketball_nba": [
            stub("R1", "Philadelphia 76ers", "Cleveland Cavaliers", START),
            stub("R8", "Miami Heat", "Boston Celtics", START)]}, two, now=at)
        run_cycle(FakeKalshi(sers, pages, at), odds, lambda at=at: at, quiet, quiet)
    health.record_success("snapshot", CHECK_AT)
    got = []

    def flaky(text):
        if len(got) == 1:
            raise slate.imessage.SendFailed("Messages not running")
        got.append(text)

    with pytest.raises(slate.imessage.SendFailed):
        slate.run_alert(CHECK_AT + timedelta(minutes=1), flaky, quiet)
    rest = alert(CHECK_AT + timedelta(minutes=2))
    assert [m[:2] for m in got] == ["#1"] and [m[:5] for m in rest] == ["#2 Bo", "Combo"]   # combo line unparked


def test_hint_asks_for_the_number_when_one_run_checks_two_slots(tmp_path, monkeypatch):
    """Review finding 4: each message has one pick, but the run wrote two, so replies need the number."""
    fee_table(tmp_path)
    monkeypatch.setenv("ODDS_PLAN", "paid")
    sers, pages = world()
    later = START + timedelta(minutes=35)
    ev2 = game_event("KXNBAGAME-E7", "KXNBAGAME", ["Boston", "Miami"], later)
    ev2["markets"][0].update({"yes_ask_dollars": "0.2000", "yes_bid_dollars": "0.1900"})
    ev2["markets"][1].update({"yes_bid_dollars": "0.6200", "yes_ask_dollars": "0.6300", "no_bid_dollars": "0.3700",
                              "no_ask_dollars": "0.3800"})
    pages[0].append(ev2)
    priced = {**PRICED, "R7": {"Miami Heat": D("1.55"), "Boston Celtics": D("2.60")}}

    def run(now):
        odds = lambda st: FakeOdds(st, [ref_sport()], {"basketball_nba": [
            stub("R1", "Philadelphia 76ers", "Cleveland Cavaliers", START),
            stub("R7", "Miami Heat", "Boston Celtics", later)]}, priced, now=now)
        run_cycle(FakeKalshi(sers, pages, now), odds, lambda: now, quiet, quiet)

    run(DAILY_AT)
    both = START - timedelta(minutes=10)        # slot 1's 15-minute check and slot 2's 60-minute check
    run(both)
    health.record_success("snapshot", both)
    sent = alert(both + timedelta(minutes=1))
    picks = [m for m in sent if m.startswith("#")]
    assert len(picks) == 2
    assert '"took 1 20" or "skip 1"' in picks[0] and '"took 2 20" or "skip 2"' in picks[1]


def test_a_reply_that_names_no_pick_asks_for_the_number_once(checked):
    """Spec: an unclear reply makes the next message ask for the pick number."""
    path = bets.replies_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("2026-10-22T07:30:00-07:00 took 40\n")      # before any pick existed: unclear
    sent = alert(CHECK_AT + timedelta(minutes=2))
    assert sent[0].startswith('Reply "took 40" not matched to a pick: send it again with the pick number')
    assert len(sent[0]) <= slate.MESSAGE_MAX
    assert not any(m.startswith("Reply") for m in alert(CHECK_AT + timedelta(minutes=4)))
