"""Scoring harness (docs/specs/harness_spec.md). PROTECTED.

A probability source is scored at decision time against the outcome, the
Kalshi mid and the Pinnacle fair, per independent unit, with cluster-bootstrap
intervals. Sources only read the four point-in-time tables through `con`, and
check_no_peeking() refuses any source whose predictions change when data
after decision time is poisoned or removed.
"""
import math
import random
from datetime import datetime, timedelta, timezone

import duckdb

from . import stats

STANDARD = {
    "quotes": ("ticker VARCHAR", "ts_utc TIMESTAMP", "yes_bid DOUBLE", "yes_ask DOUBLE"),
    "reference": ("ticker VARCHAR", "fetched_utc TIMESTAMP", "fair_prob DOUBLE"),
    "outcomes": ("ticker VARCHAR", "y INTEGER"),
    "units": ("ticker VARCHAR", "unit_id VARCHAR", "cluster VARCHAR"),
}
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

def kalshi_mid(con, ticker, t, max_age):
    row = con.execute("""
        SELECT yes_bid, yes_ask FROM quotes
        WHERE ticker = ? AND ts_utc <= ? AND ts_utc >= ? AND yes_bid > 0 AND yes_ask < 1 AND yes_bid <= yes_ask
        ORDER BY ts_utc DESC LIMIT 1""", [ticker, _naive(t), _naive(t - max_age)]).fetchone()
    return None if row is None else (row[0] + row[1]) / 2


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


# Leakage test --------------------------------------------------------------------

def _views(con, schema, t, mode):
    """Shadow views of the four tables in `schema`: rows after t poisoned or removed."""
    tn = _naive(t).isoformat(sep=" ")
    con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    con.execute(f"CREATE SCHEMA {schema}")
    poison = f"(abs(hash(ticker || CAST(ts_utc AS VARCHAR) || 'x')) % 97 + 1) / 100.0"
    if mode == "poison":
        con.execute(f"""CREATE VIEW {schema}.quotes AS SELECT ticker, ts_utc,
            CASE WHEN ts_utc > TIMESTAMP '{tn}' THEN {poison} ELSE yes_bid END AS yes_bid,
            CASE WHEN ts_utc > TIMESTAMP '{tn}' THEN least({poison} + 0.01, 0.99) ELSE yes_ask END AS yes_ask
            FROM main.quotes""")
        con.execute(f"""CREATE VIEW {schema}.reference AS SELECT ticker, fetched_utc,
            CASE WHEN fetched_utc > TIMESTAMP '{tn}'
                 THEN (abs(hash(ticker || CAST(fetched_utc AS VARCHAR))) % 97 + 1) / 100.0 ELSE fair_prob END
                 AS fair_prob FROM main.reference""")
        con.execute(f"CREATE VIEW {schema}.outcomes AS SELECT ticker, 1 - y AS y FROM main.outcomes")
    else:
        con.execute(f"CREATE VIEW {schema}.quotes AS SELECT * FROM main.quotes WHERE ts_utc <= TIMESTAMP '{tn}'")
        con.execute(f"CREATE VIEW {schema}.reference AS SELECT * FROM main.reference "
                    f"WHERE fetched_utc <= TIMESTAMP '{tn}'")
        con.execute(f"CREATE VIEW {schema}.outcomes AS SELECT * FROM main.outcomes WHERE false")
    con.execute(f"CREATE VIEW {schema}.units AS SELECT * FROM main.units")


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= TOL


def check_no_peeking(con, source, decisions, sample=50, seed=0, modes=("poison", "truncate")):
    """Raise LeakageError if the source's prediction changes when data after t is poisoned or removed."""
    _check_standard(con)
    decisions = sorted(decisions)
    picked = decisions if len(decisions) <= sample else random.Random(seed).sample(decisions, sample)
    checked = 0
    try:
        for ticker, t in sorted(picked):
            real = source.predict(con, ticker, t)
            for mode in modes:
                schema = f"leak_{mode}"
                _views(con, schema, t, mode)
                con.execute(f"SET search_path = '{schema},main'")
                try:
                    other = source.predict(con, ticker, t)
                finally:
                    con.execute("RESET search_path")
                if not _same(real, other):
                    raise LeakageError(f"{source.name} peeks past decision time: {ticker} at "
                                       f"{_naive(t).isoformat()}Z gives {real} on real data, {other} with "
                                       f"later data {'poisoned' if mode == 'poison' else 'removed'}")
            checked += 1
    finally:
        con.execute("RESET search_path")
        for schema in ("leak_poison", "leak_truncate"):
            con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    return checked


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


def score(con, decisions, source, max_age, seed=0, n_boot=2000, leak_sample=50):
    """Score `source` against the outcome, Kalshi mid and Pinnacle fair at each decision time."""
    _check_standard(con)
    checked = check_no_peeking(con, source, decisions, sample=leak_sample, seed=seed)
    outcome = dict(con.execute("SELECT ticker, y FROM outcomes").fetchall())
    unit = {t: (u, c) for t, u, c in con.execute("SELECT ticker, unit_id, cluster FROM units").fetchall()}
    rows = []
    for ticker, t in sorted(decisions):
        if ticker not in outcome or ticker not in unit:
            continue
        rows.append({"ticker": ticker, "t": t, "y": outcome[ticker], "unit": unit[ticker][0],
                     "cluster": unit[ticker][1], "source": source.predict(con, ticker, t),
                     "kalshi_mid": kalshi_mid(con, ticker, t, max_age),
                     "pinnacle_fair": pinnacle_fair(con, ticker, t, max_age)})
    result = {"source": source.name, "decisions": len(decisions), "scored_rows": len(rows),
              "leakage_checked": checked, "by": {}, "paired": {}}
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
