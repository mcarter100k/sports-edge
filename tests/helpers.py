"""Builders for controlled-input tests. Shapes follow the live API (2026-10-21)."""
from datetime import timedelta

from sports_edge.odds import RefEventStub, RefEvent, RefSport
from sports_edge.timeutil import iso_z


def series(ticker, tags=("Basketball",), fee_type="quadratic", mult=1):
    return {"ticker": ticker, "title": ticker, "tags": list(tags), "fee_type": fee_type,
            "fee_multiplier": mult, "category": "Sports", "categories": ["Sports"]}


def market(ticker, title, start, *, status="active", yes_bid="0.4100", yes_ask="0.4200",
           no_bid="0.5800", no_ask="0.5900", result="", settlement_value=None, close_after_h=45):
    exp = start + timedelta(hours=3)
    m = {"ticker": ticker, "yes_sub_title": title, "no_sub_title": title, "market_type": "binary",
         "status": status, "open_time": iso_z(start - timedelta(days=5)),
         "close_time": iso_z(exp + timedelta(hours=close_after_h)),
         "expected_expiration_time": iso_z(exp), "occurrence_datetime": iso_z(exp),
         "yes_bid_dollars": yes_bid, "yes_ask_dollars": yes_ask, "no_bid_dollars": no_bid,
         "no_ask_dollars": no_ask, "last_price_dollars": yes_ask, "yes_bid_size_fp": "500.00",
         "yes_ask_size_fp": "500.00", "volume_fp": "100.00", "volume_24h_fp": "50.00",
         "open_interest_fp": "20.00", "result": result}
    if settlement_value is not None:
        m["settlement_value_dollars"] = settlement_value
        m["settlement_ts"] = iso_z(exp + timedelta(hours=1))
    return m


def game_event(event_ticker, series_ticker, teams, start, *, tie=False, scope="Game", **mkw):
    markets = [market(f"{event_ticker}-{t[:3].upper()}", t, start, **mkw) for t in teams]
    if tie:
        markets.append(market(f"{event_ticker}-TIE", "Tie", start, **mkw))
    return {"event_ticker": event_ticker, "series_ticker": series_ticker,
            "title": " vs ".join(teams), "mutually_exclusive": True,
            "product_metadata": {"competition": "Test League", "competition_scope": scope},
            "fee_type_override": None, "fee_multiplier_override": None, "markets": markets}


class FakeKalshi:
    """Stands in for KalshiClient. pages: lists of events for the daily scan.
    by_ticker: {event_ticker: event} for matched fetches. by_series: {series: [events]}."""

    def __init__(self, series_list, pages, fetched, settled=(), lookups=None, fail_on_page=None,
                 active=True, by_ticker=None, by_series=None):
        self.series_list, self.pages, self.fetched = series_list, pages, fetched
        self.settled, self.lookups = list(settled), dict(lookups or {})
        self.fail_on_page, self.active = fail_on_page, active
        self.by_ticker = dict(by_ticker or {})
        for page in pages:
            for ev in page:
                self.by_ticker.setdefault(ev["event_ticker"], ev)
        self.by_series = dict(by_series or {})
        self.requests = 0
        self.settled_since, self.ticker_requests, self.series_requests = [], [], []
        self.scanned = False

    def exchange_active(self):
        self.requests += 1
        return self.active

    def sports_series(self):
        self.requests += 1
        return self.series_list

    def open_events_with_markets(self):
        self.scanned = True
        for i, page in enumerate(self.pages):
            self.requests += 1
            if self.fail_on_page == i:
                raise RuntimeError("simulated failure mid-pagination")
            yield self.fetched, page

    def events_by_tickers(self, tickers, batch):
        tickers = sorted(set(tickers))
        for i in range(0, len(tickers), batch):
            chunk = tickers[i:i + batch]
            self.requests += 1
            self.ticker_requests.append(chunk)
            yield self.fetched, [self.by_ticker[t] for t in chunk if t in self.by_ticker]

    def open_events_for_series(self, series):
        self.requests += 1
        self.series_requests.append(series)
        yield self.fetched, list(self.by_series.get(series, []))

    def settled_markets(self, since):
        self.requests += 1
        self.settled_since.append(since)
        return iter(self.settled)

    def market(self, ticker):
        self.requests += 1
        return self.lookups.get(ticker)

    def orderbook(self, ticker, depth=10):
        self.requests += 1
        self.book_requests = getattr(self, "book_requests", []) + [ticker]
        return (getattr(self, "books", {}) or {}).get(ticker, {"yes": [], "no": []})


class FakeOdds:
    name = "the-odds-api"

    def __init__(self, store, sports, stubs, priced, cost=1, now=None):
        self.store, self.sports, self.stubs, self.priced, self.cost = store, sports, stubs, priced, cost
        self.now = now   # the test clock: call times must not come from the wall clock
        self.priced_calls = []

    def _record(self, endpoint, key, credits):
        assert self.now is not None, "FakeOdds needs the test clock"
        self.store.append("odds_calls", [{"ts_utc": self.now, "sport_key": key, "endpoint": endpoint,
                                          "http_status": 200, "credits_last": credits,
                                          "credits_used": None, "credits_remaining": 400}])

    def list_sports(self):
        self._record("sports", None, 0)
        return self.sports

    def list_events(self, key):
        self._record("events", key, 0)
        return self.stubs.get(key, [])

    def fetch_moneylines(self, key, ids, now):
        self._record("odds", key, self.cost)
        self.priced_calls.append((key, list(ids)))
        out = []
        for eid in ids:
            if eid not in self.priced:   # like the real API: no Pinnacle price, no event
                continue
            stub = next(s for s in self.stubs[key] if s.source_event_id == eid)
            out.append(RefEvent(eid, key, stub.commence_utc, stub.home, stub.away, "pinnacle",
                                now - timedelta(minutes=2), now, dict(self.priced[eid])))
        return out


def ref_sport(key="basketball_nba", group="Basketball"):
    return RefSport(key, group, key.upper(), True, False)


def stub(eid, home, away, commence, key="basketball_nba"):
    return RefEventStub(eid, key, commence, home, away)


def fee_table(tmp_path, series=None, unconfirmed=None, default=("1", "0"), precision="0.0001"):
    """Write a cited fee table plus a stand-in source file; point the env at it."""
    import hashlib
    import json
    import os
    src = tmp_path / "fee_source.pdf"
    src.write_bytes(b"%PDF-1.4 stand-in for tests")
    table = {
        "source": {"file": str(src), "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                   "effective": "2026-07-07"},
        "default": {"taker": default[0], "maker": default[1], "cite": "p.1 General"},
        "settlement_fee": {"value": "0", "cite": "p.1 Settlement"},
        "rounding": {"balance_precision": precision, "cite": "p.3 Rounding"},
        "series": {k: {"taker": t, "maker": m, "cite": "p.2 Non-Standard Fees"}
                   for k, (t, m) in (series or {}).items()},
        "unconfirmed": dict(unconfirmed or {}),
    }
    path = tmp_path / "fee_schedule.json"
    path.write_text(json.dumps(table))
    os.environ["SPORTS_EDGE_FEE_TABLE"] = str(path)
    return path
