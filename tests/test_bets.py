from datetime import timedelta
from decimal import Decimal as D

import pytest

from sports_edge import bets, config, ledger
from sports_edge.bets import BET_LOG_HEADER, parse_reply
from sports_edge.shadow import HEADER
from sports_edge.timeutil import iso_z, parse_utc

NOW = parse_utc("2026-10-23T03:00:00Z")          # 8 PM PDT, Oct 22


@pytest.mark.parametrize("text,parsed", [
    ("took 42", ("took", None, D("0.42"))),
    ("Took 42c", ("took", None, D("0.42"))),
    ("took 42¢", ("took", None, D("0.42"))),
    ("took .42", ("took", None, D("0.42"))),
    ("took 0.42", ("took", None, D("0.42"))),
    ("took #3 42", ("took", 3, D("0.42"))),
    ("took 3 42", ("took", 3, D("0.42"))),
    ("took 3 at 42", ("took", 3, D("0.42"))),
    ("took 42.3", ("took", None, D("0.423"))),                 # a walked average, as the hint writes it
    ("took 12 64.3", ("took", 12, D("0.643"))),
    ("took 0.5", ("took", None, D("0.5"))), ("took 1", ("took", None, D("0.01"))),
    ("  SKIP ", ("skip", None, None)),
    ("skip 3", ("skip", 3, None)),
    ("skip #3", ("skip", 3, None)),
    ("took", None), ("took 0", None), ("took 100", None), ("took #3", None), ("skip 3 42", None),
    ("took 42 maybe", None), ("I took 42", None), ("Reply \"took 42\" or \"skip\"", None),
    ("#2 Cleveland over Philadelphia · Pay up to 45¢", None), ("", None), (None, None),
])
def test_reply_grammar(text, parsed):
    assert parse_reply(text) == parsed


def replies(*lines):
    """Write replies.txt as the Shortcut would: '<ISO time> <reply>' per line (or a bare reply)."""
    path = bets.replies_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((f"{when.astimezone().isoformat()} {text}" if when else text) + "\n"
                            for when, text in lines))
    return path


def pick_row(rid, ticker, side, logged, run="run-1", event="E1", price="0.42", fee="0.0171", edge="0.04"):
    row = {k: "" for k in HEADER}
    row.update({"row_id": rid, "market_ticker": ticker, "side": side, "rule_id": "GAP_v2", "run_id": run,
                "event_ticker": event, "snapshot_utc": iso_z(logged), "logged_utc": iso_z(logged),
                "price_paid": price, "fee_per_contract": fee, "edge": edge})
    return row


@pytest.fixture
def two_checks():
    t1, t2 = NOW - timedelta(hours=2), NOW - timedelta(hours=1)
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [
        pick_row("a", "E1-CLE", "YES", t1, "run-1"),
        pick_row("b", "E2-BOS", "YES", t2, "run-2", "E2"), pick_row("c", "E3-MIA", "NO", t2, "run-2", "E3"),
        pick_row("a2", "E1-CLE", "YES", t2, "run-2")])                       # repeat of #1, not a new pick
    return t1, t2


def logged():
    return {r["raw_text"] + "|" + r["message_utc"]: r for r in ledger.read_rows(config.bet_log_path())}


def by_text():
    return {r["raw_text"]: r for r in ledger.read_rows(config.bet_log_path())}


def test_pick_numbers_follow_first_logged_order_and_repeats_keep_their_number(two_checks):
    rows = ledger.read_rows(config.shadow_ledger_path())
    picks = bets.day_picks(rows, "2026-10-22")
    assert {k[0]: (n, r["row_id"]) for k, (n, r) in picks.items()} == {
        "E1-CLE": (1, "a"), "E2-BOS": (2, "b"), "E3-MIA": (3, "c")}


def test_replies_are_logged_once_against_the_named_pick(two_checks):
    t1, t2 = two_checks
    replies((t1 + timedelta(minutes=5), "took 42"),              # one pick in the latest check: #1
            (t2 + timedelta(minutes=5), "took 40"),              # two picks in the latest check: unclear
            (t2 + timedelta(minutes=6), "took #3 61"),
            (t2 + timedelta(minutes=7), "skip 2"),
            (t2 + timedelta(minutes=8), "Practice · 0 of 100 games"))   # not a reply: ignored
    status = bets.ingest_replies(NOW)
    assert (status["readable"], status["error"], status["logged"], status["unclear"]) == (True, None, 4, 1)
    assert [text for _, text in status["unclear_lines"]] == ["took 40"]
    got = by_text()
    assert (got["took 42"]["row_id"], got["took 42"]["price"], got["took 42"]["contracts"]) == ("a", "0.42", "23")
    assert got["took 40"]["action"] == "unclear" and got["took 40"]["row_id"] == ""
    assert (got["took #3 61"]["row_id"], got["took #3 61"]["pick_no"], got["took #3 61"]["contracts"]) == ("c", "3", "16")
    assert (got["skip 2"]["row_id"], got["skip 2"]["action"]) == ("b", "skip")
    assert got["took 42"]["message_utc"] == iso_z(t1 + timedelta(minutes=5))
    assert bets.ingest_replies(NOW)["logged"] == 0                 # never logged twice
    assert len(ledger.read_rows(config.bet_log_path())) == 4


def test_a_line_without_a_time_is_timed_when_first_read_and_twins_are_kept(two_checks):
    replies((None, "skip 1"), (None, "skip 1"))
    bets.ingest_replies(NOW)
    rows = ledger.read_rows(config.bet_log_path())
    assert len(rows) == 2 and {r["message_utc"] for r in rows} == {iso_z(NOW)}
    assert len({r["message_guid"] for r in rows}) == 2


def test_lines_before_yesterday_are_ignored(two_checks):
    t1, _ = two_checks
    replies((NOW - timedelta(days=3), "skip"), (t1 + timedelta(minutes=5), "took 42"))
    assert bets.ingest_replies(NOW)["logged"] == 1 and set(by_text()) == {"took 42"}


def test_missing_replies_file_is_reported_not_hidden():
    status = bets.ingest_replies(NOW)
    assert status["readable"] is False and "no replies.txt yet" in status["error"]
    assert not config.bet_log_path().exists()


def test_a_file_still_in_icloud_says_so():
    folder = config.sheet_dir()
    folder.mkdir(parents=True)
    (folder / ".replies.txt.icloud").write_bytes(b"placeholder")
    assert "not downloaded to this Mac yet" in bets.ingest_replies(NOW)["error"]


def test_unnumbered_reply_follows_the_latest_check_even_when_it_repeats_an_earlier_pick():
    """Review finding 4: run C repeats only #1, so "took 42" is #1, not the newer #2."""
    ta, tb, tc = NOW - timedelta(minutes=90), NOW - timedelta(minutes=60), NOW - timedelta(minutes=30)
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [
        pick_row("a", "E1-CLE", "YES", ta, "run-A"), pick_row("b", "E2-BOS", "YES", tb, "run-B", "E2"),
        pick_row("a3", "E1-CLE", "YES", tc, "run-C")])
    replies((tc + timedelta(minutes=2), "took 42"))
    bets.ingest_replies(NOW)
    row = by_text()["took 42"]
    assert (row["row_id"], row["pick_no"], row["action"]) == ("a", "1", "took")


def test_numbered_reply_after_midnight_finds_the_evening_pick():
    """Review finding 8."""
    evening = parse_utc("2026-10-23T06:50:00Z")                       # 11:50 PM PDT Oct 22
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [pick_row("z", "E9-LAL", "NO", evening, "run-Z", "E9")])
    after_midnight = evening + timedelta(minutes=15)
    replies((after_midnight, "took 1 42"))
    bets.ingest_replies(after_midnight + timedelta(minutes=1))
    row = by_text()["took 1 42"]
    assert (row["row_id"], row["pick_date"], row["pick_no"]) == ("z", "2026-10-22", "1")


def test_two_ways_to_back_one_team_are_one_pick_and_a_reply_takes_the_cheaper():
    """Owner ruling 5: Yes on Cleveland and No on Philadelphia are one result, shown (and taken) once."""
    t = NOW - timedelta(minutes=30)
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [
        pick_row("yes", "E1-CLE", "YES", t, "run-1", price="0.42", fee="0.0171"),
        pick_row("no", "E1-PHI", "NO", t, "run-1", price="0.40", fee="0.0168"),
        pick_row("x", "E2-BOS", "YES", t + timedelta(minutes=1), "run-2", "E2")])
    same = {("E1-CLE", "YES"): "CLE", ("E1-PHI", "NO"): "CLE"}
    outcome_of = lambda r: same.get((r["market_ticker"], r["side"]), r["market_ticker"])
    picks = bets.day_picks(ledger.read_rows(config.shadow_ledger_path()), "2026-10-22", outcome_of)
    assert sorted((n, r["row_id"]) for n, r in picks.values()) == [(1, "no"), (2, "x")]
    replies((t + timedelta(minutes=5), "took 1 40"))
    bets.ingest_replies(NOW, outcome_of=outcome_of)
    assert by_text()["took 1 40"]["row_id"] == "no"


def test_the_exact_line_shortcuts_writes_is_read(two_checks):
    t1, _ = two_checks
    path = bets.replies_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("2026-10-22T18:05:12-07:00 took 42\n\nnot a reply\n")   # 01:05Z, after the run-1 pick
    assert bets.ingest_replies(NOW)["logged"] == 1
    row = by_text()["took 42"]
    assert row["message_utc"] == "2026-10-23T01:05:12.000Z" and row["row_id"] == "a"
