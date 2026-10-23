"""research/studies/kalshi_calibration.py on a synthetic frozen dataset."""
import random
import re
from datetime import datetime, timedelta, timezone

import duckdb

from research import datasets
from research.studies import kalshi_calibration as study

T0 = datetime(2026, 3, 1, 3, 0, tzinfo=timezone.utc)      # expected expiration of game 0


def naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def make_dataset(tmp, flip=False, hockey_one_date=False):
    rng = random.Random(1)
    markets, candles, outcomes = [], [], []

    def market(ticker, event, grp, open_, close, expected):
        markets.append((ticker, event, "KXNBAGAME" if grp != "Weather" else "KXHIGHNY", grp, "binary",
                        naive(open_), naive(close), naive(expected)))

    def candle(ticker, ts, bid, ask):
        candles.append((ticker, naive(ts), bid, ask))

    def outcome(ticker, result):
        if flip and result in ("yes", "no"):
            result = "no" if result == "yes" else "yes"
        outcomes.append((ticker, result))

    for d in range(40):
        exp = T0 + timedelta(days=d)
        ev = f"KXNBAGAME-G{d:02d}"
        a, b = f"{ev}-AAA", f"{ev}-BBB"
        p = 0.15 + 0.7 * rng.random()
        close = exp - timedelta(hours=3, minutes=30) if d == 5 else exp - timedelta(minutes=20)   # game 5 ended early
        for tk in (a, b):
            market(tk, ev, "Basketball", exp - timedelta(days=5), close, exp)
        decision = exp - timedelta(hours=4)
        spread = 0.20 if d == 1 else 0.02
        if d == 4:                                    # game 4: only a quote 8 h old (stale)
            candle(a, decision - timedelta(hours=8), round(p - spread / 2, 2), round(p + spread / 2, 2))
        elif d != 2:                                  # game 2: no quote before the decision at all
            candle(a, decision - timedelta(minutes=30), round(p - spread / 2, 2), round(p + spread / 2, 2))
        candle(a, decision + timedelta(hours=2), 0.97, 0.99)       # after decision: must never be used
        y = 1 if rng.random() < p else 0
        outcome(a, "void" if d == 3 else ("yes" if y else "no"))
        outcome(b, "no" if y else "yes")
    if hockey_one_date:                               # 25 hockey games, all decided on one date
        for g in range(25):
            exp = T0 + timedelta(days=60, minutes=g)
            ev = f"KXNHLGAME-H{g:02d}"
            for side in ("AAA", "BBB"):
                markets.append((f"{ev}-{side}", ev, "KXNHLGAME", "Hockey", "binary", naive(exp - timedelta(days=5)),
                                naive(exp - timedelta(minutes=20)), naive(exp)))
            candle(f"{ev}-AAA", exp - timedelta(hours=4, minutes=30), 0.40, 0.42)
            outcome(f"{ev}-AAA", "yes" if g % 2 else "no")
            outcome(f"{ev}-BBB", "no" if g % 2 else "yes")
    # a three-market game (excluded)
    ev = "KXNBAGAME-THREE"
    for s in ("A", "B", "C"):
        market(f"{ev}-{s}", ev, "Basketball", T0 - timedelta(days=5), T0, T0)
        outcome(f"{ev}-{s}", "no")
    for d in range(30):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=d)      # January: EST
        t = day.replace(hour=5)                                                  # local midnight in New York
        ev = f"KXHIGHNY-26JAN{d + 1:02d}"
        win = rng.randrange(3)
        for i, price in enumerate((0.2, 0.5, 0.3)):
            tk = f"{ev}-B{i}"
            market(tk, ev, "Weather", t - timedelta(hours=14), t + timedelta(hours=24), t + timedelta(hours=36))
            candle(tk, t - timedelta(minutes=30), price - 0.01, price + 0.01)
            outcome(tk, "yes" if i == win else "no")
    con = duckdb.connect()
    con.execute("CREATE TABLE markets (ticker VARCHAR, event_ticker VARCHAR, series_ticker VARCHAR, grp VARCHAR, "
                "market_type VARCHAR, open_utc TIMESTAMP, close_utc TIMESTAMP, expected_expiration_utc TIMESTAMP)")
    con.executemany("INSERT INTO markets VALUES (?, ?, ?, ?, ?, ?, ?, ?)", markets)
    con.execute("CREATE TABLE candles (ticker VARCHAR, end_period_utc TIMESTAMP, yes_bid_close DOUBLE, "
                "yes_ask_close DOUBLE)")
    con.executemany("INSERT INTO candles VALUES (?, ?, ?, ?)", candles)
    con.execute("CREATE TABLE outcomes (ticker VARCHAR, result VARCHAR)")
    con.executemany("INSERT INTO outcomes VALUES (?, ?)", outcomes)
    paths = {}
    for t in ("markets", "candles", "outcomes"):
        paths[t] = tmp / f"{t}{'_f' if flip else ''}.parquet"
        con.execute(f"COPY {t} TO '{paths[t]}' (FORMAT parquet)")
    return datasets.freeze("synthetic", paths, root=tmp / "r")


def section(text, title):
    return text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def test_study_is_byte_identical_and_applies_every_rule(tmp_path):
    ds = make_dataset(tmp_path)
    d1 = study.run(ds, tmp_path / "a.md")
    d2 = study.run(ds, tmp_path / "b.md")
    a, b = (tmp_path / "a.md").read_bytes(), (tmp_path / "b.md").read_bytes()
    assert d1 == d2 and a == b
    text = a.decode()
    assert f"id `{ds.name}`" in text
    inc = section(text, "Inclusion")
    assert "Sports: games: 41" in inc
    assert "excluded: not a two-market binary game: 1" in inc
    assert "excluded games: spread over 10 cents: 1" in inc
    assert "excluded games: no two-sided quote within 6 h: 2" in inc
    assert "excluded games: not settled yes or no: 1" in inc
    assert "excluded: market closed too soon after the decision (game may have started): 1" in inc
    assert "Sports games scored: 35" in inc
    assert "Pinnacle fair baseline: not available (no Pinnacle fair prices at decision time in this dataset)" in text
    assert re.search(r"\| Sports \(pooled\) \| 35 \| 35 \|", text)
    assert "| Weather | 30 |" in text
    assert "INSUFFICIENT" in text          # Football, Hockey and Baseball have no games here
    assert "Leakage check (registered run)" in text
    assert re.search(r"leakage check on all 35 decisions passed", text)
    assert "Weather: bracket markets with a decision time: 90" in inc


def test_inclusion_never_depends_on_outcomes(tmp_path):
    a = make_dataset(tmp_path)
    b = make_dataset(tmp_path, flip=True)
    assert a.name != b.name
    study.run(a, tmp_path / "a.md")
    study.run(b, tmp_path / "b.md")
    ta, tb = (tmp_path / "a.md").read_text(), (tmp_path / "b.md").read_text()
    assert section(ta, "Inclusion") == section(tb, "Inclusion")
    assert section(ta, "Primary test") != section(tb, "Primary test")


def test_after_decision_candles_are_never_used(tmp_path):
    ds = make_dataset(tmp_path)
    study.run(ds, tmp_path / "a.md")
    text = (tmp_path / "a.md").read_text()
    # every game's after-decision candle is priced at 0.98; none may reach the calibration table
    table = section(text, "Calibration tables (descriptive)")
    assert "0.9800" not in table and "0.0200 |" not in table.split("Weather")[0]


def test_exclusion_bound_and_claim_rule():
    # 98 included games with mean -0.03; 2 excluded games priced 0.2 and 0.7 (underdog 0.2 and 0.3).
    b = study.exclusion_bound(98, -0.03, [0.2, 0.7])
    assert b == ((1 - 0.2 + 0.03) + (1 - 0.3 + 0.03)) / 100
    assert study.exclusion_bound(98, -0.03, []) == 0.0
    res = {"lo": -0.05, "hi": -0.01}
    assert study.survives(res, 0.009) and not study.survives(res, 0.011)
    assert study.survives({"lo": 0.02, "hi": 0.06}, 0.019) and not study.survives({"lo": 0.02, "hi": 0.06}, 0.021)


def test_weather_decision_time_does_not_depend_on_close_time(tmp_path):
    ds = make_dataset(tmp_path)
    con = datasets.open_dataset(ds)
    ms = study.markets(con)
    con.close()
    a, _ = study.weather_decisions(ms)
    moved = [{**m, "close": m["close"] + timedelta(days=3)} if m["grp"] == "Weather" else m for m in ms]
    b, _ = study.weather_decisions(moved)
    assert a == b and len(a) == 90
    assert {d["t"].hour for d in a} == {5}              # local midnight in New York in January (EST)
    assert a[0]["cluster"] == "2026-01-01"              # the measured date, not a Los Angeles date


def test_family_always_has_five_tests(tmp_path):
    ds = make_dataset(tmp_path)
    study.run(ds, tmp_path / "a.md")
    text = (tmp_path / "a.md").read_text()
    family = section(text, "Secondary family")
    rows = [line for line in family.splitlines() if line.startswith("| ") and not line.startswith("| group")]
    assert [r.split("|")[1].strip() for r in rows] == ["Football", "Basketball", "Hockey", "Baseball", "Weather"]
    assert sum("INSUFFICIENT" in r for r in rows) == 3


def test_the_exclusion_bound_is_printed(tmp_path):
    ds = make_dataset(tmp_path)
    study.run(ds, tmp_path / "a.md")
    primary = section((tmp_path / "a.md").read_text(), "Primary test")
    assert "exclusion bound B" in primary and primary.count("| 0.0221 |") == 1


def test_a_single_date_group_enters_the_family_as_insufficient(tmp_path):
    ds = make_dataset(tmp_path, hockey_one_date=True)
    study.run(ds, tmp_path / "a.md")
    family = section((tmp_path / "a.md").read_text(), "Secondary family")
    hockey = next(line for line in family.splitlines() if line.startswith("| Hockey"))
    assert hockey.startswith("| Hockey | 25 | 1 |") and "INSUFFICIENT" in hockey and "| 1.0000 |" in hockey


def test_claim_reasons_and_verdicts():
    rejected_clear = {"p": 0.001, "lo": -0.05, "hi": -0.03, "mean": -0.04}
    assert study.claim(rejected_clear, 0.01, True) == (True, "claimed")
    assert study.primary_verdict(rejected_clear, 0.01).startswith("REJECT H0, claimed")
    assert study.followup_met(rejected_clear, 0.01)
    killed_by_bound = {"p": 0.01, "lo": -0.03, "hi": -0.005, "mean": -0.017}
    assert study.claim(killed_by_bound, 0.01, True)[1].startswith("rejected, not claimed: the interval widened")
    assert study.primary_verdict(killed_by_bound, 0.01).startswith("H0 rejected, not claimed")
    assert not study.followup_met(killed_by_bound, 0.01)
    disagree = {"p": 0.04, "lo": -0.02, "hi": 0.001, "mean": -0.01}      # z test rejects, bootstrap interval does not
    assert "bootstrap interval itself includes 0" in study.claim(disagree, 0.0, True)[1]
    null = {"p": 0.4, "lo": -0.01, "hi": 0.02, "mean": 0.005}
    v = study.primary_verdict(null, 0.003)
    assert v.startswith("DO NOT REJECT H0") and "[-0.0130, +0.0230]" in v
    small = {"p": 0.001, "lo": -0.03, "hi": -0.015, "mean": -0.02}       # claimed but under the follow-up threshold
    assert study.claim(small, 0.0, True)[0] and not study.followup_met(small, 0.0)


def test_a_tested_price_that_reads_later_data_stops_the_run(tmp_path, monkeypatch):
    import pytest
    from research import harness
    ds = make_dataset(tmp_path)

    def leaky(self, con, ticker, t):
        row = con.execute("SELECT max(yes_bid) FROM quotes WHERE ticker = ?", [ticker]).fetchone()
        return row[0]

    monkeypatch.setattr(study.FilteredMid, "predict", leaky)
    with pytest.raises(harness.LeakageError):
        study.run(ds, tmp_path / "a.md")
    assert not (tmp_path / "a.md").exists()


def test_a_leak_on_an_excluded_decision_also_stops_the_run(tmp_path, monkeypatch):
    # Game 5 is excluded by the timing guard; its price enters only the exclusion bound, so only the study's
    # own all-decision check (not the harness score of scored games) can catch a leak there.
    import pytest
    from research import harness
    ds = make_dataset(tmp_path)
    honest = study.FilteredMid.predict

    def leaky(self, con, ticker, t):
        if ticker == "KXNBAGAME-G05-AAA":
            return con.execute("SELECT max(yes_bid) FROM quotes WHERE ticker = ?", [ticker]).fetchone()[0]
        return honest(self, con, ticker, t)

    monkeypatch.setattr(study.FilteredMid, "predict", leaky)
    with pytest.raises(harness.LeakageError):
        study.run(ds, tmp_path / "a.md")


def test_the_tested_price_applies_the_spread_rule():
    from research import harness
    t = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
    con = harness.build({"quotes": "SELECT 'W' AS ticker, TIMESTAMP '2026-03-01 11:00:00' AS ts_utc, 0.40 AS yes_bid, "
                                   "0.60 AS yes_ask UNION ALL SELECT 'N', TIMESTAMP '2026-03-01 11:00:00', 0.45, 0.47"})
    assert study.FilteredMid().predict(con, "W", t) is None
    assert abs(study.FilteredMid().predict(con, "N", t) - 0.46) < 1e-12
