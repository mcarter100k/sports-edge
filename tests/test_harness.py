"""research/harness.py (docs/specs/harness_spec.md)."""
from datetime import datetime, timedelta, timezone

import pytest

from research import harness

T = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)


def ts(dt):
    return f"TIMESTAMP '{dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=' ')}'"


def con_with(quotes=(), reference=(), outcomes=(), units=()):
    def values(rows, fmt):
        return " UNION ALL ".join("SELECT " + fmt(r) for r in rows) if rows else None
    sql = {}
    q = values(quotes, lambda r: f"'{r[0]}' AS ticker, {ts(r[1])} AS ts_utc, {r[2]} AS yes_bid, {r[3]} AS yes_ask")
    if q:
        sql["quotes"] = q
    rf = values(reference, lambda r: f"'{r[0]}' AS ticker, {ts(r[1])} AS fetched_utc, {r[2]} AS fair_prob")
    if rf:
        sql["reference"] = rf
    o = values(outcomes, lambda r: f"'{r[0]}' AS ticker, {r[1]} AS y")
    if o:
        sql["outcomes"] = o
    u = values(units, lambda r: f"'{r[0]}' AS ticker, '{r[1]}' AS unit_id, '{r[2]}' AS cluster")
    if u:
        sql["units"] = u
    return harness.build(sql)


QUOTES = [("A", T - 3 * H, 0.30, 0.34), ("A", T - H, 0.40, 0.44), ("A", T, 0.50, 0.52),
          ("A", T + H, 0.90, 0.92), ("B", T - H, 0.0, 0.10), ("C", T - 8 * H, 0.20, 0.22)]


def test_kalshi_mid_takes_the_latest_two_sided_quote_at_or_before_t_within_age():
    con = con_with(QUOTES)
    assert harness.kalshi_mid(con, "A", T, 6 * H) == pytest.approx(0.51)     # stamped exactly t counts
    assert harness.kalshi_mid(con, "A", T - 30 * timedelta(minutes=1), 6 * H) == pytest.approx(0.42)
    assert harness.kalshi_mid(con, "B", T, 6 * H) is None                   # one-sided (no bid)
    assert harness.kalshi_mid(con, "C", T, 6 * H) is None                   # older than max_age
    assert harness.kalshi_mid(con, "C", T, 9 * H) == pytest.approx(0.21)
    assert harness.kalshi_mid(con, "Z", T, 6 * H) is None
    assert harness.kalshi_quote(con, "A", T, 6 * H) == (0.50, 0.52)
    assert harness.kalshi_quote(con, "B", T, 6 * H) is None


def test_naive_times_are_refused():
    con = con_with(QUOTES)
    with pytest.raises(harness.HarnessError):
        harness.kalshi_mid(con, "A", T.replace(tzinfo=None), H)


def test_build_refuses_unknown_tables_and_creates_missing_ones_empty():
    with pytest.raises(harness.HarnessError):
        harness.build({"candles": "SELECT 1"})
    con = harness.build({})
    assert con.execute("SELECT count(*) FROM reference").fetchone()[0] == 0


class LastQuoteSource:
    name = "last quote overall (leaks)"

    def predict(self, con, ticker, t):
        row = con.execute("SELECT (yes_bid + yes_ask) / 2 FROM quotes WHERE ticker = ? AND yes_bid > 0 "
                          "ORDER BY ts_utc DESC LIMIT 1", [ticker]).fetchone()
        return row and row[0]


class OutcomeSource:
    name = "reads outcomes (leaks)"

    def predict(self, con, ticker, t):
        row = con.execute("SELECT y FROM outcomes WHERE ticker = ?", [ticker]).fetchone()
        return 0.9 if row and row[0] == 1 else 0.1


class FutureReferenceSource:
    name = "reads reference after t (leaks)"

    def predict(self, con, ticker, t):
        row = con.execute("SELECT max(fair_prob) FROM reference WHERE ticker = ?", [ticker]).fetchone()
        return row[0]


class BidOnlySource:
    name = "last bid overall (leaks)"

    def predict(self, con, ticker, t):
        return con.execute("SELECT yes_bid FROM quotes WHERE ticker = ? ORDER BY ts_utc DESC LIMIT 1",
                           [ticker]).fetchone()[0]


class CountSource:
    name = "counts all quotes (leaks through truncation only)"

    def predict(self, con, ticker, t):
        return con.execute("SELECT count(*) FROM quotes WHERE ticker = ?", [ticker]).fetchone()[0] / 100


def leak_con():
    return con_with(QUOTES, reference=[("A", T - H, 0.45), ("A", T + H, 0.80)],
                    outcomes=[("A", 1), ("B", 0)], units=[("A", "g1", "2026-10-01"), ("B", "g1", "2026-10-01")])


def test_honest_source_passes_the_leakage_test():
    con = leak_con()
    assert harness.check_no_peeking(con, harness.KalshiMidSource(6 * H), [("A", T), ("B", T)]) == 2


@pytest.mark.parametrize("source,modes", [
    (LastQuoteSource(), ("poison",)), (LastQuoteSource(), ("truncate",)),
    (OutcomeSource(), ("poison",)), (OutcomeSource(), ("truncate",)),
    (FutureReferenceSource(), ("poison",)), (FutureReferenceSource(), ("truncate",)),
    (BidOnlySource(), ("poison",)),
    (CountSource(), ("truncate",)), (CountSource(), ("poison", "truncate"))])
def test_leaky_sources_are_caught_by_each_variant(source, modes):
    con = leak_con()
    with pytest.raises(harness.LeakageError, match="peeks past decision time"):
        harness.check_no_peeking(con, source, [("A", T)], modes=modes)
    # the real views are back afterwards
    assert con.execute("SELECT max(yes_bid) FROM quotes").fetchone()[0] == 0.90
    assert con.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 2


def test_score_refuses_a_leaky_source():
    with pytest.raises(harness.LeakageError):
        harness.score(leak_con(), [("A", T)], OutcomeSource(), 6 * H)


def test_extra_relations_are_refused():
    con = leak_con()
    con.execute("CREATE TABLE candles AS SELECT 1 AS x")
    with pytest.raises(harness.HarnessError, match="exactly"):
        harness.check_no_peeking(con, harness.KalshiMidSource(H), [("A", T)])


def game_con(days=6):
    """Each day: one game with two markets (one unit), quotes at t, outcome yes for the first market."""
    quotes, outcomes, units, ref, decisions = [], [], [], [], []
    for d in range(days):
        t = T + timedelta(days=d)
        for side, (bid, ask, y) in (("X", (0.60, 0.62, 1)), ("Y", (0.38, 0.40, 0))):
            tk = f"G{d}-{side}"
            quotes.append((tk, t - H, bid, ask))
            outcomes.append((tk, y))
            units.append((tk, f"G{d}", f"2026-10-{d + 1:02d}"))
            decisions.append((tk, t))
    return con_with(quotes, ref, outcomes, units), decisions


def test_score_counts_units_not_rows_and_reports_absent_pinnacle():
    con, decisions = game_con()
    r = harness.score(con, decisions, harness.KalshiMidSource(6 * H), 6 * H, n_boot=200)
    assert r["scored_rows"] == 12
    assert r["by"]["source"]["units"] == 6 and r["by"]["source"]["rows"] == 12
    assert r["by"]["source"]["brier"] == pytest.approx(0.39 ** 2)
    assert r["by"]["pinnacle_fair"] == {"available": False, "note": "not available (no Pinnacle fair prices at "
                                                                   "decision time in this dataset)"}
    assert r["paired"]["pinnacle_fair"]["available"] is False
    assert r["paired"]["kalshi_mid"]["brier_diff"] == 0
    assert r["leakage_checked"] == 12


class Shrunk:
    name = "mid shrunk to 0.5"

    def predict(self, con, ticker, t):
        m = harness.kalshi_mid(con, ticker, t, 6 * H)
        return None if m is None else 0.5 + 0.5 * (m - 0.5)


def test_paired_difference_and_seeded_cluster_interval():
    con, decisions = game_con()
    a = harness.score(con, decisions, Shrunk(), 6 * H, seed=3, n_boot=300)
    b = harness.score(con, decisions, Shrunk(), 6 * H, seed=3, n_boot=300)
    assert a == b
    p = a["paired"]["kalshi_mid"]
    assert p["units"] == 6 and p["clusters"] == 6
    expected = (0.5 + 0.5 * (0.61 - 0.5) - 1) ** 2 - 0.39 ** 2
    assert p["brier_diff"] == pytest.approx(expected)
    lo, hi = p["brier_diff_ci"]
    assert lo == pytest.approx(expected) and hi == pytest.approx(expected)   # every unit identical


def test_pinnacle_baseline_used_when_present():
    con = con_with(QUOTES, reference=[("A", T - H, 0.45)], outcomes=[("A", 1)], units=[("A", "g", "d")])
    r = harness.score(con, [("A", T)], harness.KalshiMidSource(6 * H), 6 * H, n_boot=50)
    assert r["by"]["pinnacle_fair"]["brier"] == pytest.approx(0.55 ** 2)
    assert r["paired"]["pinnacle_fair"]["brier_diff"] == pytest.approx(0.49 ** 2 - 0.55 ** 2)


def test_walk_forward_order_embargo_and_no_overlap():
    dates = [f"2026-10-{d:02d}" for d in range(1, 11)] + ["2026-10-03"]
    splits = harness.walk_forward(dates, train_days=4, test_days=2, embargo_days=1)
    assert [(tr[-1], te) for tr, te in splits] == [("2026-10-04", ["2026-10-06", "2026-10-07"]),
                                                  ("2026-10-06", ["2026-10-08", "2026-10-09"])]
    seen = set()
    for tr, te in splits:
        assert max(tr) < min(te) and not (set(te) & seen)
        seen |= set(te)
    rolling = harness.walk_forward(dates, train_days=4, test_days=2, expanding=False)
    assert all(len(tr) == 4 for tr, _ in rolling)
    with pytest.raises(harness.HarnessError):
        harness.walk_forward(dates, 4, 2, step_days=1)


def test_intervals_resample_whole_clusters():
    # Four games in two clusters of 3 and 1; within a cluster every game has the same difference, so a
    # cluster bootstrap can only produce d1, d2 or (3 d1 + d2) / 4. Resampling games instead reaches
    # (d1 + 3 d2) / 4 at the top, which is not allowed.
    quotes, outcomes, units, decisions = [], [], [], []
    for d, (bid, cl) in enumerate([(0.60, "c1"), (0.60, "c1"), (0.60, "c1"), (0.80, "c2")]):
        t = T + timedelta(days=d)
        tk = f"G{d}"
        quotes.append((tk, t - H, bid, bid + 0.02))
        outcomes.append((tk, 1))
        units.append((tk, tk, cl))
        decisions.append((tk, t))
    con = con_with(quotes, (), outcomes, units)
    r = harness.score(con, decisions, Shrunk(), 6 * H, seed=5, n_boot=400)
    d1 = (0.5 + 0.5 * 0.11 - 1) ** 2 - 0.39 ** 2
    d2 = (0.5 + 0.5 * 0.31 - 1) ** 2 - 0.19 ** 2
    allowed = {round(d1, 12), round(d2, 12), round((3 * d1 + d2) / 4, 12)}
    lo, hi = r["paired"]["kalshi_mid"]["brier_diff_ci"]
    assert round(lo, 12) in allowed and round(hi, 12) in allowed
    assert r["paired"]["kalshi_mid"]["clusters"] == 2
