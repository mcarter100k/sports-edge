import ast
import fcntl
from decimal import Decimal as D
from pathlib import Path

import pytest

from sports_edge import config, ledger
from sports_edge.ledger import LedgerSchemaMismatch, append_rows, read_rows
from sports_edge.store import Store, StoreLocked
from sports_edge.timeutil import parse_utc

PKG = Path(config.__file__).parent
T0 = parse_utc("2026-10-21T20:00:00Z")
T1 = parse_utc("2026-10-21T21:00:00Z")


def test_header_written_once_and_rows_appended(tmp_path):
    p = tmp_path / "l.csv"
    append_rows(p, ["a", "b"], [{"a": 1, "b": 2}])
    append_rows(p, ["a", "b"], [{"a": 3}])
    assert p.read_text() == "a,b\n1,2\n3,\n"


def test_header_mismatch_writes_nothing(tmp_path):
    p = tmp_path / "l.csv"
    append_rows(p, ["a", "b"], [{"a": 1, "b": 2}])
    before = p.read_bytes()
    with pytest.raises(LedgerSchemaMismatch):
        append_rows(p, ["a", "c"], [{"a": 9, "c": 9}])
    assert p.read_bytes() == before


def test_unknown_field_rejected(tmp_path):
    with pytest.raises(LedgerSchemaMismatch):
        append_rows(tmp_path / "l.csv", ["a"], [{"a": 1, "zzz": 2}])
    assert not (tmp_path / "l.csv").exists() or read_rows(tmp_path / "l.csv") == []


def write_mode_opens(source):
    """Every open()/fdopen() call whose mode string contains 'w', found by parsing."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if name not in ("open", "fdopen"):
            continue
        modes = [a for a in node.args[1:2]] + [k.value for k in node.keywords if k.arg == "mode"]
        for m in modes:
            if isinstance(m, ast.Constant) and isinstance(m.value, str) and "w" in m.value:
                hits.append(ast.unparse(node))
    return hits


def test_write_mode_detector_sees_nested_calls():
    # positive control: the shape that a regex once missed
    assert write_mode_opens('open(config.shadow_ledger_path(), "w").close()')
    assert write_mode_opens('os.fdopen(fd, mode="w")')
    assert write_mode_opens('open(p, "a+")') == []


def test_no_module_opens_a_ledger_for_overwrite():
    # Only health.py (its own state file, atomic replace) and store.py (temp CSV
    # staging) may open anything for writing.
    allowed = {"health.py", "store.py"}
    for path in PKG.glob("*.py"):
        if path.name not in allowed:
            assert write_mode_opens(path.read_text()) == [], path.name


def test_settlement_insert_once_conflict_recorded():
    with Store.open() as s:
        row = {"ticker": "T", "result": "yes", "settlement_value": D("1"), "settlement_ts": T0,
               "source": "scan"}
        assert s.record_settlements([row], T0) == (1, 0)
        assert s.record_settlements([row], T1) == (0, 0)
        assert s.record_settlements([{**row, "result": "no", "settlement_value": D("0")}], T1) == (0, 1)
        assert s.con.execute("SELECT result FROM settlements").fetchall() == [("yes",)]
        assert s.con.execute("SELECT count(*) FROM settlement_conflicts").fetchone()[0] == 1


def test_match_insert_once_conflict_recorded():
    m = {"market_ticker": "M", "event_ticker": "E", "source": "src", "source_event_id": "R1",
         "ref_outcome": "A", "rule_version": "v"}
    with Store.open() as s:
        assert s.record_matches([m], T0) == (1, 0)
        assert s.record_matches([m], T1) == (0, 0)
        assert s.record_matches([{**m, "source_event_id": "R2"}], T1) == (0, 1)
        assert s.conflicted_events() == {"E"}


def test_upsert_keeps_first_seen():
    base = {"series_ticker": "S", "title": "t", "sport": "Basketball", "tags": "[]",
            "fee_type": "quadratic", "fee_multiplier": 1}
    with Store.open() as s:
        s.upsert("series", [{**base, "first_seen_utc": T0, "last_seen_utc": T0}], "series_ticker")
        s.upsert("series", [{**base, "title": "new", "first_seen_utc": T1, "last_seen_utc": T1}],
                 "series_ticker")
        first, last, title = s.con.execute(
            "SELECT first_seen_utc, last_seen_utc, title FROM series").fetchone()
        assert (first.hour, last.hour, title) == (20, 21, "new")


def test_values_round_trip_with_commas_and_quotes():
    with Store.open() as s:
        s.append("unmatched", [{"run_id": "r", "ts_utc": T0, "event_ticker": "E",
                                "sport": "Soccer", "reason": "x", "detail": 'a, "b", c'}])
        assert s.con.execute("SELECT detail FROM unmatched").fetchone()[0] == 'a, "b", c'


def test_second_writer_waits_then_fails_loudly():
    lock = config.data_dir() / ".db.lock"
    with open(lock, "a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        with pytest.raises(StoreLocked):
            with Store.open(lock_wait_s=1):
                pass
