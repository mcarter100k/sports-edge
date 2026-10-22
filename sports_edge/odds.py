"""Reference odds: the source interface, The Odds API, and de-vig
(docs/specs/odds_matching_spec.md).
"""
import json
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from . import net
from .timeutil import parse_utc

MAX_OVERROUND = Decimal("1.15")
DRAW = "Draw"


class OddsError(Exception):
    pass


class DevigRejected(Exception):
    pass


@dataclass(frozen=True)
class RefSport:
    key: str
    group: str
    title: str
    active: bool
    has_outrights: bool


@dataclass(frozen=True)
class RefEventStub:
    source_event_id: str
    sport_key: str
    commence_utc: datetime
    home: str
    away: str


@dataclass
class RefEvent:
    source_event_id: str
    sport_key: str
    commence_utc: datetime
    home: str
    away: str
    bookmaker: str
    book_last_update_utc: datetime
    fetched_utc: datetime
    outcomes: dict = field(default_factory=dict)   # name -> Decimal decimal odds


class ReferenceSource(Protocol):
    name: str

    def list_sports(self) -> list: ...

    def list_events(self, sport_key) -> list: ...

    def fetch_moneylines(self, sport_key, event_ids, now) -> list: ...


def devig_proportional(outcomes):
    """{name: decimal odds} -> ({name: fair prob as float}, overround as float).

    Rejects any price <= 1.0 and an overround outside [1.00, 1.15].
    """
    if len(outcomes) < 2:
        raise DevigRejected(f"need at least 2 outcomes, got {len(outcomes)}")
    implied = {}
    for name, odds in outcomes.items():
        odds = Decimal(str(odds))
        if odds <= 1:
            raise DevigRejected(f"price {odds} for {name!r} is not above 1.0")
        implied[name] = Decimal(1) / odds
    total = sum(implied.values())
    if not (Decimal(1) <= total <= MAX_OVERROUND):
        raise DevigRejected(f"overround {total:.4f} outside [1.00, {MAX_OVERROUND}]")
    return {k: float(v / total) for k, v in implied.items()}, float(total)


class TheOddsApi:
    """The Odds API v4. /sports and /events are free; /odds costs credits."""

    name = "the-odds-api"
    host = "https://api.the-odds-api.com"
    bookmaker = "pinnacle"

    def __init__(self, api_key, transport=None, on_call=None):
        if not api_key:
            raise OddsError("ODDS_API_KEY is not set")
        self.api_key = api_key
        self.transport = transport or net.https_get
        self.on_call = on_call or (lambda **kw: None)

    def _get(self, path, params, sport_key, endpoint):
        query = urllib.parse.urlencode({"apiKey": self.api_key, **params})
        status, headers, body = self.transport(f"{self.host}{path}?{query}", 30)
        lower = {k.lower(): v for k, v in (headers or {}).items()}

        def num(name):
            value = lower.get(name)
            try:
                return int(float(value)) if value not in (None, "") else None
            except ValueError:
                return None

        self.on_call(sport_key=sport_key, endpoint=endpoint, http_status=status,
                     credits_last=num("x-requests-last"), credits_used=num("x-requests-used"),
                     credits_remaining=num("x-requests-remaining"))
        if status != 200:
            # Never echo the URL: it carries the key.
            raise OddsError(f"{endpoint} {sport_key or ''} failed: HTTP {status}: {body[:200]!r}")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise OddsError(f"{endpoint}: body is not JSON") from exc

    def list_sports(self):
        data = self._get("/v4/sports", {}, None, "sports")
        return [RefSport(s["key"], s["group"], s["title"], bool(s["active"]),
                         bool(s.get("has_outrights")))
                for s in data]

    def list_events(self, sport_key):
        data = self._get(f"/v4/sports/{sport_key}/events", {}, sport_key, "events")
        return [RefEventStub(e["id"], e["sport_key"], parse_utc(e["commence_time"]),
                             e["home_team"], e["away_team"]) for e in data]

    def fetch_moneylines(self, sport_key, event_ids, now):
        data = self._get(f"/v4/sports/{sport_key}/odds", {
            "markets": "h2h", "oddsFormat": "decimal", "bookmakers": self.bookmaker,
            "eventIds": ",".join(event_ids)}, sport_key, "odds")
        events = []
        for e in data:
            for book in e.get("bookmakers") or []:
                if book.get("key") != self.bookmaker:
                    continue
                for market in book.get("markets") or []:
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: Decimal(str(o["price"])) for o in market["outcomes"]}
                    events.append(RefEvent(
                        source_event_id=e["id"], sport_key=e["sport_key"],
                        commence_utc=parse_utc(e["commence_time"]), home=e["home_team"],
                        away=e["away_team"], bookmaker=self.bookmaker,
                        book_last_update_utc=parse_utc(market.get("last_update") or book.get("last_update")),
                        fetched_utc=now, outcomes=outcomes))
        return events
