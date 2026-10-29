"""Scoring harness (docs/specs/harness_spec.md). PROTECTED.

A probability source is scored at decision time against the outcome, the
Kalshi mid and the Pinnacle fair, per independent unit, with cluster-bootstrap
intervals. Scored predictions are made on a separate connection holding only
what was known at t (no outcomes); check_no_peeking() refuses any source that
predicts differently with the full data. Fitting sees only training dates.
"""
import math
import os
import random
import shutil
import tempfile
from datetime import date, timedelta, timezone

import duckdb

from sports_edge.timeutil import pacific_day_start

from . import stats

STANDARD = {
    "quotes": ("ticker VARCHAR", "ts_utc TIMESTAMP", "yes_bid DOUBLE", "yes_ask DOUBLE"),
    "reference": ("ticker VARCHAR", "fetched_utc TIMESTAMP", "fair_prob DOUBLE"),
    "outcomes": ("ticker VARCHAR", "y INTEGER"),
    "units": ("ticker VARCHAR", "unit_id VARCHAR", "cluster VARCHAR"),
}
TIMED = {"quotes": "ts_utc", "reference": "fetched_utc"}
TOL = 1e-12
P_CLIP = 1e-6


class LeakageError(Exception):
    pass


class HarnessError(Exception):
    pass


def _naive(t):
    """Aware UTC datetime -> naive UTC (TIMESTAMP columns hold UTC)."""
    if t.tzinfo is None:
        raise HarnessError(f"naive datetime {t!r}: decision times must be timezone-aware UTC")
    return t.astimezone(timezone.utc).replace(tzinfo=None)


def build(sql):
    """A fresh connection holding exactly the four point-in-time tables.

    sql: {table: SELECT text producing that table's columns, in order}. A table left out is created
    empty, so a missing baseline reads as absent, never as zero."""
    unknown = set(sql) - set(STANDARD)
    if unknown:
        raise HarnessError(f"unknown tables {sorted(unknown)}")
    con = duckdb.connect()
    con.execute("SET threads TO 1")
    for table, cols in STANDARD.items():
        names = [c.split()[0] for c in cols]
        con.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
        if table in sql:
            con.execute(f"INSERT INTO {table} SELECT {', '.join(names)} FROM ({sql[table]}) "
                        f"ORDER BY {', '.join(names)}")
    return con


def _check_standard(con):
    rels = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()}
    if rels != set(STANDARD):
        raise HarnessError(f"connection must hold exactly {sorted(STANDARD)}, found {sorted(rels)}")


# Baselines -------------------------------------------------------------------------

def kalshi_quote(con, ticker, t, max_age):
    """(bid, ask) of the latest two-sided quote with t - max_age <= ts_utc <= t, or None."""
    return con.execute("""
        SELECT yes_bid, yes_ask FROM quotes
        WHERE ticker = ? AND ts_utc <= ? AND ts_utc >= ? AND yes_bid > 0 AND yes_ask < 1 AND yes_bid <= yes_ask
        ORDER BY ts_utc DESC LIMIT 1""", [ticker, _naive(t), _naive(t - max_age)]).fetchone()


def kalshi_mid(con, ticker, t, max_age):
    q = kalshi_quote(con, ticker, t, max_age)
    return None if q is None else (q[0] + q[1]) / 2


def pinnacle_fair(con, ticker, t, max_age):
    row = con.execute("""
        SELECT fair_prob FROM reference WHERE ticker = ? AND fetched_utc <= ? AND fetched_utc >= ?
        ORDER BY fetched_utc DESC LIMIT 1""", [ticker, _naive(t), _naive(t - max_age)]).fetchone()
    return None if row is None else row[0]


class KalshiMidSource:
    def __init__(self, max_age):
        self.max_age = max_age
        self.name = f"kalshi_mid(max_age={max_age})"

    def predict(self, con, ticker, t):
        return kalshi_mid(con, ticker, t, self.max_age)


# Isolation: a separate connection holding only what was known at t ---------------------

class Isolation:
    """A separate DuckDB connection holding `quotes` and `reference` rows at or before the current
    decision time, and `units`. It has no `outcomes` table and nothing later than t, so no table name
    can reach later data. advance(t) moves it forward in time; it never moves back."""

    def __init__(self, con):
        _check_standard(con)
        self._dir = tempfile.mkdtemp(prefix="harness-isolation-")
        self._files = {}
        for table, col in TIMED.items():
            path = os.path.join(self._dir, f"{table}.parquet")
            con.execute(f"COPY (SELECT * FROM {table} ORDER BY {col}) TO '{path}' (FORMAT parquet)")
            self._files[table] = path
        units = os.path.join(self._dir, "units.parquet")
        con.execute(f"COPY (SELECT * FROM units ORDER BY ticker) TO '{units}' (FORMAT parquet)")
        self.con = duckdb.connect()
        self.con.execute("SET threads TO 1")
        for table in ("quotes", "reference", "units"):
            self.con.execute(f"CREATE TABLE {table} ({', '.join(STANDARD[table])})")
        self.con.execute(f"INSERT INTO units SELECT * FROM read_parquet('{units}')")
        self.t = None

    def advance(self, t):
        tn = _naive(t)
        if self.t is not None and tn < self.t:
            raise HarnessError("the isolation connection only moves forward in time")
        for table, col in TIMED.items():
            if self.t is None:
                self.con.execute(f"INSERT INTO {table} SELECT * FROM read_parquet('{self._files[table]}') "
                                 f"WHERE {col} <= ?", [tn])
            elif tn > self.t:
                self.con.execute(f"INSERT INTO {table} SELECT * FROM read_parquet('{self._files[table]}') "
                                 f"WHERE {col} > ? AND {col} <= ?", [self.t, tn])
        self.t = tn

    def close(self):
        self.con.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _isolated_predictions(con, source, decisions):
    """{(ticker, t): p} with every prediction made on the isolation connection at its own t."""
    preds = {}
    with Isolation(con) as iso:
        for ticker, t in sorted(decisions, key=lambda d: (d[1], d[0])):
            iso.advance(t)
            try:
                preds[(ticker, t)] = source.predict(iso.con, ticker, t)
            except duckdb.Error as exc:
                raise LeakageError(f"{source.name} cannot predict from data available at decision time: "
                                   f"{ticker} at {_naive(t).isoformat()}Z: {exc}") from exc
    return preds


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= TOL


def check_no_peeking(con, source, decisions, sample=None, seed=0, preds=None):
    """Raise LeakageError if the source predicts differently with the full data than with only what was
    known at t. sample=None checks every decision. Returns the number checked."""
    _check_standard(con)
    decisions = sorted(set(decisions))
    picked = decisions if sample is None or len(decisions) <= sample else \
        sorted(random.Random(seed).sample(decisions, sample))
    if preds is None:
        preds = _isolated_predictions(con, source, picked)
    for ticker, t in picked:
        real = source.predict(con, ticker, t)
        if not _same(real, preds[(ticker, t)]):
            raise LeakageError(f"{source.name} peeks past decision time: {ticker} at {_naive(t).isoformat()}Z "
                               f"gives {real} with the full data, {preds[(ticker, t)]} with only what was "
                               f"known at t")
    return len(picked)


# Training: a separate connection holding only the training dates ---------------------------

class Training:
    """A separate connection for fit(): quotes and references before `cutoff`, and outcomes and units
    only for tickers whose cluster date is a training date."""

    def __init__(self, con, train_dates):
        _check_standard(con)
        self.cutoff = pacific_day_start(date.fromisoformat(max(train_dates)) + timedelta(days=1))
        cut = _naive(self.cutoff)
        dates = sorted(set(train_dates))
        self._dir = tempfile.mkdtemp(prefix="harness-training-")
        self.con = duckdb.connect()
        self.con.execute("SET threads TO 1")
        for table, cols in STANDARD.items():
            path = os.path.join(self._dir, f"{table}.parquet")
            if table in TIMED:
                con.execute(f"COPY (SELECT * FROM {table} WHERE {TIMED[table]} < ?) TO '{path}' (FORMAT parquet)",
                            [cut])
            else:
                con.execute(f"COPY (SELECT x.* FROM {table} x JOIN units u USING (ticker) "
                            f"WHERE u.cluster IN (SELECT unnest(?))) TO '{path}' (FORMAT parquet)", [dates])
            self.con.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
            self.con.execute(f"INSERT INTO {table} SELECT * FROM read_parquet('{path}')")
        shutil.rmtree(self._dir, ignore_errors=True)

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# Scoring ---------------------------------------------------------------------------

def _brier(p, y):
    return (p - y) ** 2


def _logloss(p, y):
    p = min(max(p, P_CLIP), 1 - P_CLIP)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _per_unit(rows, key):
    """{unit: (cluster, mean Brier, mean log loss)} over rows where `key` has a value."""
    acc = {}
    for r in rows:
        p = r[key]
        if p is None:
            continue
        a = acc.setdefault(r["unit"], [r["cluster"], 0.0, 0.0, 0])
        a[1] += _brier(p, r["y"])
        a[2] += _logloss(p, r["y"])
        a[3] += 1
    return {u: (c, b / n, l / n) for u, (c, b, l, n) in sorted(acc.items())}


def _mean(xs):
    return sum(xs) / len(xs)


def _rows(con, decisions, preds, max_age):
    outcome = dict(con.execute("SELECT ticker, y FROM outcomes").fetchall())
    unit = {t: (u, c) for t, u, c in con.execute("SELECT ticker, unit_id, cluster FROM units").fetchall()}
    rows = []
    for ticker, t in sorted(decisions):
        if ticker not in outcome or ticker not in unit:
            continue
        rows.append({"ticker": ticker, "t": t, "y": outcome[ticker], "unit": unit[ticker][0],
                     "cluster": unit[ticker][1], "source": preds[(ticker, t)],
                     "kalshi_mid": kalshi_mid(con, ticker, t, max_age),
                     "pinnacle_fair": pinnacle_fair(con, ticker, t, max_age)})
    return rows


def _summary(rows, seed, n_boot):
    result = {"scored_rows": len(rows), "by": {}, "paired": {}}
    per = {k: _per_unit(rows, k) for k in ("source", "kalshi_mid", "pinnacle_fair")}
    labels = {"kalshi_mid": "Kalshi mid", "pinnacle_fair": "Pinnacle fair"}
    for k, units in per.items():
        if not units:
            result["by"][k] = {"available": False, "note": f"not available (no {labels.get(k, k)} "
                                                           f"prices at decision time in this dataset)"}
            continue
        result["by"][k] = {"available": True, "rows": sum(1 for r in rows if r[k] is not None),
                           "units": len(units), "brier": _mean([v[1] for v in units.values()]),
                           "log_loss": _mean([v[2] for v in units.values()])}
    for base in ("kalshi_mid", "pinnacle_fair"):
        common = sorted(set(per["source"]) & set(per[base]))
        if not common:
            result["paired"][base] = {"available": False, "note": result["by"][base].get(
                "note", f"not available (no units with both the source and {labels[base]})")}
            continue
        db = [per["source"][u][1] - per[base][u][1] for u in common]
        dl = [per["source"][u][2] - per[base][u][2] for u in common]
        clusters = [per["source"][u][0] for u in common]
        result["paired"][base] = {
            "available": True, "units": len(common), "clusters": len(set(clusters)),
            "brier_diff": _mean(db), "brier_diff_ci": stats.bootstrap_ci(db, _mean, groups=clusters,
                                                                         n_boot=n_boot, seed=seed),
            "log_loss_diff": _mean(dl), "log_loss_diff_ci": stats.bootstrap_ci(dl, _mean, groups=clusters,
                                                                               n_boot=n_boot, seed=seed + 1)}
    return result


def _leak_sample(registered, sample):
    if registered and sample is not None:
        raise HarnessError("a registered run checks every decision; sample is for development runs only")
    return None if registered else sample


def score(con, decisions, source, max_age, seed=0, n_boot=2000, registered=True, sample=None):
    """Score `source` against the outcome, Kalshi mid and Pinnacle fair at each decision time. The scored
    predictions are made on the isolation connection; every decision (or, in a development run, a
    sample) is also checked against the full data."""
    _check_standard(con)
    sample = _leak_sample(registered, sample)
    decisions = sorted(set(decisions))
    preds = _isolated_predictions(con, source, decisions)
    checked = check_no_peeking(con, source, decisions, sample=sample, seed=seed, preds=preds)
    result = _summary(_rows(con, decisions, preds, max_age), seed, n_boot)
    return {"source": source.name, "decisions": len(decisions), "registered": registered,
            "leakage_checked": checked, **result}


def score_walk_forward(con, decisions, make_source, max_age, train_days, test_days, embargo_days=1,
                       expanding=True, step_days=None, seed=0, n_boot=2000, registered=True, sample=None):
    """Fit a fresh source per walk-forward fold on a connection holding only that fold's training dates,
    then score the fold's test decisions as score() does, pooled over folds."""
    _check_standard(con)
    if embargo_days < 1:
        raise HarnessError("walk-forward fitting needs embargo_days >= 1, so overnight settlements stay out of "
                           "the test dates")
    sample = _leak_sample(registered, sample)
    cluster = dict(con.execute("SELECT ticker, cluster FROM units").fetchall())
    decisions = sorted({d for d in decisions if d[0] in cluster})
    folds = walk_forward([cluster[t] for t, _ in decisions], train_days, test_days, step_days, embargo_days,
                         expanding)
    rows, checked, names, info = [], 0, set(), []
    for train_dates, test_dates in folds:
        test = [d for d in decisions if cluster[d[0]] in set(test_dates)]
        source = make_source()
        names.add(source.name)
        if hasattr(source, "fit"):
            with Training(con, train_dates) as tr:
                source.fit(tr.con)
        preds = _isolated_predictions(con, source, test)
        checked += check_no_peeking(con, source, test, sample=sample, seed=seed, preds=preds)
        rows += _rows(con, test, preds, max_age)
        info.append({"train": (train_dates[0], train_dates[-1]), "test": (test_dates[0], test_dates[-1]),
                     "test_decisions": len(test)})
    return {"source": " / ".join(sorted(names)), "decisions": len(decisions), "registered": registered,
            "leakage_checked": checked, "folds": info, **_summary(rows, seed, n_boot)}


# Walk-forward splits ------------------------------------------------------------------

def walk_forward(dates, train_days, test_days, step_days=None, embargo_days=0, expanding=True):
    """[(train_dates, test_dates)] over sorted distinct dates; tests never overlap and always follow
    the training dates plus the embargo."""
    if train_days < 1 or test_days < 1 or embargo_days < 0:
        raise HarnessError("train_days and test_days must be >= 1 and embargo_days >= 0")
    ds = sorted(set(dates))
    step = step_days or test_days
    if step < test_days:
        raise HarnessError("step_days below test_days would make test windows overlap")
    out, i = [], train_days
    while i + embargo_days + test_days <= len(ds):
        train = ds[:i] if expanding else ds[i - train_days:i]
        test = ds[i + embargo_days:i + embargo_days + test_days]
        out.append((train, test))
        i += step
    return out
