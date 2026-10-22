"""DuckDB storage (docs/specs/storage_spec.md).

TIMESTAMP columns hold UTC. Evidence tables are only ever appended to;
settlements and odds_matches are insert-once with a conflicts table.
"""
import csv
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import duckdb

from . import config

LOCK_WAIT_S = 600

# (column, DuckDB type) per table, in insert order.
TABLES = {
    "series": [
        ("series_ticker", "VARCHAR PRIMARY KEY"), ("title", "VARCHAR"), ("sport", "VARCHAR"),
        ("tags", "VARCHAR"), ("fee_type", "VARCHAR"), ("fee_multiplier", "DECIMAL(10,6)"),
        ("first_seen_utc", "TIMESTAMP"), ("last_seen_utc", "TIMESTAMP"),
    ],
    "markets": [
        ("ticker", "VARCHAR PRIMARY KEY"), ("event_ticker", "VARCHAR"), ("series_ticker", "VARCHAR"),
        ("sport", "VARCHAR"), ("competition", "VARCHAR"), ("competition_scope", "VARCHAR"),
        ("event_title", "VARCHAR"), ("yes_sub_title", "VARCHAR"), ("no_sub_title", "VARCHAR"),
        ("market_type", "VARCHAR"), ("status", "VARCHAR"), ("mutually_exclusive", "BOOLEAN"),
        ("open_time", "TIMESTAMP"), ("close_time", "TIMESTAMP"),
        ("expected_expiration_time", "TIMESTAMP"), ("occurrence_time", "TIMESTAMP"),
        ("resolve_by", "TIMESTAMP"), ("fee_type", "VARCHAR"), ("fee_multiplier", "DECIMAL(10,6)"),
        ("fee_waiver_expiration_time", "TIMESTAMP"), ("fee_override", "BOOLEAN"),
        ("first_seen_utc", "TIMESTAMP"), ("last_seen_utc", "TIMESTAMP"),
    ],
    "price_snapshots": [
        ("run_id", "VARCHAR"), ("ts_utc", "TIMESTAMP"), ("tier", "VARCHAR"), ("ticker", "VARCHAR"),
        ("status", "VARCHAR"), ("yes_bid", "DECIMAL(10,6)"), ("yes_ask", "DECIMAL(10,6)"),
        ("no_bid", "DECIMAL(10,6)"), ("no_ask", "DECIMAL(10,6)"), ("last_price", "DECIMAL(10,6)"),
        ("yes_bid_size", "DECIMAL(18,2)"), ("yes_ask_size", "DECIMAL(18,2)"),
        ("volume", "DECIMAL(18,2)"), ("volume_24h", "DECIMAL(18,2)"), ("open_interest", "DECIMAL(18,2)"),
    ],
    "settlements": [
        ("ticker", "VARCHAR PRIMARY KEY"), ("result", "VARCHAR"), ("settlement_value", "DECIMAL(10,6)"),
        ("settlement_ts", "TIMESTAMP"), ("recorded_utc", "TIMESTAMP"), ("source", "VARCHAR"),
    ],
    "settlement_conflicts": [
        ("ticker", "VARCHAR"), ("seen_utc", "TIMESTAMP"), ("result", "VARCHAR"),
        ("settlement_value", "DECIMAL(10,6)"), ("settlement_ts", "TIMESTAMP"), ("source", "VARCHAR"),
    ],
    "reference_odds": [
        ("fetched_utc", "TIMESTAMP"), ("source", "VARCHAR"), ("bookmaker", "VARCHAR"),
        ("sport_key", "VARCHAR"), ("source_event_id", "VARCHAR"), ("commence_utc", "TIMESTAMP"),
        ("home", "VARCHAR"), ("away", "VARCHAR"), ("outcome", "VARCHAR"),
        ("decimal_odds", "DECIMAL(12,6)"), ("fair_prob", "DOUBLE"), ("fair_prob_power", "DOUBLE"),
        ("overround", "DOUBLE"),
        ("devig_method", "VARCHAR"), ("book_last_update_utc", "TIMESTAMP"),
    ],
    "odds_matches": [
        ("market_ticker", "VARCHAR"), ("event_ticker", "VARCHAR"), ("source", "VARCHAR"),
        ("source_event_id", "VARCHAR"), ("ref_outcome", "VARCHAR"), ("rule_version", "VARCHAR"),
        ("matched_utc", "TIMESTAMP"), ("sport_key", "VARCHAR"),
    ],
    "match_conflicts": [
        ("market_ticker", "VARCHAR"), ("event_ticker", "VARCHAR"), ("source", "VARCHAR"),
        ("source_event_id", "VARCHAR"), ("ref_outcome", "VARCHAR"), ("seen_utc", "TIMESTAMP"),
    ],
    "unmatched": [
        ("run_id", "VARCHAR"), ("ts_utc", "TIMESTAMP"), ("event_ticker", "VARCHAR"),
        ("sport", "VARCHAR"), ("reason", "VARCHAR"), ("detail", "VARCHAR"),
    ],
    "odds_calls": [
        ("ts_utc", "TIMESTAMP"), ("sport_key", "VARCHAR"), ("endpoint", "VARCHAR"),
        ("http_status", "INTEGER"), ("credits_last", "INTEGER"), ("credits_used", "INTEGER"),
        ("credits_remaining", "INTEGER"),
    ],
    "reference_events": [
        ("fetched_utc", "TIMESTAMP"), ("source", "VARCHAR"), ("sport_key", "VARCHAR"),
        ("source_event_id", "VARCHAR"), ("commence_utc", "TIMESTAMP"), ("home", "VARCHAR"),
        ("away", "VARCHAR"),
    ],
    "check_log": [
        ("run_id", "VARCHAR"), ("ts_utc", "TIMESTAMP"), ("source", "VARCHAR"),
        ("source_event_id", "VARCHAR"), ("sport_key", "VARCHAR"), ("cluster_start_utc", "TIMESTAMP"),
        ("offset_min", "INTEGER"), ("status", "VARCHAR"),
    ],
    "job_runs": [
        ("run_id", "VARCHAR"), ("job", "VARCHAR"), ("phase", "VARCHAR"), ("ts_utc", "TIMESTAMP"),
        ("status", "VARCHAR"), ("summary", "VARCHAR"),
    ],
}


class StoreLocked(Exception):
    pass


def to_db_ts(dt):
    """Aware datetime -> naive UTC for a TIMESTAMP column."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime: {dt!r}")
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def from_db_ts(value):
    """TIMESTAMP value (naive UTC) -> aware UTC datetime."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc)


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return to_db_ts(value).isoformat(sep=" ")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@contextmanager
def _file_lock(path, wait_s):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fh.close()
                raise StoreLocked(f"could not lock {path} within {wait_s}s")
            time.sleep(1)
    try:
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


class Store:
    def __init__(self, con):
        self.con = con

    @classmethod
    @contextmanager
    def open(cls, path=None, lock_wait_s=LOCK_WAIT_S):
        path = path or config.db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(path.parent / ".db.lock", lock_wait_s):
            con = duckdb.connect(str(path))
            try:
                store = cls(con)
                store.create_schema()
                yield store
            finally:
                con.close()

    def create_schema(self):
        for table, cols in TABLES.items():
            body = ", ".join(f"{name} {typ}" for name, typ in cols)
            self.con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({body})")

    @contextmanager
    def transaction(self):
        self.con.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        self.con.execute("COMMIT")

    # Bulk paths ------------------------------------------------------------

    def _staged(self, table, rows):
        """Write rows (dicts) to a temp CSV; return (path, select-sql)."""
        cols = TABLES[table]
        names = [c for c, _ in cols]
        fd, tmp = tempfile.mkstemp(suffix=".csv", dir=str(config.data_dir()))
        with os.fdopen(fd, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(names)
            for row in rows:
                w.writerow([_csv_value(row.get(n)) for n in names])
        types = {n: t.replace(" PRIMARY KEY", "") for n, t in cols}
        spec = "{" + ", ".join(f"'{n}': '{t}'" for n, t in types.items()) + "}"
        sql = (f"SELECT * FROM read_csv(?, header=true, columns={spec}, nullstr='', "
               f"quote='\"', escape='\"', auto_detect=false)")
        return tmp, sql

    def append(self, table, rows):
        rows = list(rows)
        if not rows:
            return 0
        tmp, sql = self._staged(table, rows)
        try:
            self.con.execute(f"INSERT INTO {table} {sql}", [tmp])
        finally:
            os.unlink(tmp)
        return len(rows)

    def upsert(self, table, rows, key, keep_on_update=("first_seen_utc",)):
        rows = list(rows)
        if not rows:
            return 0
        names = [c for c, _ in TABLES[table]]
        updates = ", ".join(f"{n} = excluded.{n}" for n in names
                            if n != key and n not in keep_on_update)
        tmp, sql = self._staged(table, rows)
        try:
            self.con.execute(f"INSERT INTO {table} {sql} ON CONFLICT ({key}) DO UPDATE SET {updates}",
                             [tmp])
        finally:
            os.unlink(tmp)
        return len(rows)

    # Settlements: insert once, conflicts recorded --------------------------

    def record_settlements(self, rows, now):
        """rows: dicts with ticker, result, settlement_value, settlement_ts, source.

        Returns (added, conflicts). An existing settlement is never overwritten.
        """
        if not rows:
            return 0, 0
        tickers = [r["ticker"] for r in rows]
        existing = {t: (res, val) for t, res, val in self.con.execute(
            "SELECT ticker, result, settlement_value FROM settlements WHERE ticker IN "
            "(SELECT unnest(?))", [tickers]).fetchall()}
        new, conflicts, seen = [], [], set()
        for r in rows:
            t = r["ticker"]
            if t in seen:
                continue
            seen.add(t)
            if t not in existing:
                new.append({**r, "recorded_utc": now})
                continue
            res, val = existing[t]
            val_new = r.get("settlement_value")
            if res != r["result"] or (val is not None and val_new is not None and val != val_new):
                conflicts.append({**r, "seen_utc": now})
        self.append("settlements", new)
        self.append("settlement_conflicts", conflicts)
        return len(new), len(conflicts)

    # Matches: insert once, conflicts recorded ------------------------------

    def record_matches(self, rows, now):
        """rows: dicts with market_ticker, event_ticker, source, source_event_id, ref_outcome,
        rule_version. Returns (added, conflicts)."""
        if not rows:
            return 0, 0
        existing = {(m, s): (e, o) for m, s, e, o in self.con.execute(
            "SELECT market_ticker, source, source_event_id, ref_outcome FROM odds_matches").fetchall()}
        new, conflicts = [], []
        for r in rows:
            key = (r["market_ticker"], r["source"])
            if key not in existing:
                new.append({**r, "matched_utc": now})
                existing[key] = (r["source_event_id"], r["ref_outcome"])
            elif existing[key] != (r["source_event_id"], r["ref_outcome"]):
                conflicts.append({**r, "seen_utc": now})
        self.append("odds_matches", new)
        self.append("match_conflicts", conflicts)
        return len(new), len(conflicts)

    def conflicted_events(self):
        return {e for (e,) in self.con.execute("SELECT DISTINCT event_ticker FROM match_conflicts").fetchall()}

    # Job runs ---------------------------------------------------------------

    def job_event(self, run_id, job, phase, now, status, summary=None):
        self.append("job_runs", [{"run_id": run_id, "job": job, "phase": phase, "ts_utc": now,
                                  "status": status,
                                  "summary": json.dumps(summary or {}, sort_keys=True, default=str)}])

    def last_success(self, job, tier=None):
        rows = self.con.execute(
            "SELECT ts_utc, summary FROM job_runs WHERE job = ? AND phase = 'finish' AND status = 'ok' "
            "ORDER BY ts_utc DESC LIMIT 200", [job]).fetchall()
        for ts, summary in rows:
            if tier is None or json.loads(summary).get("tier") == tier:
                return from_db_ts(ts)
        return None

    def last_finish(self, job):
        """(status, time) of the job's latest finished run, or (None, None)."""
        row = self.con.execute(
            "SELECT status, ts_utc FROM job_runs WHERE job = ? AND phase = 'finish' "
            "ORDER BY ts_utc DESC LIMIT 1", [job]).fetchone()
        return (row[0], from_db_ts(row[1])) if row else (None, None)

    def run_finish(self, run_id):
        """(status, time, summary) of one run's finish row, or (None, None, {})."""
        row = self.con.execute(
            "SELECT status, ts_utc, summary FROM job_runs WHERE run_id = ? AND phase = 'finish'",
            [run_id]).fetchone()
        return (row[0], from_db_ts(row[1]), json.loads(row[2] or "{}")) if row else (None, None, {})

    def newest_snapshot(self):
        (ts,) = self.con.execute("SELECT max(ts_utc) FROM price_snapshots").fetchone()
        return from_db_ts(ts)

    # Odds budget -----------------------------------------------------------

    def credits_used_since(self, since):
        (used,) = self.con.execute(
            "SELECT coalesce(sum(credits_last), 0) FROM odds_calls WHERE ts_utc >= ?",
            [to_db_ts(since)]).fetchone()
        return int(used)

    def last_odds_fetch(self):
        (ts,) = self.con.execute("SELECT max(fetched_utc) FROM reference_odds").fetchone()
        return from_db_ts(ts)
