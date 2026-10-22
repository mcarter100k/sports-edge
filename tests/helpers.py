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
         "no_ask_dollars": no_ask, "last_price_dollars": yes_ask, "yes_bid_size_fp": "10.00",
         "yes_ask_size_fp": "10.00", "volume_fp": "100.00", "volume_24h_fp": "50.00",
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
    """Stands in for KalshiClient; pages are lists of events."""

    def __init__(self, series_list, pages, fetched, settled=(), lookups=None, fail_on_page=None):
        self.series_list, self.pages, self.fetched = series_list, pages, fetched
        self.settled, self.lookups = list(settled), dict(lookups or {})
        self.fail_on_page = fail_on_page
        self.requests = 0
        self.settled_since = []

    def sports_series(self):
        self.requests += 1
        return self.series_list

    def open_events_with_markets(self):
        for i, page in enumerate(self.pages):
            self.requests += 1
            if self.fail_on_page == i:
                raise RuntimeError("simulated failure mid-pagination")
            yield self.fetched, page

    def settled_markets(self, since):
        self.requests += 1
        self.settled_since.append(since)
        return iter(self.settled)

    def market(self, ticker):
        self.requests += 1
        return self.lookups.get(ticker)


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
            stub = next(s for s in self.stubs[key] if s.source_event_id == eid)
            out.append(RefEvent(eid, key, stub.commence_utc, stub.home, stub.away, "pinnacle",
                                now - timedelta(minutes=2), now, dict(self.priced[eid])))
        return out


def ref_sport(key="basketball_nba", group="Basketball"):
    return RefSport(key, group, key.upper(), True, False)


def stub(eid, home, away, commence, key="basketball_nba"):
    return RefEventStub(eid, key, commence, home, away)
