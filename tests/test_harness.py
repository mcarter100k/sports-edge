"""research/harness.py (docs/specs/harness_spec.md)."""
import random
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


# Baselines ---------------------------------------------------------------------------------

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


# The isolation connection ---------------------------------------------------------------------

def leak_con():
    return con_with(QUOTES, reference=[("A", T - H, 0.45), ("A", T + H, 0.80)],
                    outcomes=[("A", 1), ("B", 0)], units=[("A", "g1", "2026-10-01"), ("B", "g1", "2026-10-01")])


def test_isolation_holds_only_rows_at_or_before_t_and_no_outcomes():
    con = leak_con()
    with harness.Isolation(con) as iso:
        iso.advance(T)
        assert iso.con.execute("SELECT max(ts_utc) FROM quotes").fetchone()[0] == T.replace(tzinfo=None)
        assert iso.con.execute("SELECT count(*) FROM reference").fetchone()[0] == 1
        tables = {r[0] for r in iso.con.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables").fetchall()}
        assert tables == {"main.quotes", "main.reference", "main.units"}
        assert iso.con.execute("SELECT count(*) FROM main.quotes WHERE ts_utc > ?",
                               [T.replace(tzinfo=None)]).fetchone()[0] == 0
        iso.advance(T + H)
        assert iso.con.execute("SELECT count(*) FROM quotes").fetchone()[0] == 6
        with pytest.raises(harness.HarnessError):
            iso.advance(T)


class Spy:
    """Honest, and records what each connection it is handed contains."""
    name = "spy"

    def __init__(self):
        self.seen = []

    def predict(self, con, ticker, t):
        has_outcomes = con.execute("SELECT count(*) FROM information_schema.tables "
                                   "WHERE table_name = 'outcomes'").fetchone()[0] > 0
        latest = con.execute("SELECT max(ts_utc) FROM quotes").fetchone()[0]
        self.seen.append((has_outcomes, latest, t))
        return harness.kalshi_mid(con, ticker, t, 6 * H)


class LastQuoteSource:
    name = "last quote overall (leaks)"

    def predict(self, con, ticker, t):
        row = con.execute("SELECT (yes_bid + yes_ask) / 2 FROM quotes WHERE ticker = ? AND yes_bid > 0 "
                          "ORDER BY ts_utc DESC LIMIT 1", [ticker]).fetchone()
        return row and row[0]


class MainQuotesSource:
    name = "names main.quotes (leaks)"

    def predict(self, con, ticker, t):
        return con.execute("SELECT max(yes_bid) FROM main.quotes WHERE ticker = ?", [ticker]).fetchone()[0]


class OutcomeSource:
    name = "reads outcomes (leaks)"

    def predict(self, con, ticker, t):
        row = con.execute("SELECT y FROM outcomes WHERE ticker = ?", [ticker]).fetchone()
        return 0.9 if row and row[0] == 1 else 0.1


class FutureReferenceSource:
    name = "reads reference after t (leaks)"

    def predict(self, con, ticker, t):
        return con.execute("SELECT max(fair_prob) FROM reference WHERE ticker = ?", [ticker]).fetchone()[0]


class CountSource:
    name = "counts all quotes (leaks)"

    def predict(self, con, ticker, t):
        return con.execute("SELECT count(*) FROM quotes WHERE ticker = ?", [ticker]).fetchone()[0] / 100


class BidOnlySource:
    name = "last bid overall (leaks)"

    def predict(self, con, ticker, t):
        return con.execute("SELECT yes_bid FROM quotes WHERE ticker = ? ORDER BY ts_utc DESC LIMIT 1",
                           [ticker]).fetchone()[0]


def test_honest_source_passes():
    assert harness.check_no_peeking(leak_con(), harness.KalshiMidSource(6 * H), [("A", T), ("B", T)]) == 2


@pytest.mark.parametrize("source", [LastQuoteSource(), MainQuotesSource(), OutcomeSource(), FutureReferenceSource(),
                                    CountSource(), BidOnlySource()])
def test_leaky_sources_are_caught(source):
    with pytest.raises(harness.LeakageError):
        harness.check_no_peeking(leak_con(), source, [("A", T)])


def test_an_outcome_reader_is_caught_even_where_its_answer_would_not_change():
    # B settled no: OutcomeSource says 0.1 with or without the outcome, yet it is still caught,
    # because the isolation connection has no outcomes table at all.
    with pytest.raises(harness.LeakageError, match="cannot predict from data available at decision time"):
        harness.check_no_peeking(leak_con(), OutcomeSource(), [("B", T)])


def test_score_refuses_a_leaky_source():
    with pytest.raises(harness.LeakageError):
        harness.score(leak_con(), [("A", T)], MainQuotesSource(), 6 * H)


def test_extra_relations_are_refused():
    con = leak_con()
    con.execute("CREATE TABLE candles AS SELECT 1 AS x")
    with pytest.raises(harness.HarnessError, match="exactly"):
        harness.check_no_peeking(con, harness.KalshiMidSource(H), [("A", T)])


# Registered versus development runs -----------------------------------------------------------

def game_con(days=6, y_first=1):
    """Each day: one game with two markets (one unit), quotes at t - 1 h and t + 2 h."""
    quotes, outcomes, units, decisions = [], [], [], []
    for d in range(days):
        t = T + timedelta(days=d)
        for side, (bid, ask, y) in (("X", (0.60, 0.62, y_first)), ("Y", (0.38, 0.40, 1 - y_first))):
            tk = f"G{d}-{side}"
            quotes += [(tk, t - H, bid, ask), (tk, t + 2 * H, 0.97, 0.99)]
            outcomes.append((tk, y))
            units.append((tk, f"G{d}", f"2026-10-{d + 1:02d}"))
            decisions.append((tk, t))
    return con_with(quotes, (), outcomes, units), decisions


def test_every_scored_prediction_sees_only_data_known_at_t():
    con, decisions = game_con()
    spy = Spy()
    harness.score(con, decisions, spy, 6 * H, n_boot=50)
    iso_calls = [s for s in spy.seen if not s[0]]
    assert len(iso_calls) == len(decisions)
    assert all(latest is None or latest <= t.replace(tzinfo=None) for _, latest, t in iso_calls)


def test_registered_runs_check_every_decision_and_refuse_a_sample():
    con, decisions = game_con()
    r = harness.score(con, decisions, harness.KalshiMidSource(6 * H), 6 * H, n_boot=50)
    assert r["registered"] is True and r["leakage_checked"] == len(decisions) == 12
    with pytest.raises(harness.HarnessError, match="registered"):
        harness.score(con, decisions, harness.KalshiMidSource(6 * H), 6 * H, sample=3)
    dev = harness.score(con, decisions, harness.KalshiMidSource(6 * H), 6 * H, n_boot=50, registered=False, sample=3)
    assert dev["registered"] is False and dev["leakage_checked"] == 3


class LeaksOnOneTicker:
    """Peeks at the after-decision quote only for G5-X."""
    name = "leaks on G5-X"

    def predict(self, con, ticker, t):
        if ticker == "G5-X":
            return con.execute("SELECT max(yes_bid) FROM quotes WHERE ticker = ?", [ticker]).fetchone()[0]
        return harness.kalshi_mid(con, ticker, t, 6 * H)


class HonestTwin(LeaksOnOneTicker):
    """The same rule, restricted to what was known at t."""
    name = "honest twin"

    def predict(self, con, ticker, t):
        if ticker == "G5-X":
            return con.execute("SELECT max(yes_bid) FROM quotes WHERE ticker = ? AND ts_utc <= ?",
                               [ticker, t.replace(tzinfo=None)]).fetchone()[0]
        return harness.kalshi_mid(con, ticker, t, 6 * H)


def test_scored_predictions_come_from_the_isolation_connection():
    # A development run whose sample misses G5-X does not catch the leak, but the scored numbers still
    # cannot contain it: they were predicted without the later quote.
    con, decisions = game_con()
    picked = sorted(random.Random(0).sample(sorted(decisions), 2))
    assert ("G5-X", T + timedelta(days=5)) not in picked
    r = harness.score(con, decisions, LeaksOnOneTicker(), 6 * H, n_boot=50, registered=False, sample=2)
    honest = harness.score(con, decisions, HonestTwin(), 6 * H, n_boot=50)
    assert r["by"]["source"]["brier"] == pytest.approx(honest["by"]["source"]["brier"])
    leaky_value_brier = ((0.97 - 1) ** 2 + 0.39 ** 2) / 2      # what G5's unit would score with the later bid
    assert r["by"]["source"]["brier"] != pytest.approx((0.39 ** 2 * 5 + leaky_value_brier) / 6)
    with pytest.raises(harness.LeakageError):
        harness.score(con, decisions, LeaksOnOneTicker(), 6 * H, n_boot=50)


# Scoring ------------------------------------------------------------------------------------------

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
    assert lo == pytest.approx(expected) and hi == pytest.approx(expected)


def test_intervals_resample_whole_clusters():
    # Four games in two clusters of 3 and 1; a cluster bootstrap can only give d1, d2 or (3 d1 + d2) / 4.
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


def test_pinnacle_baseline_used_when_present():
    con = con_with(QUOTES, reference=[("A", T - H, 0.45)], outcomes=[("A", 1)], units=[("A", "g", "d")])
    r = harness.score(con, [("A", T)], harness.KalshiMidSource(6 * H), 6 * H, n_boot=50)
    assert r["by"]["pinnacle_fair"]["brier"] == pytest.approx(0.55 ** 2)
    assert r["paired"]["pinnacle_fair"]["brier_diff"] == pytest.approx(0.49 ** 2 - 0.55 ** 2)


# Walk-forward fitting -----------------------------------------------------------------------------

def series_con(days=12):
    """One market a day; the outcome alternates; cluster = the Los Angeles date of t."""
    quotes, outcomes, units, decisions = [], [], [], []
    for d in range(days):
        t = T + timedelta(days=d)
        tk = f"M{d:02d}"
        quotes += [(tk, t - H, 0.48, 0.52), (tk, t + 3 * H, 0.98, 0.99)]
        outcomes.append((tk, d % 2))
        units.append((tk, tk, (t - 7 * H).date().isoformat()))
        decisions.append((tk, t))
    return con_with(quotes, (), outcomes, units), decisions


class Memorizer:
    """Learns every outcome it can see in fit(); predicts it with certainty, else 0.5."""

    def __init__(self):
        self.name = "memorizer"
        self.known = {}
        self.fit_seen = None

    def fit(self, con):
        self.known = dict(con.execute("SELECT ticker, y FROM outcomes").fetchall())
        self.fit_seen = (con.execute("SELECT max(ts_utc) FROM quotes").fetchone()[0], set(self.known))

    def predict(self, con, ticker, t):
        return {1: 0.999, 0: 0.001}.get(self.known.get(ticker), 0.5)


def test_a_source_cannot_memorize_test_outcomes():
    con, decisions = series_con()
    made = []

    def make():
        made.append(Memorizer())
        return made[-1]

    r = harness.score_walk_forward(con, decisions, make, 6 * H, train_days=4, test_days=2, embargo_days=1, n_boot=50)
    assert len(made) == len(r["folds"]) == 3                  # one fresh instance per fold
    assert r["by"]["source"]["brier"] == pytest.approx(0.25)  # every test prediction was 0.5
    assert r["leakage_checked"] == sum(f["test_decisions"] for f in r["folds"])
    for m, fold in zip(made, r["folds"]):
        latest, tickers = m.fit_seen
        last_train = datetime.fromisoformat(fold["train"][1]).date()
        cutoff = harness.pacific_day_start(last_train + timedelta(days=1)).replace(tzinfo=None)
        assert latest < cutoff
        assert tickers == {f"M{d:02d}" for d in range((last_train - T.date()).days + 1)}
    # Positive control: fit on all the data, the same source predicts every outcome.
    full = Memorizer()
    full.fit(con)
    assert all(abs(full.predict(con, tk, t) - d % 2) < 0.01 for d, (tk, t) in enumerate(decisions))


def test_the_training_connection_holds_only_training_dates():
    con, decisions = series_con()
    with harness.Training(con, ["2026-10-01", "2026-10-02"]) as tr:
        assert {r[0] for r in tr.con.execute("SELECT ticker FROM outcomes").fetchall()} == {"M00", "M01"}
        assert {r[0] for r in tr.con.execute("SELECT ticker FROM units").fetchall()} == {"M00", "M01"}
        assert tr.cutoff == datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)   # midnight PDT
        assert tr.con.execute("SELECT max(ts_utc) FROM quotes").fetchone()[0] < tr.cutoff.replace(tzinfo=None)


def test_walk_forward_fitting_needs_an_embargo():
    con, decisions = series_con()
    with pytest.raises(harness.HarnessError, match="embargo"):
        harness.score_walk_forward(con, decisions, Memorizer, 6 * H, 4, 2, embargo_days=0)


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
