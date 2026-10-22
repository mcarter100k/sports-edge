import sqlite3
from datetime import timedelta
from decimal import Decimal as D

import pytest

from sports_edge import bets, config, ledger
from sports_edge.bets import APPLE_EPOCH, BET_LOG_HEADER, decode_attributed_body, parse_reply
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


def test_attributed_body_text_is_recovered():
    for text in ("took 42", "skip #3", "x" * 200):
        raw = text.encode()
        length = bytes([len(raw)]) if len(raw) < 0x80 else b"\x81" + len(raw).to_bytes(2, "little")
        blob = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08" \
               b"NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + raw + b"\x86\x84"
        assert decode_attributed_body(blob) == text
    assert decode_attributed_body(None) is None and decode_attributed_body(b"nothing here") is None


def chat_db(path, handle, messages):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB,
                              date INTEGER, is_from_me INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);""")
    con.execute("INSERT INTO chat VALUES (1, ?)", [handle])
    con.execute("INSERT INTO chat VALUES (2, 'someone-else@example.com')")
    for i, (when, text, chat) in enumerate(messages, 1):
        ns = int((when - APPLE_EPOCH).total_seconds() * 1e9)
        con.execute("INSERT INTO message VALUES (?, ?, ?, NULL, ?, 1)", [i, f"G{i}", text, ns])
        con.execute("INSERT INTO chat_message_join VALUES (?, ?)", [chat, i])
    con.commit()
    con.close()


def pick_row(rid, ticker, side, logged, run="run-1", event="E1"):
    row = {k: "" for k in HEADER}
    row.update({"row_id": rid, "market_ticker": ticker, "side": side, "rule_id": "GAP_v2", "run_id": run,
                "event_ticker": event, "snapshot_utc": iso_z(logged), "logged_utc": iso_z(logged)})
    return row


@pytest.fixture
def two_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("IMESSAGE_TO", "+1 (555) 123-4567")
    db = tmp_path / "chat.db"
    monkeypatch.setenv("SPORTS_EDGE_MESSAGES_DB", str(db))
    t1, t2 = NOW - timedelta(hours=2), NOW - timedelta(hours=1)
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [
        pick_row("a", "E1-CLE", "YES", t1, "run-1"),
        pick_row("b", "E2-BOS", "YES", t2, "run-2", "E2"), pick_row("c", "E3-MIA", "NO", t2, "run-2", "E3"),
        pick_row("a2", "E1-CLE", "YES", t2, "run-2")])                       # repeat of #1, not a new pick
    return db, t1, t2


def logged():
    return {r["message_guid"]: r for r in ledger.read_rows(config.bet_log_path())}


def test_pick_numbers_follow_first_logged_order_and_repeats_keep_their_number(two_checks):
    rows = ledger.read_rows(config.shadow_ledger_path())
    picks = bets.day_picks(rows, "2026-10-22")
    assert {k[0]: (n, r["row_id"]) for k, (n, r) in picks.items()} == {
        "E1-CLE": (1, "a"), "E2-BOS": (2, "b"), "E3-MIA": (3, "c")}


def test_replies_are_logged_once_against_the_named_pick(two_checks):
    db, t1, t2 = two_checks
    chat_db(db, "+15551234567", [
        (t1 + timedelta(minutes=5), "took 42", 1),               # one pick in the latest check: #1
        (t2 + timedelta(minutes=5), "took 40", 1),               # two picks in the latest check: unclear
        (t2 + timedelta(minutes=6), "took #3 61", 1),
        (t2 + timedelta(minutes=7), "skip 2", 1),
        (t2 + timedelta(minutes=8), "Practice · 0 of 100 games", 1),   # the system's own text: ignored
        (t2 + timedelta(minutes=9), "took 50", 2)])              # another chat: ignored
    status = bets.ingest_replies(NOW)
    assert status == {"readable": True, "error": None, "logged": 4, "unclear": 1}
    got = logged()
    assert (got["G1"]["row_id"], got["G1"]["action"], got["G1"]["price"], got["G1"]["contracts"]) == \
        ("a", "took", "0.42", "23")
    assert got["G2"]["action"] == "unclear" and got["G2"]["row_id"] == ""
    assert (got["G3"]["row_id"], got["G3"]["pick_no"], got["G3"]["contracts"]) == ("c", "3", "16")
    assert (got["G4"]["row_id"], got["G4"]["action"]) == ("b", "skip")
    assert bets.ingest_replies(NOW)["logged"] == 0                 # never logged twice
    assert len(ledger.read_rows(config.bet_log_path())) == 4


def test_unreadable_messages_database_is_reported_not_hidden(monkeypatch, tmp_path):
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    status = bets.ingest_replies(NOW)                 # conftest points at a file that does not exist
    assert status["readable"] is False and "no_chat.db" in status["error"]
    assert not config.bet_log_path().exists()


def test_without_a_number_nothing_is_read():
    assert bets.ingest_replies(NOW) == {"readable": False, "error": "IMESSAGE_TO not set", "logged": 0,
                                        "unclear": 0}


def test_unnumbered_reply_follows_the_latest_check_even_when_it_repeats_an_earlier_pick(tmp_path, monkeypatch):
    """Review finding 4: run C repeats only #1, so "took 42" is #1, not the newer #2."""
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    db = tmp_path / "chat.db"
    monkeypatch.setenv("SPORTS_EDGE_MESSAGES_DB", str(db))
    ta, tb, tc = NOW - timedelta(minutes=90), NOW - timedelta(minutes=60), NOW - timedelta(minutes=30)
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [
        pick_row("a", "E1-CLE", "YES", ta, "run-A"), pick_row("b", "E2-BOS", "YES", tb, "run-B", "E2"),
        pick_row("a3", "E1-CLE", "YES", tc, "run-C")])
    chat_db(db, "+15551234567", [(tc + timedelta(minutes=2), "took 42", 1)])
    bets.ingest_replies(NOW)
    row = logged()["G1"]
    assert (row["row_id"], row["pick_no"], row["action"]) == ("a", "1", "took")


def test_numbered_reply_after_midnight_finds_the_evening_pick(tmp_path, monkeypatch):
    """Review finding 8."""
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    db = tmp_path / "chat.db"
    monkeypatch.setenv("SPORTS_EDGE_MESSAGES_DB", str(db))
    evening = parse_utc("2026-10-23T06:50:00Z")                       # 11:50 PM PDT Oct 22
    ledger.append_rows(config.shadow_ledger_path(), HEADER, [pick_row("z", "E9-LAL", "NO", evening, "run-Z", "E9")])
    after_midnight = evening + timedelta(minutes=15)
    chat_db(db, "+15551234567", [(after_midnight, "took 1 42", 1)])
    bets.ingest_replies(after_midnight + timedelta(minutes=1))
    row = logged()["G1"]
    assert (row["row_id"], row["pick_date"], row["pick_no"]) == ("z", "2026-10-22", "1")


def test_old_messages_in_the_chat_are_never_read(two_checks):
    """Review finding 6: nanosecond dates must be compared as nanoseconds."""
    db, t1, _ = two_checks
    chat_db(db, "+15551234567", [(NOW - timedelta(days=3), "skip", 1), (t1 + timedelta(minutes=5), "took 42", 1)])
    assert bets.ingest_replies(NOW)["logged"] == 1 and set(logged()) == {"G2"}
