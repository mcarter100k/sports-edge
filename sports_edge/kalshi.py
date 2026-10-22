"""Read-only Kalshi market data client (docs/specs/kalshi_client_spec.md).

GET requests to public market-data endpoints only. There is no signing code
and no method that sends anything other than GET.
"""
import json
import time
import urllib.error
import urllib.parse
from decimal import Decimal, InvalidOperation

from . import config, net

RETRY_DELAYS = (1, 2, 4, 8, 16)
MAX_PAGES = 2000
TIMEOUT_S = 30


class KalshiError(Exception):
    pass


class KalshiSchemaError(KalshiError):
    pass


class PaginationLimit(KalshiError):
    pass


def dec(value):
    """Fixed-point string -> Decimal. '' or None -> None, never 0."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise KalshiSchemaError(f"not a number: {value!r}") from exc


def require(obj, *fields, where=""):
    missing = [f for f in fields if f not in obj]
    if missing:
        raise KalshiSchemaError(f"{where or 'response'} missing required field(s) {missing}")
    return obj


def urllib_transport(url, timeout):
    """Perform one GET. Returns (status, body_bytes). HTTP errors return their status."""
    status, _headers, body = net.https_get(url, timeout)
    return status, body


class KalshiClient:
    def __init__(self, base_url=None, max_rps=None, transport=None, sleep=time.sleep,
                 clock=time.monotonic):
        self.base_url = (base_url or config.kalshi_base_url()).rstrip("/")
        self.min_interval = 1.0 / (max_rps or config.kalshi_max_rps())
        self.transport = transport or urllib_transport
        self.sleep = sleep
        self.clock = clock
        self._last = None
        self.requests = 0

    def _throttle(self):
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def get(self, path, params=None):
        query = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        last_error = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            self._throttle()
            self.requests += 1
            try:
                status, body = self.transport(url, TIMEOUT_S)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                if net.is_permanent(exc):
                    raise KalshiError(f"GET {path} failed: {exc}") from exc
                status, body, last_error = None, b"", f"{type(exc).__name__}: {exc}"
            if status == 200:
                try:
                    return json.loads(body)
                except ValueError as exc:
                    raise KalshiSchemaError(f"GET {path}: body is not JSON") from exc
            if status is not None:
                last_error = f"HTTP {status}: {body[:200]!r}"
                if status != 429 and status < 500:
                    raise KalshiError(f"GET {path} failed: {last_error}")
            if attempt < len(RETRY_DELAYS):
                self.sleep(RETRY_DELAYS[attempt])
        raise KalshiError(f"GET {path} failed after {len(RETRY_DELAYS) + 1} attempts: {last_error}")

    def paginate(self, path, params, items_key):
        params = dict(params)
        cursor = ""
        for _ in range(MAX_PAGES):
            page = self.get(path, {**params, "cursor": cursor})
            require(page, items_key, where=f"GET {path}")
            yield page
            cursor = page.get("cursor") or ""
            if not cursor:
                return
        raise PaginationLimit(f"GET {path}: more than {MAX_PAGES} pages")

    # Endpoints -------------------------------------------------------------

    def sports_series(self):
        data = self.get("/series", {"category": "Sports"})
        require(data, "series", where="GET /series")
        for s in data["series"]:
            require(s, "ticker", "tags", "fee_type", "fee_multiplier", where=f"series {s.get('ticker')}")
        return data["series"]

    def open_events_with_markets(self):
        """Yield (fetched_utc, events) per page."""
        from .timeutil import utcnow
        for page in self.paginate("/events", {"status": "open", "with_nested_markets": "true",
                                              "limit": 200}, "events"):
            fetched = utcnow()
            for ev in page["events"]:
                require(ev, "event_ticker", "series_ticker", where="event")
                for m in ev.get("markets") or []:
                    require(m, "ticker", "status", "close_time", where=f"market in {ev['event_ticker']}")
            yield fetched, page["events"]

    def exchange_active(self):
        data = self.get("/exchange/status")
        require(data, "exchange_active", where="GET /exchange/status")
        return bool(data["exchange_active"])

    def events_by_tickers(self, event_tickers, batch):
        """Yield (fetched_utc, events) for events of any status, in batches of event tickers."""
        from .timeutil import utcnow
        tickers = sorted(set(event_tickers))
        for i in range(0, len(tickers), batch):
            chunk = tickers[i:i + batch]
            for page in self.paginate("/events", {"tickers": ",".join(chunk),
                                                  "with_nested_markets": "true", "limit": 200}, "events"):
                yield utcnow(), self._checked(page["events"])

    def open_events_for_series(self, series_ticker):
        """Yield (fetched_utc, events) for one series' open events."""
        from .timeutil import utcnow
        for page in self.paginate("/events", {"series_ticker": series_ticker, "status": "open",
                                              "with_nested_markets": "true", "limit": 200}, "events"):
            yield utcnow(), self._checked(page["events"])

    def _checked(self, events):
        for ev in events:
            require(ev, "event_ticker", "series_ticker", where="event")
            for m in ev.get("markets") or []:
                require(m, "ticker", "status", "close_time", where=f"market in {ev['event_ticker']}")
        return events

    def settled_markets(self, min_settled_ts: int):
        for page in self.paginate("/markets", {"status": "settled", "min_settled_ts": min_settled_ts,
                                               "mve_filter": "exclude", "limit": 1000}, "markets"):
            for m in page["markets"]:
                require(m, "ticker", "status", "result", where="settled market")
                yield m

    def orderbook(self, ticker, depth=10):
        """Resting bids per side: {"yes": [(price, count)], "no": [...]}, best (highest) first.

        A Yes ask at p is a No bid at 1 - p, and the reverse.
        """
        quoted = urllib.parse.quote(ticker, safe="")
        data = self.get(f"/markets/{quoted}/orderbook", {"depth": depth})
        require(data, "orderbook_fp", where=f"GET /markets/{ticker}/orderbook")
        book = data["orderbook_fp"] or {}
        out = {}
        for side in ("yes", "no"):
            levels = [(dec(p), dec(n)) for p, n in (book.get(f"{side}_dollars") or [])]
            out[side] = sorted(levels, key=lambda lv: lv[0], reverse=True)
        return out

    def market(self, ticker):
        """One market from the live set, then the historical set. None if neither has it."""
        quoted = urllib.parse.quote(ticker, safe="")
        for path in (f"/markets/{quoted}", f"/historical/markets/{quoted}"):
            try:
                data = self.get(path)
            except KalshiError as exc:
                if "HTTP 404" in str(exc):
                    continue
                raise
            require(data, "market", where=f"GET {path}")
            return data["market"]
        return None
