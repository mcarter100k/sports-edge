"""Picks by number, and bet logging from replies
(docs/specs/slate_alerts_spec.md, "Bet logging" and the owner rulings before merge).

Replies are lines in <sheet folder>/replies.txt, appended by the owner's Apple
Shortcuts automation. The file is read, never written. Each reply line is
logged once to data/bet_log.csv through ledger.append_rows.

A pick is one result: two-team games pair "Yes on A" with "No on B". The caller
supplies outcome_of(row) for that; the default treats every market + side as
its own result.
"""
import hashlib
import re
from datetime import timedelta
from decimal import Decimal

from . import config, ledger
from .timeutil import display_date, iso_z, pacific_midnight, parse_utc

BET_LOG_HEADER = ["logged_utc", "message_guid", "message_utc", "pick_date", "pick_no", "row_id",
                  "market_ticker", "event_ticker", "side", "action", "price", "contracts", "raw_text"]
STAKE = Decimal("10")
REPLIES_FILE = "replies.txt"
_NUMBERS = re.compile(r"(?:\s*(?:at\s+)?#?(?:\d*\.\d+|\d+)\s*(?:c|¢)?)*\s*")
_NUMBER = re.compile(r"(#)?(\d*\.\d+|\d+)\s*(c|¢)?")


class BetLogUnreadable(Exception):
    pass


# Picks by number ---------------------------------------------------------------------

def bet_key(r):
    return r["market_ticker"], r["side"], r["rule_id"]


def first_fire_ids(rows):
    """{bet key: row_id of its earliest row}: the grader's bet unit."""
    first = {}
    for r in sorted(rows, key=lambda r: (r["snapshot_utc"], r["row_id"])):
        first.setdefault(bet_key(r), r["row_id"])
    return first


def cheaper(rows):
    """The row shown for a result: lowest price + fee per contract, then the larger gap."""
    return min(rows, key=lambda r: (Decimal(r["price_paid"] or 0) + Decimal(r["fee_per_contract"] or 0),
                                    -Decimal(r["edge"] or 0), r["row_id"]))


def results(rows, outcome_of=bet_key):
    """{result key: [first-fire rows]}."""
    by_id = {r["row_id"]: r for r in rows}
    out = {}
    for rid in first_fire_ids(rows).values():
        out.setdefault(outcome_of(by_id[rid]), []).append(by_id[rid])
    return out


def day_picks(rows, day, outcome_of=bet_key):
    """{result key: (pick number, shown first-fire row)} for results first logged on Pacific day `day`."""
    today = []
    for key, rs in results(rows, outcome_of).items():
        first = min(rs, key=lambda r: (r["logged_utc"], r["market_ticker"], r["side"]))
        if display_date(parse_utc(first["logged_utc"])) == day:
            today.append(((first["logged_utc"], first["market_ticker"], first["side"]), key, rs))
    today.sort(key=lambda t: t[0])
    return {key: (i, cheaper(rs)) for i, (_, key, rs) in enumerate(today, 1)}


def number_of(rows, key, outcome_of=bet_key):
    """(Pacific day, pick number) of one result, or (None, None)."""
    rs = results(rows, outcome_of).get(key)
    if not rs:
        return None, None
    day = display_date(parse_utc(min(r["logged_utc"] for r in rs)))
    return day, day_picks(rows, day, outcome_of)[key][0]


def shown_in_run(rows, run_id, outcome_of=bet_key):
    """{result key: the row a run's message shows for it}."""
    groups = {}
    for r in rows:
        if r["run_id"] == run_id:
            groups.setdefault(outcome_of(r), []).append(r)
    return {k: cheaper(rs) for k, rs in groups.items()}


# Reply grammar -------------------------------------------------------------------------

def _price(token, cents_mark):
    """1 or more is cents (42, 42.3); below 1 is dollars (.42, 0.42); c or ¢ is always cents."""
    value = Decimal(token)
    price = value / 100 if (cents_mark or value >= 1) else value
    return price if Decimal("0.01") <= price <= Decimal("0.99") else None


def parse_reply(text):
    """('took', pick or None, price) | ('skip', pick or None, None) | None if not a reply."""
    t = (text or "").strip().lower()
    m = re.fullmatch(r"(took|skip)\b(.*)", t, re.DOTALL)
    if not m or not _NUMBERS.fullmatch(m.group(2)):
        return None
    action, nums = m.group(1), _NUMBER.findall(m.group(2))
    if action == "skip":
        if not nums:
            return "skip", None, None
        if len(nums) == 1 and "." not in nums[0][1] and not nums[0][2]:
            return "skip", int(nums[0][1]), None
        return None
    if len(nums) == 1 and not nums[0][0]:
        price = _price(nums[0][1], nums[0][2])
        return ("took", None, price) if price else None
    if len(nums) == 2 and "." not in nums[0][1] and not nums[0][2] and not nums[1][0]:
        price = _price(nums[1][1], nums[1][2])
        return ("took", int(nums[0][1]), price) if price else None
    return None


# replies.txt ---------------------------------------------------------------------------

def replies_path():
    return config.sheet_dir() / REPLIES_FILE


def read_replies(path=None):
    """[(id, time or None, text)] for every non-blank line, in file order. Raises BetLogUnreadable.

    A line is '<ISO 8601 time with offset> <reply>' or just '<reply>'. Its id is the sha1
    of the line and its occurrence number, so identical lines are told apart.
    """
    path = path or replies_path()
    if not path.exists():
        if path.with_name(f".{path.name}.icloud").exists():
            raise BetLogUnreadable(f"{path.name} is in iCloud but not downloaded to this Mac yet")
        raise BetLogUnreadable(f"no {path.name} yet in {path.parent} (set up the Shortcut in the README)")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise BetLogUnreadable(f"{path}: {exc}") from exc
    seen, out = {}, []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        when, text = None, line
        head, _, rest = line.partition(" ")
        try:
            when, text = parse_utc(head), rest.strip()
        except ValueError:
            pass
        seen[line] = seen.get(line, 0) + 1
        out.append((hashlib.sha1(f"{line}|{seen[line]}".encode()).hexdigest(), when, text))
    return out


def ingest_replies(now, ledger_rows=None, outcome_of=bet_key):
    """Log new reply lines. Returns {'readable', 'error', 'logged', 'unclear', 'unclear_lines'}
    (unclear_lines: [(id, text)] of new replies that named no pick).

    Timestamped lines older than the previous Pacific midnight are ignored; a line
    without a timestamp is timed when it is first read.
    """
    status = {"readable": False, "error": None, "logged": 0, "unclear": 0, "unclear_lines": []}
    try:
        lines = read_replies()
    except BetLogUnreadable as exc:
        status["error"] = str(exc)
        return status
    status["readable"] = True
    since = pacific_midnight(now) - timedelta(days=1)
    path = config.bet_log_path()
    seen = {r["message_guid"] for r in ledger.read_rows(path)}
    rows = ledger_rows if ledger_rows is not None else ledger.read_rows(config.shadow_ledger_path())
    new = []
    for guid, when, text in lines:
        parsed = parse_reply(text)
        if guid in seen or parsed is None or (when is not None and when < since):
            continue
        new.append(log_row(guid, when or now, text, parsed, rows, now, outcome_of))
    ledger.append_rows(path, BET_LOG_HEADER, new)
    status["logged"] = len(new)
    status["unclear_lines"] = [(r["message_guid"], r["raw_text"]) for r in new if r["action"] == "unclear"]
    status["unclear"] = len(status["unclear_lines"])
    return status


def log_row(guid, when, text, parsed, rows, now, outcome_of=bet_key):
    """The bet_log row for one reply: which pick it names, or 'unclear'.

    Numbered: the most recently messaged result with that number, numbered on the reply's
    Pacific day or the day before. Unnumbered: the latest check run that wrote rows, only if
    it showed exactly one result. Either way the row is the one that check showed.
    """
    action, number, price = parsed
    before = [r for r in rows if parse_utc(r["logged_utc"]) <= when]
    shown, day, no = None, display_date(when), ""
    if number is not None:
        found = []
        for d in (display_date(when), display_date(when - timedelta(days=1))):
            for key, (n, _) in day_picks(before, d, outcome_of).items():
                if n == number:
                    latest = max((r for r in before if outcome_of(r) == key), key=lambda r: r["logged_utc"])
                    found.append((latest["logged_utc"], d, n,
                                  shown_in_run(before, latest["run_id"], outcome_of)[key]))
        if found:
            _, day, no, shown = max(found, key=lambda f: f[0])
    elif before:
        run = max(before, key=lambda r: r["logged_utc"])["run_id"]
        in_run = shown_in_run(before, run, outcome_of)
        if len(in_run) == 1:
            ((key, shown),) = in_run.items()
            day, no = number_of(before, key, outcome_of)
    pick = None
    if shown is not None:
        pick = {r["row_id"]: r for r in before}[first_fire_ids(before)[bet_key(shown)]]
    else:
        action = "unclear"
    took = price is not None and action == "took"
    return {"logged_utc": iso_z(now), "message_guid": guid, "message_utc": iso_z(when), "pick_date": day,
            "pick_no": no, "row_id": pick["row_id"] if pick else "",
            "market_ticker": pick["market_ticker"] if pick else "",
            "event_ticker": pick["event_ticker"] if pick else "", "side": pick["side"] if pick else "",
            "action": action, "price": price if took else "",
            "contracts": int(STAKE // price) if took else "", "raw_text": (text or "")[:200]}
