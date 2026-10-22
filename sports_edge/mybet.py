"""Log a bet the owner placed (docs/specs/my_bets_spec.md). Record-only.

python -m sports_edge.mybet < bet.txt      (or: python -m sports_edge.mybet bet.txt)

The input is a slip in the rate format plus one line "placed <cost> pays <payout>".
Each leg's Kalshi market is identified only by deterministic rules; anything
else is "not identified" and the bet then won't settle automatically. Never
written to the shadow ledger, the pick bet log or the stage.
"""
import hashlib
import math
import re
import sys
from decimal import Decimal

from . import checks, config, ledger, rate
from .store import Store
from .timeutil import display, iso_z, utcnow

MY_BETS_HEADER = ["bet_id", "logged_utc", "logged_pt", "league", "n_legs", "legs", "leg_tickers", "cost",
                  "payout", "implied_chance", "legs_product", "markup"]
PLACED = re.compile(r"placed\s+\$?(?P<cost>[\d,]+(?:\.\d{1,2})?)\s+pays\s+\$?(?P<payout>[\d,]+(?:\.\d{1,2})?)",
                    re.IGNORECASE)
PLAYER = re.compile(r"(?P<player>.+): (?P<line>\d+)\+ (?P<stat>.+)")


def split_placed(text):
    """(slip text without the placed line, cost, payout), or SlipError."""
    lines = text.splitlines()
    found = [i for i, ln in enumerate(lines) if PLACED.fullmatch(ln.strip())]
    if len(found) != 1:
        raise rate.SlipError("one line 'placed <cost> pays <payout>' is required "
                             f"(found {len(found)})")
    m = PLACED.fullmatch(lines[found[0]].strip())
    rest = "\n".join(ln for i, ln in enumerate(lines) if i != found[0])
    money = lambda s: Decimal(s.replace(",", ""))
    if money(m["cost"]) <= 0 or money(m["payout"]) <= money(m["cost"]):
        raise rate.SlipError(f"placed line: {lines[found[0]].strip()}")
    return rest, money(m["cost"]), money(m["payout"])


def identify(store, slip, now):
    """[ticker or None] per leg: moneyline legs by the game pairing, player legs by exact title."""
    sport_key = {label: key for key, label in checks.slate_sports().items()}.get(slip.league)
    series = rate.learned_series(store, sport_key) if sport_key else []
    paired = rate.pair_games(store, slip, series, now) if series else {}
    out = []
    for leg in slip.legs:
        game = paired.get(leg.game)
        if game is None:
            out.append(None)
            continue
        if leg.team:
            out.append(game[leg.team][0])
            continue
        m = PLAYER.fullmatch(leg.text)
        if not m:
            out.append(None)
            continue
        key = next(iter(game.values()))[0].rsplit("-", 1)[0].split("-", 1)[1]     # KXNBAGAME-26OCT23HOUSAS
        rows = store.con.execute("""
            SELECT ticker FROM markets WHERE event_ticker LIKE ? AND lower(event_title) LIKE ? AND yes_sub_title = ?""",
            [f"%-{key}", f"%: {m['stat'].strip().lower()}", f"{m['player']}: {m['line']}+"]).fetchall()
        out.append(rows[0][0] if len(rows) == 1 else None)
    return out


def log_bet(text, now=None, out=print):
    rest, cost, payout = split_placed(text)
    slip = rate.parse(rest)
    now = now or utcnow()
    with Store.open(lock_wait_s=120) as store:
        tickers = identify(store, slip, now)
    implied = cost / payout
    product = math.prod(leg.price for leg in slip.legs)
    more = implied / product - 1
    bet_id = hashlib.sha1(f"{iso_z(now)}|{text}".encode()).hexdigest()[:12]
    ledger.append_rows(config.my_bets_path(), MY_BETS_HEADER, [{
        "bet_id": bet_id, "logged_utc": iso_z(now), "logged_pt": display(now), "league": slip.league or "",
        "n_legs": len(slip.legs), "legs": " | ".join(f"{leg.text} @ {leg.pct}%" for leg in slip.legs),
        "leg_tickers": " | ".join(t or "" for t in tickers), "cost": cost, "payout": payout,
        "implied_chance": implied.quantize(Decimal("0.000001")), "legs_product": product.quantize(Decimal("0.000001")),
        "markup": more.quantize(Decimal("0.0001"))}])
    missing = [str(n) for n, t in enumerate(tickers, 1) if t is None]
    out(f"Logged your bet {bet_id}: {len(slip.legs)} legs · ${cost:,.2f} pays ${payout:,.2f} · "
        f"payout's chance {implied * 100:.2f}% · legs multiply to {product * 100:.2f}% · "
        f"you pay about {abs(more) * 100:.0f}% {'more' if more >= 0 else 'less'} than the legs")
    if missing:
        out(f"Identified {len(slip.legs) - len(missing)} of {len(slip.legs)} legs; leg{'s' if len(missing) > 1 else ''} "
            f"{', '.join(missing)} not found, so this bet won't settle automatically.")
    else:
        out(f"Identified all {len(slip.legs)} legs; it will settle from Kalshi results.")
    return bet_id


def main(argv):
    text = open(argv[0], encoding="utf-8").read() if argv else sys.stdin.read()
    try:
        log_bet(text)
    except rate.SlipError as exc:
        print(f"Can't read the bet: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
