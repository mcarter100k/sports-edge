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


def make_dataset(tmp, flip=False):
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
    # a three-market game (excluded)
    ev = "KXNBAGAME-THREE"
    for s in ("A", "B", "C"):
        market(f"{ev}-{s}", ev, "Basketball", T0 - timedelta(days=5), T0, T0)
        outcome(f"{ev}-{s}", "no")
    for d in range(30):
        close = T0 + timedelta(days=d, hours=2)
        ev = f"KXHIGHNY-W{d:02d}"
        win = rng.randrange(3)
        for i, price in enumerate((0.2, 0.5, 0.3)):
            tk = f"{ev}-B{i}"
            market(tk, ev, "Weather", close - timedelta(days=2), close, close + timedelta(hours=12))
            candle(tk, close - timedelta(hours=24, minutes=30), price - 0.01, price + 0.01)
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
    assert "Leakage test (registered run)" in text and "35 sports and 90 weather decisions, all passed" in text
    assert re.search(r"leakage check on all 35 decisions passed", text)


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
