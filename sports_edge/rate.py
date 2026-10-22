"""Rate a Kalshi slip (docs/specs/rate_slip_spec.md). Display only.

python -m sports_edge.rate < slip.txt      (or: python -m sports_edge.rate slip.txt)

Reads the plain-text slip, pairs its games with Kalshi and the sharp book
only through existing matches, and prints each leg's fair chance and grade,
the payout's markup, and the combo's fair line when it can be priced. It
writes nothing but the odds call log (one pull per sport, within the daily
credit budget, never stored as a reference read).
"""
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from . import checks, config, fees
from .matching import normalize
from .odds import DevigRejected, devig_proportional
from .odds_step import build_odds_source, can_spend
from .store import Store, from_db_ts, to_db_ts
from .timeutil import utcnow

SOURCE = "the-odds-api"
FRESH = timedelta(minutes=15)
PAIR_WINDOW = timedelta(hours=36)
STAKE = Decimal("10")
NO_CREDITS = "Can't rate: no odds credits left today"

LEG = re.compile(r"(?P<text>.+?)\s+(?P<pct>\d{1,2}(?:\.\d+)?)%")
GAME = re.compile(r"(?P<a>.+?) vs (?P<b>.+)")
COUNT = re.compile(r"(?P<n>\d+) MARKET COMBO")
PAYOUT = re.compile(r"\$(?P<stake>[\d,]+(?:\.\d{1,2})?) pays \$(?P<payout>[\d,]+(?:\.\d{1,2})?)")
MONEYLINE = re.compile(r"(?P<team>.+) to win")
TEAM = re.compile(r"(?P<code>[A-Z]{2,4}) (?P<name>.+)")


class SlipError(Exception):
    pass


@dataclass
class Leg:
    text: str
    pct: str
    game: int
    team: str = None              # the team a moneyline leg backs; None otherwise

    @property
    def price(self):
        return Decimal(self.pct) / 100


@dataclass
class Slip:
    league: str = None
    games: list = field(default_factory=list)        # [(team a, team b)]
    legs: list = field(default_factory=list)
    count: int = None
    stake: str = None
    payout: str = None


# Reading the slip ------------------------------------------------------------------

def parse(text):
    """The slip, or SlipError naming the first line that fits no form. Nothing is guessed."""
    slip, lines = Slip(), [(i, ln.strip()) for i, ln in enumerate(text.splitlines(), 1) if ln.strip()]

    def bad(i, ln):
        return SlipError(f"line {i}: {ln}")

    for i, ln in lines:
        if slip.payout is not None:
            raise bad(i, ln)
        if slip.count is not None:
            m = PAYOUT.fullmatch(ln)
            if not m:
                raise bad(i, ln)
            slip.stake, slip.payout = m["stake"], m["payout"]
            continue
        m = re.fullmatch(COUNT.pattern + r"\s+" + PAYOUT.pattern, ln) or COUNT.fullmatch(ln)
        if m:
            slip.count = int(m["n"])
            if "payout" in m.groupdict() and m["payout"]:
                slip.stake, slip.payout = m["stake"], m["payout"]
            continue
        m = LEG.fullmatch(ln)
        if m:
            if not slip.games or not (1 <= Decimal(m["pct"]) <= 99):
                raise bad(i, ln)
            leg = Leg(m["text"].strip(), m["pct"], len(slip.games) - 1)
            ml = MONEYLINE.fullmatch(leg.text)
            if ml:
                if ml["team"] not in slip.games[-1]:
                    raise bad(i, ln)
                leg.team = ml["team"]
            slip.legs.append(leg)
            continue
        m = GAME.fullmatch(ln)
        if m and "%" not in ln and "$" not in ln:
            slip.games.append((m["a"].strip(), m["b"].strip()))
            continue
        if not slip.games and slip.league is None and not any(c in ln for c in "%$"):
            slip.league = ln
            continue
        raise bad(i, ln)
    if slip.count is None or slip.payout is None or slip.count != len(slip.legs) or not slip.legs:
        raise SlipError("the combo line must give the number of legs and the payout, "
                        f"and the number must match the {len(slip.legs)} legs read")
    return slip


# Pairing with Kalshi and the sharp book -----------------------------------------------

def team_fits(team, ticker, ref_outcome):
    """Code equals the ticker's team suffix, or the name equals the end of the matched sharp name."""
    m = TEAM.fullmatch(team)
    if not m:
        return False
    if ticker.rsplit("-", 1)[-1] == m["code"]:
        return True
    name, ref = normalize(m["name"]), normalize(ref_outcome or "")
    return bool(ref) and (ref == name or ref.endswith(" " + name))


def learned_series(store, sport_key):
    """Kalshi series that have games matched to this odds sport key."""
    return [s for (s,) in store.con.execute("""
        SELECT DISTINCT m.series_ticker FROM odds_matches o JOIN markets m ON m.ticker = o.market_ticker
        WHERE o.source = ? AND o.sport_key = ? ORDER BY 1""", [SOURCE, sport_key]).fetchall()]


def pair_games(store, slip, series, now):
    """{game index: {team: (market ticker, source_event_id or None, ref_outcome or None)}},
    only for games that pair with exactly one Kalshi game event of the given series."""
    rows = store.con.execute("""
        SELECT m.event_ticker, m.ticker, m.yes_sub_title, m.status, o.source_event_id, o.ref_outcome
        FROM markets m
        LEFT JOIN odds_matches o ON o.market_ticker = m.ticker AND o.source = ?
        WHERE m.competition_scope = 'Game' AND m.series_ticker IN (SELECT unnest(?))
          AND m.event_ticker IN (SELECT event_ticker FROM markets WHERE resolve_by > ? AND resolve_by <= ?)
        ORDER BY m.ticker""", [SOURCE, series, to_db_ts(now), to_db_ts(now + PAIR_WINDOW)]).fetchall()
    events = {}
    for ev, ticker, title, status, eid, outcome in rows:
        events.setdefault(ev, []).append((ticker, title, status, eid, outcome))
    out = {}
    for g, (a, b) in enumerate(slip.games):
        found = []
        for ev, markets in events.items():
            teams = [m for m in markets if m[1] != "Tie"]
            if len(teams) != 2 or len(markets) != 2 or any(m[2] != "active" for m in markets):
                continue
            for x, y in ((teams[0], teams[1]), (teams[1], teams[0])):
                if team_fits(a, x[0], x[4]) and team_fits(b, y[0], y[4]) and \
                        not (team_fits(a, y[0], y[4]) and team_fits(b, x[0], x[4])):
                    found.append({a: (x[0], x[3], x[4]), b: (y[0], y[3], y[4])})
        if len(found) == 1:
            out[g] = found[0]
    return out


# Fair chances ---------------------------------------------------------------------------

def stored_fair(store, eid, outcome, now):
    found = store.con.execute("""
        SELECT fetched_utc, fair_prob FROM reference_odds WHERE source = ? AND source_event_id = ? AND outcome = ?
        ORDER BY fetched_utc DESC LIMIT 1""", [SOURCE, eid, outcome]).fetchone()
    if found and now - from_db_ts(found[0]) <= FRESH:
        return Decimal(str(found[1])).quantize(Decimal("0.000001"))
    return None


def pulled_fairs(source, sport_key, event_ids, now):
    """{(source_event_id, outcome): fair} from one pull. Never stored as a reference read."""
    out = {}
    for ref in source.fetch_moneylines(sport_key, sorted(event_ids), now):
        try:
            fair, _ = devig_proportional(ref.outcomes)
        except DevigRejected:
            continue
        for name, f in fair.items():
            out[(ref.source_event_id, name)] = Decimal(str(f)).quantize(Decimal("0.000001"))
    return out


def leg_fee(store, ticker, price):
    """Taker fee per contract for buying this leg alone with $10, or a reason it is unknown."""
    if fees.table_state() != "ok":
        return None, "fee not confirmed (no fee table)" if fees.table_state() == "missing" else \
            "fee not confirmed (fee table invalid)"
    series, fee_type, mult, override = store.con.execute(
        "SELECT series_ticker, fee_type, fee_multiplier, fee_override FROM markets WHERE ticker = ?",
        [ticker]).fetchone()
    table = fees.load_table()
    try:
        m_taker, _ = fees.resolve_multipliers(table, series, fee_type, mult, bool(override))
    except fees.FeeScheduleUnknown:
        return None, "fee not confirmed"
    return fees.fee_per_contract(price, int(STAKE // price), m_taker, "taker", fees.table_precision(table)), None


def grade(d):
    """A >= 5c under fair after fee, B 3-5c, C 0-3c, D over fair by less than 3c, F 3c or more over."""
    if d >= Decimal("0.05"):
        return "A"
    if d >= Decimal("0.03"):
        return "B"
    if d >= 0:
        return "C"
    if d > Decimal("-0.03"):
        return "D"
    return "F"


# The rating -------------------------------------------------------------------------------

def pct(x, places=1):
    return f"{Decimal(x) * 100:.{places}f}%"


def markup_line(slip):
    payout_chance = Decimal(slip.stake.replace(",", "")) / Decimal(slip.payout.replace(",", ""))
    legs_chance = math.prod(leg.price for leg in slip.legs)
    more = payout_chance / legs_chance - 1
    return (f"Markup: the payout's chance is {pct(payout_chance, 2)}; the legs' own prices multiply to "
            f"{pct(legs_chance, 2)}, so you pay about {abs(more) * 100:.0f}% {'more' if more >= 0 else 'less'} "
            f"than the legs. You'd win about 1 in {round(1 / legs_chance)}.")


def rate_lines(store, slip, now, source_factory=build_odds_source):
    sport_key = {label: key for key, label in checks.slate_sports().items()}.get(slip.league)
    series = learned_series(store, sport_key) if sport_key else []
    paired = pair_games(store, slip, series, now) if series else {}
    starts = checks.latest_starts(store, SOURCE)
    fair, reason, needs_pull = {}, {}, set()
    for n, leg in enumerate(slip.legs, 1):
        if leg.team is None:
            reason[n] = "no sharp price (not a moneyline)"
        elif not config.odds_api_key():
            reason[n] = "no sharp price (odds not set up)"
        elif not sport_key:
            reason[n] = f"not paired (league {slip.league or 'missing'} not followed)"
        elif not series:
            reason[n] = f"not paired (no {slip.league} games paired with the sharp book yet)"
        elif leg.game not in paired:
            reason[n] = "not paired (no single Kalshi game fits)"
        else:
            ticker, eid, outcome = paired[leg.game][leg.team]
            if eid is None:
                reason[n] = "not paired with the sharp book"
            elif starts.get(eid) and starts[eid] <= now:
                reason[n] = "game started — not rated"
            else:
                fair[n] = stored_fair(store, eid, outcome, now)
                if fair[n] is None:
                    needs_pull.add(n)
    no_credits = False
    if needs_pull:
        source = source_factory(store)
        if source is None:
            for n in needs_pull:
                reason[n] = "no sharp price (odds not set up)"
        elif not can_spend(store, now):
            no_credits = True
            for n in needs_pull:
                reason[n] = "no sharp price (odds over 15 min old, no credits left)"
        else:
            ids = {paired[slip.legs[n - 1].game][slip.legs[n - 1].team][1] for n in needs_pull}
            got = pulled_fairs(source, sport_key, ids, now)
            for n in needs_pull:
                leg = slip.legs[n - 1]
                _, eid, outcome = paired[leg.game][leg.team]
                fair[n] = got.get((eid, outcome))
                if fair[n] is None:
                    reason[n] = "no sharp price (none available now)"
    lines = [f"Slip: {len(slip.legs)} legs · ${slip.stake} pays ${slip.payout}"]
    for n, leg in enumerate(slip.legs, 1):
        head = f"{n}. {leg.text} · {leg.pct}%"
        if fair.get(n) is None:
            lines.append(f"{head} · {reason[n]}")
            continue
        fee, why = leg_fee(store, paired[leg.game][leg.team][0], leg.price)
        if fee is None:
            lines.append(f"{head} · fair {pct(fair[n])} · no grade ({why})")
            continue
        d = fair[n] - leg.price - fee
        lines.append(f"{head} · fair {pct(fair[n])} · {abs(d) * 100:.1f}¢ {'under' if d >= 0 else 'over'} "
                     f"fair after fee · {grade(d)}")
    lines.append(markup_line(slip))
    lines.append(combo_line(slip, fair))
    if no_credits:
        lines.append(NO_CREDITS)
    return lines


def combo_line(slip, fair):
    missing = [n for n in range(1, len(slip.legs) + 1) if fair.get(n) is None]
    shared = {}
    for leg in slip.legs:
        shared[leg.game] = shared.get(leg.game, 0) + 1
    same = [f"{' vs '.join(slip.games[g])} ({k})" for g, k in shared.items() if k > 1]
    if missing or same:
        why = ([f"{len(missing)} leg{'s' if len(missing) != 1 else ''} without a sharp price"] if missing else []) \
            + ([f"same-game legs: {', '.join(same)}"] if same else [])
        return "Combo: not fair-priced — " + "; ".join(why) + "."
    joint = math.prod(fair.values())
    payout_chance = Decimal(slip.stake.replace(",", "")) / Decimal(slip.payout.replace(",", ""))
    return (f"Combo: the legs' fair chances multiply to {pct(joint, 2)} against the payout's "
            f"{pct(payout_chance, 2)}, before fee (combo fee unconfirmed, so no grade).")


def rate(text, now=None, source_factory=build_odds_source, out=print):
    slip = parse(text)
    now = now or utcnow()
    with Store.open(lock_wait_s=120) as store:
        lines = rate_lines(store, slip, now, source_factory)
    for line in lines:
        out(line)
    return lines


def main(argv):
    text = open(argv[0], encoding="utf-8").read() if argv else sys.stdin.read()
    try:
        rate(text)
    except SlipError as exc:
        print(f"Can't read the slip: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
