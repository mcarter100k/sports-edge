"""Kalshi-only backfill for research (docs/specs/research_spec.md). Zero odds credits.

    python -m research.backfill fetch [--group sports|weather|all] [--series S ...] [--limit N]
    python -m research.backfill normalize
    python -m research.backfill summary

GET requests to Kalshi's public market data only. Writes only under
$SPORTS_EDGE_RESEARCH_DIR/backfill; never opens the live database.
"""
import argparse
import csv
import glob
import gzip
import json
import math
import os
import queue
import sys
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from sports_edge.kalshi import KalshiClient, KalshiError
from sports_edge.timeutil import iso_z, parse_utc, utcnow

from .datasets import research_dir

SPORTS = {"KXNFLGAME": "Football", "KXNCAAFGAME": "Football", "KXNBAGAME": "Basketball",
          "KXNHLGAME": "Hockey", "KXMLBGAME": "Baseball"}
WEATHER = {s: "Weather" for s in ("KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHAUS", "KXHIGHLAX",
                                  "KXHIGHDEN", "KXHIGHPHIL")}
WEATHER_MIN_CLOSE = datetime(2025, 10, 22, tzinfo=timezone.utc)
GROUPS = {**SPORTS, **WEATHER}
WINDOW = timedelta(days=14)
PERIOD_MIN = 60
BATCH_MAX_MARKETS = 100
BATCH_MAX_CANDLES = 9000          # Kalshi's limit is 10,000; leave room for edge hours
CHUNK_REQUESTS = 200
QUIET_BEFORE_S = 30               # no requests from :x4:30 ... (30 s before each quarter hour)
QUIET_AFTER_S = 240               # ... to 4 min after it, while the live snapshot job runs
COOL_DOWN_S = 60                  # a 429 that outlasts the client's retries pauses every worker
COOL_DOWNS = 10


def backfill_dir():
    return research_dir() / "backfill"


def quiet_wait(now):
    """Seconds to wait so that no request lands near a quarter hour (0 if none)."""
    s = (now.minute % 15) * 60 + now.second + now.microsecond / 1e6
    if s >= 15 * 60 - QUIET_BEFORE_S:
        return 15 * 60 - s + QUIET_AFTER_S
    if s < QUIET_AFTER_S:
        return QUIET_AFTER_S - s
    return 0.0


class SharedThrottle:
    """At most `rps` requests a second across every thread that shares it."""

    def __init__(self, rps, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep = 1.0 / rps, clock, sleep
        self.lock = threading.Lock()
        self.next_at = None

    def wait(self):
        with self.lock:
            now = self.clock()
            at = now if self.next_at is None else max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            self.sleep(at - now)

    def cool_down(self, seconds):
        """Push every thread's next slot `seconds` ahead (after a lasting 429)."""
        with self.lock:
            self.next_at = max(self.next_at or 0.0, self.clock()) + seconds


class PoliteClient:
    """KalshiClient GETs, held off around each quarter hour and paced by a shared throttle."""

    def __init__(self, client, now_fn=utcnow, sleep=time.sleep, throttle=None):
        self.client, self.now_fn, self.sleep, self.throttle = client, now_fn, sleep, throttle

    def get(self, path, params=None):
        wait = quiet_wait(self.now_fn())
        if wait > 0:
            self.sleep(wait)
        for attempt in range(COOL_DOWNS + 1):
            if self.throttle is not None:
                self.throttle.wait()
            try:
                return self.client.get(path, params)
            except KalshiError as exc:
                if "HTTP 429" not in str(exc) or self.throttle is None or attempt == COOL_DOWNS:
                    raise
                self.throttle.cool_down(COOL_DOWN_S)

    def paginate(self, path, params, key):
        params, cursor = dict(params), ""
        while True:
            page = self.get(path, {**params, "cursor": cursor})
            if key not in page:
                raise ValueError(f"GET {path}: no {key!r} in response")
            yield page
            cursor = page.get("cursor") or ""
            if not cursor:
                return


# Raw storage -----------------------------------------------------------------

class ChunkWriter:
    """Gzipped JSON lines, written to a temporary file and renamed when complete, so a crash never
    leaves a half-written chunk that later looks complete."""

    def __init__(self, folder, stem):
        self.folder, self.stem, self.n, self.fh, self.tmp, self.count = Path(folder), stem, 0, None, None, 0
        self.folder.mkdir(parents=True, exist_ok=True)

    def write(self, record):
        if self.fh is None:
            fd, self.tmp = tempfile.mkstemp(prefix=".", suffix=".part", dir=self.folder)
            os.close(fd)
            self.fh = gzip.open(self.tmp, "wt")
        self.fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        self.count += 1
        if self.count >= CHUNK_REQUESTS:
            self.close()

    def close(self):
        if self.fh is None:
            return
        self.fh.close()
        final = self.folder / f"{self.stem}_{self.n:04d}.jsonl.gz"
        while final.exists():
            self.n += 1
            final = self.folder / f"{self.stem}_{self.n:04d}.jsonl.gz"
        os.rename(self.tmp, final)
        self.n += 1
        self.fh, self.tmp, self.count = None, None, 0


def read_chunks(pattern):
    for path in sorted(glob.glob(str(pattern))):
        with gzip.open(path, "rt") as fh:
            for line in fh:
                yield json.loads(line)


def stamp(now):
    return now.strftime("%Y%m%dT%H%M%SZ")


# Fetch -------------------------------------------------------------------------

def in_scope(series, market):
    if series in WEATHER:
        return parse_utc(market["close_time"]) >= WEATHER_MIN_CLOSE
    return True


def list_markets(api, series, run_stamp, out):
    """Settled markets of one series from both sets. Writes the raw pages; returns
    [(source, market)] in scope."""
    folder = backfill_dir() / "raw" / series
    found = []
    for source, path, params in (("live", "/markets", {"series_ticker": series, "status": "settled",
                                                       "limit": 1000}),
                                 ("historical", "/historical/markets", {"series_ticker": series, "limit": 1000})):
        writer = ChunkWriter(folder, f"markets_{source}_{run_stamp}")
        for page in api.paginate(path, params, "markets"):
            writer.write({"fetched_utc": iso_z(utcnow()), "source": source, "path": path, "page": page})
            found += [(source, m) for m in page["markets"] if in_scope(series, m)]
        writer.close()
    out(f"{series}: {len(found)} settled markets in scope")
    return found


def window(market):
    close = parse_utc(market["close_time"])
    start = max(parse_utc(market["open_time"]), close - WINDOW)
    start = start.replace(minute=0, second=0, microsecond=0)
    return int(start.timestamp()), int(close.timestamp())


def hours(start, end):
    return math.floor((end - start) / 3600) + 2


def batches(markets):
    """Pack live markets into batch calls within Kalshi's limits: [(start, end, [tickers])]."""
    out, cur, lo, hi = [], [], None, None
    for m in sorted(markets, key=lambda m: (window(m), m["ticker"])):
        s, e = window(m)
        nlo, nhi = (s, e) if not cur else (min(lo, s), max(hi, e))
        if cur and (len(cur) + 1 > BATCH_MAX_MARKETS or (len(cur) + 1) * hours(nlo, nhi) > BATCH_MAX_CANDLES):
            out.append((lo, hi, cur))
            cur, nlo, nhi = [], s, e
        cur.append(m["ticker"])
        lo, hi = nlo, nhi
    if cur:
        out.append((lo, hi, cur))
    return out


def done_tickers(series):
    done = set()
    for rec in read_chunks(backfill_dir() / "raw" / series / "candles_*.jsonl.gz"):
        done.update(rec["tickers"])
    return done


def historical_candles(api, m):
    lo, hi = window(m)
    quoted = urllib.parse.quote(m["ticker"], safe="")
    params = {"start_ts": lo, "end_ts": hi, "period_interval": PERIOD_MIN}
    resp = api.get(f"/historical/markets/{quoted}/candlesticks", params)
    return {"fetched_utc": iso_z(utcnow()), "source": "historical", "tickers": [m["ticker"]],
            "returned": [m["ticker"]], "params": params, "response": resp}


def fetch_candles(api, series, markets, run_stamp, out, limit=None, pool=None):
    """pool: a list of clients for historical calls, one per worker thread (default [api])."""
    done = done_tickers(series)
    todo = [(src, m) for src, m in markets if m["ticker"] not in done]
    if limit is not None:
        todo = todo[:limit]
    writer = ChunkWriter(backfill_dir() / "raw" / series, f"candles_{run_stamp}")
    calls = 0
    pool = pool or [api]
    try:
        for lo, hi, tickers in batches([m for src, m in todo if src == "live"]):
            params = {"market_tickers": ",".join(tickers), "start_ts": lo, "end_ts": hi,
                      "period_interval": PERIOD_MIN}
            resp = api.get("/markets/candlesticks", params)
            got = {x["market_ticker"] for x in resp.get("markets") or []}
            writer.write({"fetched_utc": iso_z(utcnow()), "source": "live", "tickers": tickers,
                          "returned": sorted(got), "params": params, "response": resp})
            calls += 1
        hist = [m for src, m in todo if src == "historical"]
        slices = [hist[i::len(pool)] for i in range(len(pool))]
        with ThreadPoolExecutor(max_workers=len(pool)) as ex:
            # Each worker walks its own slice with its own client; results come back to this thread,
            # which alone writes.
            results = queue.Queue()

            def work(client, part):
                for m in part:
                    results.put(historical_candles(client, m))

            futures = [ex.submit(work, c, part) for c, part in zip(pool, slices)]
            while any(not f.done() for f in futures) or not results.empty():
                try:
                    rec = results.get(timeout=0.5)
                except queue.Empty:
                    continue
                writer.write(rec)
                calls += 1
                if calls % 1000 == 0:
                    out(f"{series}: {calls} candle calls")
            for f in futures:
                f.result()          # re-raise a worker's error after its stored results are written
    finally:
        writer.close()
    out(f"{series}: {len(todo)} markets fetched in {calls} calls ({len(done)} already stored)")
    return calls


def fetch(series_list, out, limit=None, rps=None, client=None, now_fn=utcnow, sleep=time.sleep, workers=None):
    rps = rps or float(os.environ.get("RESEARCH_MAX_RPS", "3"))
    n = workers or (1 if client else int(os.environ.get("RESEARCH_WORKERS", "4")))
    throttle = SharedThrottle(rps, sleep=sleep)
    # Each worker's own client does not pace itself; the shared throttle does.
    clients = [client] if client else [KalshiClient(max_rps=1000) for _ in range(n)]
    pool = [PoliteClient(c, now_fn=now_fn, sleep=sleep, throttle=throttle) for c in clients]
    api = pool[0]
    run_stamp = stamp(utcnow())
    for series in series_list:
        markets = list_markets(api, series, run_stamp, out)
        fetch_candles(api, series, markets, run_stamp, out, limit, pool=pool)
    out(f"BACKFILL FETCH OK requests={sum(c.requests for c in clients)} workers={n}")


# Normalize --------------------------------------------------------------------

MARKET_COLS = ["ticker", "event_ticker", "series_ticker", "grp", "source", "market_type", "status", "title",
               "yes_sub_title", "no_sub_title", "open_utc", "close_utc", "expected_expiration_utc",
               "occurrence_utc", "strike_type", "floor_strike", "cap_strike", "listed_utc"]
OUTCOME_COLS = ["ticker", "result", "settlement_value", "settlement_utc"]
CANDLE_COLS = ["ticker", "end_period_utc", "yes_bid_open", "yes_bid_high", "yes_bid_low", "yes_bid_close",
               "yes_ask_open", "yes_ask_high", "yes_ask_low", "yes_ask_close", "price_close", "volume",
               "open_interest", "source"]


def _num(v):
    if v is None or v == "":
        return ""
    return str(v)


def _ohlc(d, field):
    """One OHLC value from either schema: live {'close_dollars': ..} or historical {'close': ..}."""
    if not isinstance(d, dict):
        return ""
    return _num(d.get(f"{field}_dollars", d.get(field)))


def _ts(v):
    return iso_z(parse_utc(v)) if v else ""


def market_row(series, source, m, listed):
    return {"ticker": m["ticker"], "event_ticker": m["event_ticker"], "series_ticker": series,
            "grp": GROUPS[series], "source": source, "market_type": m.get("market_type", ""),
            "status": m.get("status", ""), "title": m.get("title", ""), "yes_sub_title": m.get("yes_sub_title", ""),
            "no_sub_title": m.get("no_sub_title", ""), "open_utc": _ts(m.get("open_time")),
            "close_utc": _ts(m.get("close_time")), "expected_expiration_utc": _ts(m.get("expected_expiration_time")),
            "occurrence_utc": _ts(m.get("occurrence_datetime")), "strike_type": m.get("strike_type") or "",
            "floor_strike": _num(m.get("floor_strike")), "cap_strike": _num(m.get("cap_strike")),
            "listed_utc": listed}


def outcome_row(m):
    value = m.get("settlement_value_dollars", m.get("settlement_value"))
    return {"ticker": m["ticker"], "result": m.get("result") or "", "settlement_value": _num(value),
            "settlement_utc": _ts(m.get("settlement_ts"))}


def candle_rows(ticker, candles, source):
    for c in candles or []:
        yield {"ticker": ticker,
               "end_period_utc": iso_z(datetime.fromtimestamp(int(c["end_period_ts"]), timezone.utc)),
               **{f"yes_bid_{f}": _ohlc(c.get("yes_bid"), f) for f in ("open", "high", "low", "close")},
               **{f"yes_ask_{f}": _ohlc(c.get("yes_ask"), f) for f in ("open", "high", "low", "close")},
               "price_close": _ohlc(c.get("price"), "close"),
               "volume": _num(c.get("volume_fp", c.get("volume"))),
               "open_interest": _num(c.get("open_interest_fp", c.get("open_interest"))), "source": source}


def latest_listing(series, source):
    files = sorted(glob.glob(str(backfill_dir() / "raw" / series / f"markets_{source}_*.jsonl.gz")))
    if not files:
        return []
    run = os.path.basename(files[-1]).rsplit("_", 1)[0]
    return [f for f in files if os.path.basename(f).rsplit("_", 1)[0] == run]


SQL_TYPES = {"open_utc": "TIMESTAMP", "close_utc": "TIMESTAMP", "expected_expiration_utc": "TIMESTAMP",
             "occurrence_utc": "TIMESTAMP", "listed_utc": "TIMESTAMP", "settlement_utc": "TIMESTAMP",
             "end_period_utc": "TIMESTAMP", "floor_strike": "DOUBLE", "cap_strike": "DOUBLE",
             "settlement_value": "DOUBLE", "volume": "DOUBLE", "open_interest": "DOUBLE", "price_close": "DOUBLE"}


def _sql_type(col):
    if col.startswith(("yes_bid_", "yes_ask_")):
        return "DOUBLE"
    return SQL_TYPES.get(col, "VARCHAR")


def _write_table(rows, cols, path, key):
    """rows -> Parquet at path, one row per key (first seen wins), via a temporary CSV."""
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    seen, n = set(), 0
    with os.fdopen(fd, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            k = tuple(r[c] for c in key)
            if k in seen:
                continue
            seen.add(k)
            w.writerow({c: str(r[c]).replace("Z", "") if _sql_type(c) == "TIMESTAMP" else r[c] for c in cols})
            n += 1
    spec = "{" + ", ".join(f"'{c}': '{_sql_type(c)}'" for c in cols) + "}"
    con = duckdb.connect()
    try:
        con.execute("SET threads TO 1")
        con.execute(f"COPY (SELECT * FROM read_csv('{tmp}', header=true, columns={spec}, nullstr='', "
                    f"auto_detect=false) ORDER BY {', '.join(key)}) TO '{path}' (FORMAT parquet, COMPRESSION zstd)")
    finally:
        con.close()
        os.unlink(tmp)
    return n


def normalize(out):
    tables = backfill_dir() / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    markets, outcomes = [], []
    for series in GROUPS:
        for source in ("live", "historical"):
            for f in latest_listing(series, source):
                for rec in read_chunks(f):
                    for m in rec["page"]["markets"]:
                        if in_scope(series, m):
                            markets.append(market_row(series, source, m, rec["fetched_utc"]))
                            outcomes.append(outcome_row(m))
    markets.sort(key=lambda r: (r["ticker"], r["source"] != "live"))
    outcomes.sort(key=lambda r: r["ticker"])
    known = {r["ticker"] for r in markets}

    def candles():
        for series in GROUPS:
            for rec in read_chunks(backfill_dir() / "raw" / series / "candles_*.jsonl.gz"):
                resp = rec["response"]
                if rec["source"] == "live":
                    for x in resp.get("markets") or []:
                        if x["market_ticker"] in known:
                            yield from candle_rows(x["market_ticker"], x.get("candlesticks"), "live")
                else:
                    t = rec["tickers"][0]
                    if t in known:
                        yield from candle_rows(t, resp.get("candlesticks"), "historical")

    nm = _write_table(markets, MARKET_COLS, tables / "markets.parquet", ["ticker"])
    no = _write_table(outcomes, OUTCOME_COLS, tables / "outcomes.parquet", ["ticker"])
    nc = _write_table(candles(), CANDLE_COLS, tables / "candles.parquet", ["ticker", "end_period_utc"])
    out(f"BACKFILL NORMALIZE OK markets={nm} outcomes={no} candles={nc}")
    return {"markets": nm, "outcomes": no, "candles": nc}


def summary(out):
    """Counts only. Never prints outcomes."""
    t = backfill_dir() / "tables"
    con = duckdb.connect()
    try:
        rows = con.execute(f"""
            SELECT m.grp, m.series_ticker, count(*) AS markets, count(DISTINCT m.event_ticker) AS events,
                   count(DISTINCT c.ticker) AS with_candles
            FROM read_parquet('{t / 'markets.parquet'}') m
            LEFT JOIN (SELECT DISTINCT ticker FROM read_parquet('{t / 'candles.parquet'}')) c USING (ticker)
            GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    finally:
        con.close()
    out("group       series        markets  events  with_candles")
    for g, s, m, e, w in rows:
        out(f"{g:11s} {s:12s} {m:8d} {e:7d} {w:13d}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="research.backfill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--group", choices=["sports", "weather", "all"], default="all")
    f.add_argument("--series", nargs="*")
    f.add_argument("--limit", type=int, help="at most N new markets per series (for gates)")
    sub.add_parser("normalize")
    sub.add_parser("summary")
    args = ap.parse_args(argv)
    out = lambda s: print(s, flush=True)
    if args.cmd == "fetch":
        chosen = args.series or [s for s, g in GROUPS.items()
                                 if args.group == "all" or (args.group == "weather") == (g == "Weather")]
        unknown = [s for s in chosen if s not in GROUPS]
        if unknown:
            ap.error(f"series outside the registered scope: {unknown}")
        fetch(chosen, out, args.limit)
    elif args.cmd == "normalize":
        normalize(out)
    else:
        summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
