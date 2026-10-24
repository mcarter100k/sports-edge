"""Kalshi game events <-> reference events (docs/specs/odds_matching_spec.md).

Never guess: a match needs every rule to hold, and it must be unique both ways.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from .timeutil import eastern_to_utc, parse_utc

RULE_VERSION = "match_v3"          # v2: "st" reads as "state"; v3: Kalshi placeholder times
TIE = "Tie"
DRAW = "Draw"
WINDOW_BEFORE = timedelta(hours=8)
PLACEHOLDER_AFTER = timedelta(hours=32)   # widened window for placeholder events, tried only if the normal one is empty
PLACEHOLDER_ET = (23, 0)                  # Kalshi's placeholder: 23:00 America/New_York on the ticker's date
TICKER_DATE = re.compile(r"^[^-]+-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})")
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


@dataclass
class KalshiGame:
    event_ticker: str
    sport_tags: tuple
    resolve_by: object            # aware datetime
    teams: dict                   # yes_sub_title -> market_ticker (excludes Tie)
    tie_market: str = None        # market ticker of the Tie market, if 3-way
    placeholder: bool = False     # Kalshi's time is a placeholder (is_placeholder)


@dataclass
class MatchResult:
    matches: list = field(default_factory=list)      # dicts: market_ticker, event_ticker, source_event_id, ref_outcome
    unmatched: list = field(default_factory=list)    # dicts: event_ticker, sport, reason, detail
    claimed: set = field(default_factory=set)        # reference ids that fit at least one Kalshi game


def normalize(name):
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\bst\b", "state", text)        # "Appalachian St." = "Appalachian State"


def names_match(kalshi_name, ref_name, aliases):
    k, r = normalize(kalshi_name), normalize(ref_name)
    if not k or not r:
        return False
    if k == r or r.startswith(k + " "):
        return True
    return kalshi_name in aliases and normalize(aliases[kalshi_name]) == r


def ticker_date(event_ticker):
    """The date in an event ticker's first -YYMMMDD token, or None."""
    m = TICKER_DATE.match(event_ticker or "")
    if not m:
        return None
    try:
        return date(2000 + int(m.group(1)), MONTHS.index(m.group(2)) + 1, int(m.group(3)))
    except ValueError:
        return None


def is_placeholder(event_ticker, markets):
    """True only if every market's expected expiration equals its occurrence time, all markets agree,
    and that time is exactly 23:00 America/New_York on the ticker's date (spec amendment, match_v3)."""
    day = ticker_date(event_ticker)
    if day is None or not markets:
        return False
    times = set()
    for m in markets:
        exp = parse_utc(m.get("expected_expiration_time"))
        occ = parse_utc(m.get("occurrence_datetime", m.get("occurrence_time")))
        if exp is None or occ is None or exp != occ:
            return False
        times.add(exp)
    return len(times) == 1 and times.pop() == eastern_to_utc(day, *PLACEHOLDER_ET)


def kalshi_game(event, sport_tags, resolve_by):
    """Return (KalshiGame, None) if the event is a moneyline game, else (None, reason)."""
    meta = event.get("product_metadata") or {}
    markets = event.get("markets") or []
    if meta.get("competition_scope") != "Game":
        return None, "not_moneyline:scope"
    if not event.get("mutually_exclusive"):
        return None, "not_moneyline:not_mutually_exclusive"
    if len(markets) not in (2, 3):
        return None, f"not_moneyline:{len(markets)}_markets"
    titles = [(m.get("yes_sub_title") or "").strip() for m in markets]
    if any(not t for t in titles) or any(m.get("market_type", "binary") != "binary" for m in markets):
        return None, "not_moneyline:market_shape"
    ties = [m for m, t in zip(markets, titles) if t == TIE]
    if (len(markets) == 3) != (len(ties) == 1) or len(ties) > 1:
        return None, "not_moneyline:tie_shape"
    teams = {t: m["ticker"] for m, t in zip(markets, titles) if t != TIE}
    if len(teams) != 2:
        return None, "not_moneyline:duplicate_titles"
    if resolve_by is None:
        return None, "not_moneyline:no_resolve_time"
    return KalshiGame(event["event_ticker"], tuple(sport_tags), resolve_by, teams,
                      ties[0]["ticker"] if ties else None, is_placeholder(event["event_ticker"], markets)), None


def _team_mapping(game, stub, aliases):
    """Map each Kalshi team to exactly one distinct reference team, or None."""
    ref_teams = [stub.home, stub.away]
    mapping = {}
    for k in game.teams:
        hits = [r for r in ref_teams if names_match(k, r, aliases)]
        if len(hits) != 1:
            return None
        mapping[k] = hits[0]
    if len(set(mapping.values())) != 2:
        return None
    return mapping


def time_window(game, widened=False):
    """Rule 4's window; for a placeholder event the widened one (tried only after the normal one)."""
    return (game.resolve_by - WINDOW_BEFORE,
            game.resolve_by + (PLACEHOLDER_AFTER if widened else timedelta(0)))


def _in_window(game, stubs, aliases, widened):
    lo, hi = time_window(game, widened)
    out = []
    for stub in stubs:
        if not (lo <= stub.commence_utc <= hi):
            continue
        mapping = _team_mapping(game, stub, aliases)
        if mapping:
            out.append((stub, mapping))
    return out


def searched_window(game, stubs, aliases):
    """The window candidates() searched last: the widened one only for a placeholder event whose normal
    window found nothing."""
    return time_window(game, game.placeholder and not _in_window(game, stubs, aliases, False))


def candidates(game, stubs, aliases):
    """Reference stubs that satisfy the time and name rules for one game."""
    out = _in_window(game, stubs, aliases, False)
    if not out and game.placeholder:
        out = _in_window(game, stubs, aliases, True)
    return out


def aliases_for(game, aliases_by_tag):
    aliases = {}
    for tag in game.sport_tags:
        aliases.update(aliases_by_tag.get(tag, {}))
    return aliases


def match(games, stubs_by_tag, aliases_by_tag):
    """games: list of (KalshiGame, sport label). stubs_by_tag: {kalshi_tag: [RefEventStub]}.
    aliases_by_tag: {kalshi_tag: {kalshi_name: reference_name}}.

    Free event listings do not say whether a draw is priced, so the outcome-count
    rule is enforced when prices arrive (check_outcomes), never assumed here.
    """
    result = MatchResult()
    per_game = {}
    for game, sport in games:
        pool, aliases = [], {}
        for tag in game.sport_tags:
            pool.extend(stubs_by_tag.get(tag, []))
            aliases.update(aliases_by_tag.get(tag, {}))
        if not pool:
            continue  # sport not polled: not an UNMATCHED case
        cands = candidates(game, pool, aliases)
        per_game[game.event_ticker] = (game, sport, cands, pool)

    claims = {}
    for ticker, (game, sport, cands, pool) in per_game.items():
        for stub, _ in cands:
            claims.setdefault(stub.source_event_id, set()).add(ticker)
    result.claimed = set(claims)

    for ticker, (game, sport, cands, pool) in per_game.items():
        if not cands:
            lo, hi = searched_window(game, pool, aliases_for(game, aliases_by_tag))
            near = [s for s in pool if lo <= s.commence_utc <= hi]
            reason = "name_mismatch" if near else "no_candidate"
            detail = f"teams={sorted(game.teams)} in_window={len(near)}" + (" placeholder=1" if game.placeholder else "")
            result.unmatched.append({"event_ticker": ticker, "sport": sport, "reason": reason,
                                     "detail": detail})
            continue
        if len(cands) > 1:
            ids = sorted(s.source_event_id for s, _ in cands)
            result.unmatched.append({"event_ticker": ticker, "sport": sport, "reason": "ambiguous",
                                     "detail": f"{len(cands)} reference events: {ids}"})
            continue
        stub, mapping = cands[0]
        if len(claims[stub.source_event_id]) > 1:
            result.unmatched.append({"event_ticker": ticker, "sport": sport, "reason": "ambiguous",
                                     "detail": f"reference {stub.source_event_id} also fits "
                                               f"{sorted(claims[stub.source_event_id] - {ticker})}"})
            continue
        for team, market_ticker in game.teams.items():
            result.matches.append({"market_ticker": market_ticker, "event_ticker": ticker,
                                   "source_event_id": stub.source_event_id,
                                   "sport_key": stub.sport_key, "ref_outcome": mapping[team]})
        if game.tie_market:
            result.matches.append({"market_ticker": game.tie_market, "event_ticker": ticker,
                                   "source_event_id": stub.source_event_id,
                                   "sport_key": stub.sport_key, "ref_outcome": DRAW})
    return result


def check_outcomes(kalshi_outcomes, ref_outcomes):
    """Outcome-count rule: the Kalshi outcome set must map onto the priced set exactly.

    kalshi_outcomes: the ref_outcome names this event's markets map to.
    ref_outcomes: names priced by the reference. Returns None if fine, else a reason.
    """
    if set(kalshi_outcomes) != set(ref_outcomes):
        return (f"outcome_count: kalshi maps to {sorted(kalshi_outcomes)}, "
                f"reference priced {sorted(ref_outcomes)}")
    return None
