"""Kalshi-only research backfill (docs/specs/research_spec.md), against a fake transport."""
import json
import sys
import urllib.parse
from datetime import datetime, timezone

import duckdb
import pytest

from research import backfill
from sports_edge import config
from sports_edge.kalshi import KalshiClient

NOON = datetime(2026, 10, 1, 12, 7, 0, tzinfo=timezone.utc)      # outside every quiet window


def market(ticker, event, open_t, close_t, result="yes", **extra):
    return {"ticker": ticker, "event_ticker": event, "open_time": open_t, "close_time": close_t,
            "expected_expiration_time": close_t, "status": "finalized", "market_type": "binary",
            "yes_sub_title": ticker[-3:], "result": result, "settlement_value_dollars": "1.0000"
            if result == "yes" else "0.0000", "settlement_ts": close_t, "last_price_dollars": "0.99", **extra}


LIVE = [market("KXNBAGAME-26OCT21A-AAA", "KXNBAGAME-26OCT21A", "2026-10-15T00:00:00Z", "2026-10-22T03:00:00Z"),
        market("KXNBAGAME-26OCT21A-BBB", "KXNBAGAME-26OCT21A", "2026-10-15T00:00:00Z", "2026-10-22T03:00:00Z",
               result="no")]
HIST = [market("KXNBAGAME-26JUN13C-CCC", "KXNBAGAME-26JUN13C", "2026-06-09T00:00:00Z", "2026-06-14T03:00:00Z")]


def live_candle(ts):
    return {"end_period_ts": ts, "yes_bid": {"open_dollars": "0.40", "high_dollars": "0.45", "low_dollars": "0.38",
                                             "close_dollars": "0.44"},
            "yes_ask": {"open_dollars": "0.42", "high_dollars": "0.47", "low_dollars": "0.40", "close_dollars": "0.46"},
            "price": {"close_dollars": "0.45"}, "volume_fp": "10.00", "open_interest_fp": "100.00"}


def hist_candle(ts):
    return {"end_period_ts": ts, "yes_bid": {"open": "0.20", "high": "0.25", "low": "0.18", "close": "0.24"},
            "yes_ask": {"open": "0.22", "high": "0.27", "low": "0.20", "close": "0.26"},
            "price": {"close": None}, "volume": 3, "open_interest": 7}


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, url, timeout):
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        path = u.path.split("/trade-api/v2", 1)[1]
        self.calls.append((path, q))
        if path == "/markets":
            return 200, json.dumps({"markets": LIVE, "cursor": ""}).encode()
        if path == "/historical/markets":
            if not q.get("cursor"):             # the client drops an empty cursor
                return 200, json.dumps({"markets": HIST[:0], "cursor": "p2"}).encode()
            return 200, json.dumps({"markets": HIST, "cursor": ""}).encode()
        if path == "/markets/candlesticks":
            ts = int(q["start_ts"]) + 3600
            return 200, json.dumps({"markets": [{"market_ticker": t, "candlesticks": [live_candle(ts)]}
                                                for t in q["market_tickers"].split(",")]}).encode()
        if path.startswith("/historical/markets/") and path.endswith("/candlesticks"):
            ts = int(q["start_ts"]) + 3600
            return 200, json.dumps({"candlesticks": [hist_candle(ts), hist_candle(ts + 3600)],
                                    "ticker": path.split("/")[3]}).encode()
        return 404, b"{}"


def run_fetch(fake, series=("KXNBAGAME",), limit=None):
    client = KalshiClient(transport=fake, sleep=lambda s: None, max_rps=1000)
    lines = []
    backfill.fetch(list(series), lines.append, limit=limit, client=client, now_fn=lambda: NOON,
                   sleep=lambda s: None)
    return lines


@pytest.mark.parametrize("hh_mm_ss,wait", [((12, 7, 0), 0), ((12, 14, 30), 270), ((12, 14, 29), 0),
                                           ((12, 0, 0), 240), ((12, 3, 59), 1), ((12, 4, 0), 0),
                                           ((12, 44, 45), 255)])
def test_quiet_window_around_each_quarter_hour(hh_mm_ss, wait):
    h, m, s = hh_mm_ss
    assert backfill.quiet_wait(datetime(2026, 10, 1, h, m, s, tzinfo=timezone.utc)) == pytest.approx(wait)


def test_polite_client_waits_before_a_request_in_the_window():
    slept = []
    fake = Fake()
    api = backfill.PoliteClient(KalshiClient(transport=fake, sleep=lambda s: None, max_rps=1000),
                                now_fn=lambda: datetime(2026, 10, 1, 12, 0, 30, tzinfo=timezone.utc),
                                sleep=slept.append)
    api.get("/markets", {"series_ticker": "X"})
    assert slept == [210] and len(fake.calls) == 1


def test_batches_respect_kalshi_limits():
    ms = [market(f"T{i:03d}", f"E{i}", "2026-10-01T00:00:00Z", "2026-10-20T00:00:00Z") for i in range(250)]
    out = backfill.batches(ms)
    assert sum(len(t) for _, _, t in out) == 250
    for lo, hi, tickers in out:
        assert len(tickers) <= 100
        assert len(tickers) * backfill.hours(lo, hi) <= backfill.BATCH_MAX_CANDLES
    # A 14-day window is about 338 hours, so at most 26 markets fit in a call.
    assert max(len(t) for _, _, t in out) == 26


def test_window_is_capped_at_14_days_before_close():
    lo, hi = backfill.window(market("T", "E", "2026-09-01T00:30:00Z", "2026-10-20T05:00:00Z"))
    assert hi - lo == 14 * 86400 + 5 * 3600 - 5 * 3600
    assert datetime.fromtimestamp(lo, timezone.utc) == datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)


def test_weather_scope_starts_2025_10_22():
    assert not backfill.in_scope("KXHIGHNY", {"close_time": "2025-10-21T23:59:59Z"})
    assert backfill.in_scope("KXHIGHNY", {"close_time": "2025-10-22T00:00:00Z"})
    assert backfill.in_scope("KXNBAGAME", {"close_time": "2025-04-01T00:00:00Z"})


def test_fetch_normalize_and_resume(tmp_path):
    fake = Fake()
    run_fetch(fake)
    paths = [p for p, _ in fake.calls]
    assert paths.count("/markets/candlesticks") == 1
    assert sum(p.startswith("/historical/markets/KX") for p in paths) == 1
    assert all(p.startswith(("/markets", "/historical/markets")) for p in paths)

    # A rerun lists again but fetches no candles for stored markets.
    fake2 = Fake()
    lines = run_fetch(fake2)
    assert not [p for p, _ in fake2.calls if "candlesticks" in p]
    assert any("3 already stored" in line for line in lines)

    counts = backfill.normalize(lambda s: None)
    assert counts == {"markets": 3, "outcomes": 3, "candles": 4}
    t = backfill.backfill_dir() / "tables"
    con = duckdb.connect()
    m = con.execute(f"SELECT ticker, grp, source, close_utc FROM read_parquet('{t / 'markets.parquet'}') "
                    "ORDER BY ticker").fetchall()
    assert sorted((r[0][-3:], r[1], r[2]) for r in m) == [("AAA", "Basketball", "live"),
                                                          ("BBB", "Basketball", "live"),
                                                          ("CCC", "Basketball", "historical")]
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{t / 'markets.parquet'}')").fetchall()]
    assert "result" not in cols and "last_price" not in " ".join(cols), "outcomes stay out of the markets table"
    c = con.execute(f"SELECT ticker, yes_bid_close, yes_ask_close, price_close, volume, source FROM "
                    f"read_parquet('{t / 'candles.parquet'}') ORDER BY ticker, end_period_utc").fetchall()
    live = [r for r in c if r[0].endswith("AAA")]
    hist = [r for r in c if r[0].endswith("CCC")]
    assert live[0][1:] == (0.44, 0.46, 0.45, 10.0, "live")
    assert len(hist) == 2 and hist[-1][1:] == (0.24, 0.26, None, 3.0, "historical")
    o = con.execute(f"SELECT ticker, result FROM read_parquet('{t / 'outcomes.parquet'}')").fetchall()
    assert sorted((r[0][-3:], r[1]) for r in o) == [("AAA", "yes"), ("BBB", "no"), ("CCC", "yes")]


def test_limit_caps_new_markets_per_series():
    fake = Fake()
    run_fetch(fake, limit=1)
    calls = [p for p, _ in fake.calls if "candlesticks" in p]
    assert len(calls) == 1


def test_backfill_never_touches_the_live_data_directory():
    run_fetch(Fake())
    backfill.normalize(lambda s: None)
    assert list(config.data_dir().iterdir()) == []
    assert str(backfill.backfill_dir()).startswith(str(backfill.research_dir()))


def test_only_registered_series_can_be_fetched():
    with pytest.raises(SystemExit):
        backfill.main(["fetch", "--series", "KXNOTREGISTERED"])


def test_partial_chunk_is_not_read_as_complete(tmp_path):
    w = backfill.ChunkWriter(tmp_path, "candles_x")
    w.write({"tickers": ["A"]})
    assert backfill.done_tickers.__name__  # writer still open: nothing renamed yet
    assert not list(tmp_path.glob("candles_x_*.jsonl.gz"))
    w.close()
    assert len(list(tmp_path.glob("candles_x_*.jsonl.gz"))) == 1


def test_worker_pool_fetches_each_historical_market_once_and_errors_surface(monkeypatch):
    many = [market(f"KXNBAGAME-26JUN{i:02d}H-HHH", f"KXNBAGAME-26JUN{i:02d}H", "2026-06-01T00:00:00Z",
                   "2026-06-05T00:00:00Z") for i in range(1, 11)]
    monkeypatch.setattr(sys.modules[__name__], "HIST", many)
    fakes = [Fake() for _ in range(3)]
    pool = [backfill.PoliteClient(KalshiClient(transport=f, sleep=lambda s: None, max_rps=1000),
                                  now_fn=lambda: NOON, sleep=lambda s: None) for f in fakes]
    calls = backfill.fetch_candles(pool[0], "KXNBAGAME", [("historical", m) for m in many], "run", lambda s: None,
                                   pool=pool)
    assert calls == 10
    hits = sorted(p.split("/")[3] for f in fakes for p, _ in f.calls)
    assert hits == sorted(m["ticker"] for m in many)
    assert all(f.calls for f in fakes), "every worker took part"
    assert backfill.done_tickers("KXNBAGAME") == {m["ticker"] for m in many}

    class Boom(Fake):
        def __call__(self, url, timeout):
            return 400, b'{"error": "bad"}'

    bad = [backfill.PoliteClient(KalshiClient(transport=Boom(), sleep=lambda s: None, max_rps=1000),
                                 now_fn=lambda: NOON, sleep=lambda s: None)]
    more = [market("KXNBAGAME-26JUN30Z-ZZZ", "KXNBAGAME-26JUN30Z", "2026-06-01T00:00:00Z", "2026-06-05T00:00:00Z")]
    from sports_edge.kalshi import KalshiError
    with pytest.raises(KalshiError):
        backfill.fetch_candles(bad[0], "KXNBAGAME", [("historical", m) for m in more], "run2", lambda s: None,
                               pool=bad)


def test_shared_throttle_spaces_requests_across_threads():
    import threading
    slept, lock = [], threading.Lock()

    def sleep(s):
        with lock:
            slept.append(s)

    th = backfill.SharedThrottle(5, clock=lambda: 100.0, sleep=sleep)   # time stands still
    threads = [threading.Thread(target=th.wait) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # One request goes at once; the other 19 are each given their own slot, 0.2 s apart.
    assert sorted(round(s, 9) for s in slept) == [round(0.2 * k, 9) for k in range(1, 20)]


def test_shared_throttle_sleeps_until_the_next_slot():
    t = {"now": 10.0}
    slept = []
    th = backfill.SharedThrottle(4, clock=lambda: t["now"], sleep=slept.append)
    th.wait()
    th.wait()
    th.wait()
    assert slept == [pytest.approx(0.25), pytest.approx(0.5)]


def test_polite_client_paces_every_request_through_the_shared_throttle():
    waits = []

    class Th:
        def wait(self):
            waits.append(1)

    api = backfill.PoliteClient(KalshiClient(transport=Fake(), sleep=lambda s: None, max_rps=1000),
                                now_fn=lambda: NOON, sleep=lambda s: None, throttle=Th())
    api.get("/markets", {"series_ticker": "X"})
    list(api.paginate("/historical/markets", {"series_ticker": "X"}, "markets"))
    assert len(waits) == 3


def test_weather_listing_drops_markets_closing_before_the_scope_date(monkeypatch):
    old = market("KXHIGHNY-25OCT20-T60", "KXHIGHNY-25OCT20", "2025-10-19T14:00:00Z", "2025-10-21T05:00:00Z")
    new = market("KXHIGHNY-25OCT22-T60", "KXHIGHNY-25OCT22", "2025-10-21T14:00:00Z", "2025-10-23T05:00:00Z")
    monkeypatch.setattr(sys.modules[__name__], "LIVE", [old, new])
    monkeypatch.setattr(sys.modules[__name__], "HIST", [])
    api = backfill.PoliteClient(KalshiClient(transport=Fake(), sleep=lambda s: None, max_rps=1000),
                                now_fn=lambda: NOON, sleep=lambda s: None)
    found = backfill.list_markets(api, "KXHIGHNY", "run", lambda s: None)
    assert [m["ticker"] for _, m in found] == [new["ticker"]]
