"""Picks by number, and bet logging from iMessage replies
(docs/specs/slate_alerts_spec.md, "Bet logging by iMessage reply").

Replies are read from ~/Library/Messages/chat.db, opened read-only. Each
reply message is logged once to data/bet_log.csv through ledger.append_rows.
"""
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from . import config, ledger
from .timeutil import display_date, iso_z, pacific_midnight, parse_utc

BET_LOG_HEADER = ["logged_utc", "message_guid", "message_utc", "pick_date", "pick_no", "row_id",
                  "market_ticker", "event_ticker", "side", "action", "price", "contracts", "raw_text"]
STAKE = Decimal("10")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
NS_DATES_ABOVE = 10 ** 12          # chat.db dates above this are nanoseconds, below it seconds
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


def day_picks(rows, day):
    """{bet key: (pick number, first-fire row)} for bets first logged on Pacific day `day` (YYYY-MM-DD)."""
    first = first_fire_ids(rows)
    by_id = {r["row_id"]: r for r in rows}
    today = [by_id[rid] for rid in first.values()
             if display_date(parse_utc(by_id[rid]["logged_utc"])) == day]
    today.sort(key=lambda r: (r["logged_utc"], r["market_ticker"], r["side"]))
    return {bet_key(r): (i, r) for i, r in enumerate(today, 1)}


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


# chat.db -------------------------------------------------------------------------------

def decode_attributed_body(blob):
    """The plain text inside a typedstream attributedBody, or None."""
    if not blob or b"NSString" not in blob:
        return None
    rest = blob.split(b"NSString", 1)[1][5:]
    if not rest:
        return None
    if rest[0] == 0x81:
        length, start = int.from_bytes(rest[1:3], "little"), 3
    elif rest[0] == 0x82:
        length, start = int.from_bytes(rest[1:5], "little"), 5
    else:
        length, start = rest[0], 1
    return rest[start:start + length].decode("utf-8", errors="replace")


def _apple_time(value):
    seconds = value / 1e9 if value > NS_DATES_ABOVE else value
    return APPLE_EPOCH + timedelta(seconds=seconds)


def _digits(handle):
    return re.sub(r"\D", "", handle)[-10:]


def read_messages(handle, since, path=None):
    """[(guid, utc, text)] in the chat with `handle` since `since`, oldest first."""
    path = path or config.messages_db()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            chats = con.execute("SELECT ROWID, chat_identifier FROM chat").fetchall()
            wanted = [rid for rid, ident in chats if ident == handle or
                      ("@" not in handle and _digits(ident or "") and _digits(ident or "") == _digits(handle))]
            if not wanted:
                return []
            since_ns = int((since - APPLE_EPOCH).total_seconds() * 1e9)
            marks = ",".join("?" * len(wanted))
            rows = con.execute(f"""
                SELECT m.guid, m.date, m.text, m.attributedBody FROM message m
                JOIN chat_message_join j ON j.message_id = m.ROWID
                WHERE j.chat_id IN ({marks})
                  AND ((m.date > ? AND m.date >= ?) OR (m.date <= ? AND m.date >= ?))
                ORDER BY m.date""", [*wanted, NS_DATES_ABOVE, since_ns, NS_DATES_ABOVE,
                                     int(since_ns / 1e9)]).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise BetLogUnreadable(f"{path}: {exc}") from exc
    return [(guid, _apple_time(date), text if text else decode_attributed_body(body))
            for guid, date, text, body in rows]


def ingest_replies(now, ledger_rows=None):
    """Log new replies. Returns {'readable', 'error', 'logged', 'unclear'}."""
    status = {"readable": False, "error": None, "logged": 0, "unclear": 0}
    handle = config.imessage_to()
    if not handle:
        status["error"] = "IMESSAGE_TO not set"
        return status
    try:
        messages = read_messages(handle, pacific_midnight(now) - timedelta(days=1))
    except BetLogUnreadable as exc:
        status["error"] = str(exc)
        return status
    status["readable"] = True
    path = config.bet_log_path()
    seen = {r["message_guid"] for r in ledger.read_rows(path)}
    rows = ledger_rows if ledger_rows is not None else ledger.read_rows(config.shadow_ledger_path())
    new = []
    for guid, when, text in messages:
        parsed = parse_reply(text)
        if guid in seen or parsed is None:
            continue
        new.append(log_row(guid, when, text, parsed, rows, now))
    ledger.append_rows(path, BET_LOG_HEADER, new)
    status["logged"] = len(new)
    status["unclear"] = sum(1 for r in new if r["action"] == "unclear")
    return status


def log_row(guid, when, text, parsed, rows, now):
    """The bet_log row for one reply: which pick it names, or 'unclear'.

    Numbered: the most recently messaged pick with that number, numbered on the reply's
    Pacific day or the day before. Unnumbered: the latest check run that wrote rows,
    only if it wrote exactly one pick (repeats included).
    """
    action, number, price = parsed
    before = [r for r in rows if parse_utc(r["logged_utc"]) <= when]
    by_id = {r["row_id"]: r for r in before}
    pick, day, no = None, display_date(when), ""
    if number is not None:
        found = []
        for d in (display_date(when), display_date(when - timedelta(days=1))):
            for key, (n, first) in day_picks(before, d).items():
                if n == number:
                    latest = max(r["logged_utc"] for r in before if bet_key(r) == key)
                    found.append((latest, d, n, first))
        if found:
            _, day, no, pick = max(found, key=lambda f: f[0])
    elif before:
        latest_run = max(before, key=lambda r: r["logged_utc"])["run_id"]
        keys = {bet_key(r) for r in before if r["run_id"] == latest_run}
        if len(keys) == 1:
            pick = by_id[first_fire_ids(before)[keys.pop()]]
            day = display_date(parse_utc(pick["logged_utc"]))
            no = day_picks(before, day)[bet_key(pick)][0]
    if pick is None:
        action = "unclear"
    took = price is not None and action == "took"
    return {"logged_utc": iso_z(now), "message_guid": guid, "message_utc": iso_z(when), "pick_date": day,
            "pick_no": no, "row_id": pick["row_id"] if pick else "",
            "market_ticker": pick["market_ticker"] if pick else "",
            "event_ticker": pick["event_ticker"] if pick else "", "side": pick["side"] if pick else "",
            "action": action, "price": price if took else "",
            "contracts": int(STAKE // price) if took else "", "raw_text": (text or "")[:200]}
