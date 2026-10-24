"""Nightly grader (docs/specs/grader_spec.md).

python -m sports_edge.grader
"""
import math
import statistics
import sys
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from . import bets, checks, config, ledger
from .fees import fee_for_order
from .kalshi import KalshiClient
from .snapshot import new_run_id, record_failure, sweep_settlements
from .store import Store, from_db_ts, to_db_ts
from .timeutil import iso_z, parse_utc, utcnow

JOB = "grader"
INSUFFICIENT_N = 20
P_CLIP = 1e-6

CALIBRATION_HEADER = [
    "market_ticker", "event_ticker", "sport", "source_event_id", "y", "p_kalshi", "p_ref",
    "kalshi_snapshot_utc", "ref_fetched_utc", "commence_utc", "brier_kalshi", "brier_ref",
    "logloss_kalshi", "logloss_ref", "p_ref_power", "brier_ref_power", "logloss_ref_power", "graded_utc",
]
SHADOW_GRADE_HEADER = [
    "row_id", "market_ticker", "event_ticker", "side", "rule_id", "snapshot_utc", "result",
    "payout", "price_paid", "fee_per_contract", "pnl_per_contract",
    # record-only closing line value (docs/specs/grader_spec.md amendment)
    "closing_fair_prob", "closing_ref_fetched_utc", "closing_side_fair", "clv", "fair_move", "graded_utc",
]
# record-only markouts (docs/specs/grader_spec.md, "Markouts"); nothing but markout_report reads them
MARKOUT_HEADER = [
    "row_id", "market_ticker", "event_ticker", "side", "rule_id", "snapshot_utc", "start_utc", "price_paid",
    "entry_mid", "next_utc", "next_mid", "markout_next", "close_utc", "close_mid", "markout_close", "graded_utc",
]
NEXT_WINDOW = (timedelta(minutes=10), timedelta(minutes=20))


def brier(p, y):
    return (p - y) ** 2


def log_loss(p, y):
    p = min(max(p, P_CLIP), 1 - P_CLIP)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def calibrate(store, source, now):
    graded = {r["market_ticker"] for r in ledger.read_rows(config.calibration_grades_path())}
    candidates = store.con.execute("""
        SELECT o.market_ticker, o.event_ticker, o.source_event_id, o.ref_outcome, m.sport, s.result
        FROM odds_matches o
        JOIN settlements s ON s.ticker = o.market_ticker
        JOIN markets m ON m.ticker = o.market_ticker
        WHERE o.source = ? AND s.result IN ('yes', 'no')
          AND o.event_ticker NOT IN (SELECT event_ticker FROM match_conflicts)""",
                                    [source]).fetchall()
    rows, skipped = [], {}
    for ticker, event, eid, outcome, sport, result in candidates:
        if ticker in graded:
            continue
        commence = store.con.execute(
            "SELECT max(commence_utc) FROM reference_odds WHERE source = ? AND source_event_id = ?",
            [source, eid]).fetchone()[0]
        if commence is None:
            skipped["no_reference"] = skipped.get("no_reference", 0) + 1
            continue
        snap = store.con.execute("""
            SELECT ts_utc, yes_bid, yes_ask FROM price_snapshots
            WHERE ticker = ? AND ts_utc < ? AND yes_bid > 0 AND yes_ask < 1 AND yes_bid <= yes_ask
            ORDER BY ts_utc DESC LIMIT 1""", [ticker, commence]).fetchone()
        ref = store.con.execute("""
            SELECT fetched_utc, fair_prob, fair_prob_power FROM reference_odds
            WHERE source = ? AND source_event_id = ? AND outcome = ? AND fetched_utc < ?
            ORDER BY fetched_utc DESC LIMIT 1""", [source, eid, outcome, commence]).fetchone()
        if snap is None or ref is None:
            reason = "no_pre_game_kalshi_quote" if snap is None else "no_pre_game_reference"
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        y = 1 if result == "yes" else 0
        p_k = float((Decimal(snap[1]) + Decimal(snap[2])) / 2)
        p_r = float(ref[1])
        p_rp = float(ref[2]) if ref[2] is not None else None
        rows.append({
            "market_ticker": ticker, "event_ticker": event, "sport": sport, "source_event_id": eid,
            "y": y, "p_kalshi": f"{p_k:.6f}", "p_ref": f"{p_r:.6f}",
            "kalshi_snapshot_utc": iso_z(from_db_ts(snap[0])),
            "ref_fetched_utc": iso_z(from_db_ts(ref[0])), "commence_utc": iso_z(from_db_ts(commence)),
            "brier_kalshi": f"{brier(p_k, y):.6f}", "brier_ref": f"{brier(p_r, y):.6f}",
            "logloss_kalshi": f"{log_loss(p_k, y):.6f}", "logloss_ref": f"{log_loss(p_r, y):.6f}",
            "p_ref_power": f"{p_rp:.6f}" if p_rp is not None else "",
            "brier_ref_power": f"{brier(p_rp, y):.6f}" if p_rp is not None else "",
            "logloss_ref_power": f"{log_loss(p_rp, y):.6f}" if p_rp is not None else "",
            "graded_utc": iso_z(now)})
    ledger.append_rows(config.calibration_grades_path(), CALIBRATION_HEADER, rows)
    return len(rows), skipped, len(candidates)


def closing_line(store, r):
    """Record-only closing line for one shadow row: the last reference read strictly after both the
    row's snapshot and its own reference read, and before the start. Blank if the game was read only
    once. clv is after the fee: closing side fair - price paid - fee per contract."""
    blank = {"closing_fair_prob": "", "closing_ref_fetched_utc": "", "closing_side_fair": "",
             "clv": "", "fair_move": ""}
    if not r.get("ref_outcome") or not r.get("commence_utc"):
        return blank
    entry = max(t for t in (parse_utc(r["snapshot_utc"]), parse_utc(r.get("odds_fetched_utc"))) if t)
    found = store.con.execute("""
        SELECT fetched_utc, fair_prob FROM reference_odds
        WHERE source = ? AND source_event_id = ? AND outcome = ? AND fetched_utc > ? AND fetched_utc < ?
        ORDER BY fetched_utc DESC LIMIT 1""", [r["odds_source"], r["source_event_id"], r["ref_outcome"],
                                               to_db_ts(entry), to_db_ts(parse_utc(r["commence_utc"]))]).fetchone()
    if found is None:
        return blank
    closing = Decimal(str(found[1])).quantize(Decimal("0.000001"))
    side_close = closing if r["side"] == "YES" else Decimal(1) - closing
    entry_fair = Decimal(r["fair_prob"])
    side_entry = entry_fair if r["side"] == "YES" else Decimal(1) - entry_fair
    return {"closing_fair_prob": closing, "closing_ref_fetched_utc": iso_z(from_db_ts(found[0])),
            "closing_side_fair": side_close,
            "clv": side_close - Decimal(r["price_paid"]) - Decimal(r["fee_per_contract"]),
            "fair_move": side_close - side_entry}


def grade_shadow(store, now):
    graded = {r["row_id"] for r in ledger.read_rows(config.shadow_grades_path())}
    pending = [r for r in ledger.read_rows(config.shadow_ledger_path()) if r["row_id"] not in graded]
    if not pending:
        return 0, {}
    results = dict(store.con.execute(
        "SELECT ticker, result FROM settlements WHERE ticker IN (SELECT unnest(?))",
        [sorted({r["market_ticker"] for r in pending})]).fetchall())
    out, skipped = [], {}
    for r in pending:
        result = results.get(r["market_ticker"])
        if result is None:
            continue
        if result not in ("yes", "no"):
            skipped[f"result_{result}"] = skipped.get(f"result_{result}", 0) + 1
            continue
        won = (r["side"] == "YES") == (result == "yes")
        payout = Decimal(1 if won else 0)
        pnl = payout - Decimal(r["price_paid"]) - Decimal(r["fee_per_contract"])
        clv = closing_line(store, r)
        out.append({
            "row_id": r["row_id"], "market_ticker": r["market_ticker"], "event_ticker": r["event_ticker"],
            "side": r["side"], "rule_id": r["rule_id"], "snapshot_utc": r["snapshot_utc"],
            "result": result, "payout": payout, "price_paid": r["price_paid"],
            "fee_per_contract": r["fee_per_contract"], "pnl_per_contract": pnl, **clv,
            "graded_utc": iso_z(now)})
    ledger.append_rows(config.shadow_grades_path(), SHADOW_GRADE_HEADER, out)
    return len(out), skipped


def side_mid(yes_mid, side):
    """The mid of the side bought, before fee: the Yes mid, or 1 - it for NO."""
    return yes_mid if side == "YES" else Decimal(1) - yes_mid


def markouts(store, r, start):
    """Side mid at the next stored price 10-20 min after the row, and at the last price before the
    start, each minus the side mid at entry. Two-sided snapshots only; blank when missing."""
    out = {k: "" for k in MARKOUT_HEADER[8:15]}
    if r.get("kalshi_mid") in (None, ""):
        return out
    entry, at = side_mid(Decimal(r["kalshi_mid"]), r["side"]), parse_utc(r["snapshot_utc"])
    snaps = [(from_db_ts(ts), side_mid((bid + ask) / 2, r["side"])) for ts, bid, ask in store.con.execute("""
        SELECT ts_utc, yes_bid, yes_ask FROM price_snapshots
        WHERE ticker = ? AND ts_utc > ? AND ts_utc < ? AND yes_bid > 0 AND yes_bid <= yes_ask AND yes_ask < 1
        ORDER BY ts_utc""", [r["market_ticker"], to_db_ts(at), to_db_ts(start)]).fetchall()]
    found = {"next": next((s for s in snaps if NEXT_WINDOW[0] <= s[0] - at <= NEXT_WINDOW[1]), None),
             "close": snaps[-1] if snaps else None}
    out["entry_mid"] = entry
    for name, snap in found.items():
        if snap:
            out.update({f"{name}_utc": iso_z(snap[0]), f"{name}_mid": snap[1], f"markout_{name}": snap[1] - entry})
    return out


def grade_markouts(store, now, source):
    """Measure each shadow row once its game has started (idempotent by row_id). Postponed rows are
    skipped and counted. Returns (rows written, postponed rows skipped)."""
    path = config.shadow_markouts_path()
    done = {r["row_id"] for r in ledger.read_rows(path)}
    starts = checks.latest_starts(store, source)
    out, postponed = [], 0
    for r in ledger.read_rows(config.shadow_ledger_path()):
        if r["row_id"] in done:
            continue
        at_check = parse_utc(r["commence_utc"])
        start = starts.get(r["source_event_id"]) or at_check
        if start - at_check >= bets.POSTPONED_AFTER:
            postponed += 1
            continue
        if now < start:
            continue
        out.append({**{k: r[k] for k in MARKOUT_HEADER[:6]}, "start_utc": iso_z(start), "price_paid": r["price_paid"],
                    **markouts(store, r, start), "graded_utc": iso_z(now)})
    if out:
        ledger.append_rows(path, MARKOUT_HEADER, out)
    return len(out), postponed


def markout_report(store, postponed):
    """One line. Unit: the result (two ways to back one team are one), counted in games."""
    logged = ledger.read_rows(config.shadow_ledger_path())
    measured = {m["row_id"]: m for m in ledger.read_rows(config.shadow_markouts_path())}
    skipped = f"; {postponed} postponed rows skipped" if postponed else ""
    teams = {}
    for e in {r["event_ticker"] for r in logged}:
        titles = [t for (t,) in store.con.execute(
            "SELECT yes_sub_title FROM markets WHERE event_ticker = ? ORDER BY ticker", [e]).fetchall()]
        teams[e] = {"titles": [t for t in titles if t != "Tie"], "tie": "Tie" in titles}
    first = {}       # each result's earliest first-fire row: {row_id: game}
    for rs in bets.results(logged, lambda r: bets.result_key(r, teams[r["event_ticker"]])).values():
        pick = min(rs, key=lambda x: (x["snapshot_utc"], x["row_id"]))
        first[pick["row_id"]] = pick["event_ticker"]
    parts = []
    for name, words in (("markout_next", "next price 10-20 min later"), ("markout_close", "last price before start")):
        games = {}
        for rid, event in first.items():
            if measured.get(rid, {}).get(name):
                games.setdefault(event, []).append(Decimal(measured[rid][name]))
        if games:
            n = len(games)
            mean = statistics.fmean(float(sum(v) / len(v)) for v in games.values())
            parts.append(f"{words} {mean:+.4f} over {n} games"
                         + (f" (INSUFFICIENT: under {INSUFFICIENT_N})" if n < INSUFFICIENT_N else ""))
    if parts:
        return ["markouts (Kalshi mid of the side bought, before fee, minus the mid at entry; record only): "
                + " · ".join(parts) + skipped]
    cause = ("no shadow rows logged yet" if not logged
             else "all logged games postponed" if not measured and postponed == len(logged)
             else "no logged game has started yet" if not measured
             else "no later two-sided price before any start")
    return [f"markouts: 0 measured ({cause}){skipped}"]


def insufficient(n):
    return f"{n} (INSUFFICIENT: under {INSUFFICIENT_N})" if n < INSUFFICIENT_N else str(n)


def calibration_report(matched, settled_matched):
    rows = ledger.read_rows(config.calibration_grades_path())
    if not rows:
        return [f"calibration: 0 graded (no matched market has settled yet): matched markets={matched}, "
                f"matched and settled={settled_matched}"]
    events = {r["event_ticker"] for r in rows}
    mean = lambda k: statistics.fmean(float(r[k]) for r in rows)
    bk, br, lk, lr = mean("brier_kalshi"), mean("brier_ref"), mean("logloss_kalshi"), mean("logloss_ref")
    return [
        f"calibration: {len(rows)} markets, independent events {insufficient(len(events))}",
        f"  Brier    kalshi {bk:.4f}  reference {br:.4f}  (kalshi - reference {bk - br:+.4f}; lower is better)",
        f"  log loss kalshi {lk:.4f}  reference {lr:.4f}  (kalshi - reference {lk - lr:+.4f})",
    ] + power_lines(rows)


def power_lines(rows):
    have = [r for r in rows if r.get("p_ref_power")]
    if not have:
        return ["  power de-vig: no graded rows with a power fair probability yet"]
    bp = statistics.fmean(float(r["brier_ref_power"]) for r in have)
    lp = statistics.fmean(float(r["logloss_ref_power"]) for r in have)
    bpr = statistics.fmean(float(r["brier_ref"]) for r in have)
    lpr = statistics.fmean(float(r["logloss_ref"]) for r in have)
    return [f"  reference, power vs proportional on the same {len(have)} markets: "
            f"Brier {bp:.4f} vs {bpr:.4f}, log loss {lp:.4f} vs {lpr:.4f} (lower is better)"]


def shadow_report():
    graded = ledger.read_rows(config.shadow_grades_path())
    if not graded:
        logged = len(ledger.read_rows(config.shadow_ledger_path()))
        return [f"shadow: 0 graded ({logged} rows logged; none of their markets has settled yet)"]
    first = {}
    for r in sorted(graded, key=lambda r: r["snapshot_utc"]):
        first.setdefault((r["market_ticker"], r["side"], r["rule_id"]), r)
    by_event = {}
    for r in first.values():
        by_event.setdefault(r["event_ticker"], []).append(float(r["pnl_per_contract"]))
    event_pnl = {e: statistics.fmean(v) for e, v in by_event.items()}
    pnls = [float(r["pnl_per_contract"]) for r in first.values()]
    lines = [
        f"shadow: {len(graded)} rows graded; first-fire {len(first)}; "
        f"independent events {insufficient(len(event_pnl))}",
        f"  first-fire P&L per contract after fees: mean {statistics.fmean(pnls):+.4f}, "
        f"median {statistics.median(pnls):+.4f}",
    ]
    if len(event_pnl) > 1:
        best = max(event_pnl, key=event_pnl.get)
        rest = [v for e, v in event_pnl.items() if e != best]
        lines.append(f"  per-event mean {statistics.fmean(event_pnl.values()):+.4f}; "
                     f"without the best event {statistics.fmean(rest):+.4f}")
    clvs = [float(r["clv"]) for r in first.values() if r.get("clv") not in (None, "")]
    if clvs:
        lines.append(f"  first-fire CLV (closing side fair - price paid - fee): mean {statistics.fmean(clvs):+.4f}, "
                     f"median {statistics.median(clvs):+.4f}, over {len(clvs)} rows (record only)")
    else:
        lines.append("  first-fire CLV: none yet (no second reference read before a start)")
    lines.append("  Phase 1 claims nothing from these numbers; this is a record, not a verdict.")
    return lines


def latest_replies(bet_rows):
    """{shadow row_id: latest took/skip reply}. A later reply for the same pick replaces an earlier one."""
    out = {}
    for r in sorted(bet_rows, key=lambda r: (r["message_utc"], r["logged_utc"])):
        if r["action"] in ("took", "skip") and r.get("row_id"):
            out[r["row_id"]] = r
    return out


def taken_bets(store, ledger_rows=None, bet_rows=None):
    """Bets the owner took, each with its settled result in dollars at the price taken.

    dollars is None while the market is unsettled or settled other than yes/no.
    """
    ledger_rows = ledger.read_rows(config.shadow_ledger_path()) if ledger_rows is None else ledger_rows
    bet_rows = ledger.read_rows(config.bet_log_path()) if bet_rows is None else bet_rows
    by_id = {r["row_id"]: r for r in ledger_rows}
    taken = [(reply, by_id[rid]) for rid, reply in latest_replies(bet_rows).items()
             if reply["action"] == "took" and rid in by_id]
    results = dict(store.con.execute(
        "SELECT ticker, result FROM settlements WHERE ticker IN (SELECT unnest(?))",
        [sorted({row["market_ticker"] for _, row in taken})]).fetchall()) if taken else {}
    out = []
    for reply, row in taken:
        price, n = Decimal(reply["price"]), int(reply["contracts"])
        fee = fee_for_order(price, n, Decimal(row["fee_multiplier"]), "taker", Decimal(row["fee_precision"]))
        result = results.get(row["market_ticker"])
        dollars = None
        if result in ("yes", "no"):
            won = (row["side"] == "YES") == (result == "yes")
            dollars = (Decimal(n) if won else Decimal(0)) - price * n - fee
        out.append({"row_id": row["row_id"], "pick_date": reply["pick_date"], "pick_no": reply["pick_no"],
                    "event_ticker": row["event_ticker"], "market_ticker": row["market_ticker"],
                    "side": row["side"], "price": price, "contracts": n, "fee": fee,
                    "result": result, "dollars": dollars})
    return out


def _dollars(x):
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def bets_report(store):
    """Taken and not-taken picks, both always reported (docs/specs/grader_spec.md amendment)."""
    bet_rows = ledger.read_rows(config.bet_log_path())
    if not bet_rows:
        return ["  bets you took: no replies logged yet", "  picks not taken: no replies logged yet"]
    ledger_rows = ledger.read_rows(config.shadow_ledger_path())
    taken = taken_bets(store, ledger_rows, bet_rows)
    settled = [b for b in taken if b["dollars"] is not None]
    lines = [f"  bets you took: {len(taken)} in {len({b['event_ticker'] for b in taken})} games; "
             f"settled {len(settled)}: {_dollars(sum((b['dollars'] for b in settled), Decimal(0)))} "
             f"at your prices; open {len(taken) - len(settled)}"]
    taken_ids = {b["row_id"] for b in taken}
    first = {}
    for r in sorted(ledger_rows, key=lambda r: r["snapshot_utc"]):
        first.setdefault((r["market_ticker"], r["side"], r["rule_id"]), r)
    not_taken = [r for r in first.values() if r["row_id"] not in taken_ids]
    grades = {g["row_id"]: g for g in ledger.read_rows(config.shadow_grades_path())}
    graded = [(r, grades[r["row_id"]]) for r in not_taken if r["row_id"] in grades]
    paper = sum((Decimal(g["pnl_per_contract"]) * int(r["contracts_assumed"]) for r, g in graded), Decimal(0))
    lines.append(f"  picks not taken: {len(not_taken)} in {len({r['event_ticker'] for r in not_taken})} games; "
                 f"settled {len(graded)}: {_dollars(paper)} on paper at the logged price; "
                 f"open {len(not_taken) - len(graded)}")
    return lines


# Stage line and paper results for the slate (docs/specs/slate_alerts_spec.md) ------------

STAGES = ((1000, "Confirmed"), (500, "Supported"), (100, "Early signal"))
NEXT_STAGE = {"Early signal": "Supported needs 500", "Supported": "Confirmed needs 1,000"}
PRACTICE_N = 100
DRAWDOWN_UNITS = 20
Z95 = 1.96


def _low(xs):
    if len(xs) < 2:
        return None
    return statistics.fmean(xs) - Z95 * statistics.stdev(xs) / math.sqrt(len(xs))


def stage(graded, closes_possible=True):
    """(stage name, line) from graded first-fire bets, by game (docs/specs/slate_alerts_spec.md).

    Only games with a closing price (a non-blank clv) count toward beating the final price
    and toward the stage thresholds. closes_possible=False (the free plan reads each game once)
    says so on the Practice line rather than showing a count that cannot grow.
    """
    ids = set(bets.first_fire_ids(graded).values())
    first = sorted((r for r in graded if r["row_id"] in ids), key=lambda r: (r["snapshot_utc"], r["row_id"]))
    games = {}
    for r in first:
        games.setdefault(r["event_ticker"], []).append(r)
    n = len(games)
    beat, ret = [], []
    for rs in games.values():
        have = [float(r["clv"]) for r in rs if r.get("clv") not in (None, "")]
        if have:
            beat.append(statistics.fmean(have))
        ret.append(sum(float(r["pnl_per_contract"]) for r in rs)
                   / sum(float(r["price_paid"]) + float(r["fee_per_contract"]) for r in rs))
    cum = peak = 0.0
    for r in first:
        cum += float(r["pnl_per_contract"]) / (float(r["price_paid"]) + float(r["fee_per_contract"]))
        peak = max(peak, cum)
    c = len(beat)                           # games with a closing price
    if c >= PRACTICE_N and statistics.fmean(beat) <= 0:
        return "Practice", f"Practice (demoted: not beating the final price) · {c} games"
    if peak - cum >= DRAWDOWN_UNITS:
        return "Practice", f"Practice (demoted: {DRAWDOWN_UNITS}-bet losing stretch) · {n} games"
    beat_low = _low(beat)
    for need, name in STAGES:
        if c < need or beat_low is None or beat_low <= 0:
            continue
        if name == "Early signal" or (name == "Supported" and statistics.fmean(ret) > 0) or \
                (name == "Confirmed" and (_low(ret) or 0) > 0):
            return name, " · ".join([name, f"{c} games with a closing price"]
                                    + ([NEXT_STAGE[name]] if name in NEXT_STAGE else []))
    return "Practice", (f"Practice · {c} of {PRACTICE_N} games with a closing price"
                        + ("" if closes_possible else " (free plan adds none)"))


def paper_results(store, picks):
    """Paper results at $10 per pick: (won, lost, void, open, dollars, staked)."""
    tickers = sorted({r["market_ticker"] for r in picks})
    results = dict(store.con.execute(
        "SELECT ticker, result FROM settlements WHERE ticker IN (SELECT unnest(?))", [tickers]).fetchall()) \
        if tickers else {}
    won = lost = void = open_ = 0
    money = staked = Decimal(0)
    for r in picks:
        res = results.get(r["market_ticker"])
        n = int(r["contracts_assumed"])
        if res is None:
            open_ += 1
            continue
        if res not in ("yes", "no"):
            void += 1
            continue
        win = (r["side"] == "YES") == (res == "yes")
        won, lost = won + win, lost + (not win)
        cost = (Decimal(r["price_paid"]) + Decimal(r["fee_per_contract"])) * n
        money += (Decimal(n) if win else Decimal(0)) - cost
        staked += cost
    return won, lost, void, open_, money, staked


# The owner's own bets (docs/specs/my_bets_spec.md): record-only, never the shadow ledger or stage ---

MY_BET_GRADE_HEADER = ["bet_id", "result", "dollars", "legs_settled", "graded_utc"]


def _own_bets():
    """Logged own bets, one per bet_id (the first row); a row without a bet_id stays on its own."""
    out = {}
    for n, b in enumerate(ledger.read_rows(config.my_bets_path())):
        out.setdefault(b.get("bet_id") or f"row {n}", b)
    return list(out.values())


def _identified(b):
    return all(b["leg_tickers"].split(" | "))


def _money(b):
    """(cost, payout) or None when the row can't be read."""
    try:
        return Decimal(b["cost"]), Decimal(b["payout"])
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return None


def _readable(b):
    """Every needed field present, and both amounts finite numbers (my_bets_spec.md, amendment 2026-10-24)."""
    if not all(isinstance(b.get(k), str) for k in ("bet_id", "leg_tickers", "cost", "payout")) or not b["bet_id"]:
        return False
    money = _money(b)
    return bool(money) and all(x.is_finite() for x in money)


OPEN_PAST_DUE = timedelta(days=3)


def settle_my_bets(store, now):
    """Settle each fully identified bet once, only when every leg has settled (my_bets_spec.md, amendment
    2026-10-24): won when all yes; void_leg when any leg settled other than yes or no; otherwise lost. A bet with a
    leg not identified is never graded; an unreadable row is skipped. Returns rows written."""
    done = {r["bet_id"] for r in ledger.read_rows(config.my_bet_grades_path())}
    pending = [b for b in _own_bets() if b["bet_id"] not in done and _readable(b) and _identified(b)]
    tickers = sorted({t for b in pending for t in b["leg_tickers"].split(" | ")})
    results = dict(store.con.execute(
        "SELECT ticker, result FROM settlements WHERE ticker IN (SELECT unnest(?))", [tickers]).fetchall()) \
        if tickers else {}
    out = []
    for b in pending:
        got = [results.get(t) for t in b["leg_tickers"].split(" | ")]
        cost, payout = _money(b)
        if None in got:
            continue                                   # open: a leg hasn't settled
        if all(g == "yes" for g in got):
            result, dollars = "won", payout - cost
        elif any(g not in ("yes", "no") for g in got):
            result, dollars = "void_leg", ""           # out of the net, whatever the other legs did
        else:
            result, dollars = "lost", -cost
        out.append({"bet_id": b["bet_id"], "result": result, "dollars": dollars,
                    "legs_settled": sum(1 for g in got if g is not None), "graded_utc": iso_z(now)})
    ledger.append_rows(config.my_bet_grades_path(), MY_BET_GRADE_HEADER, out)
    return len(out)


def my_bets_summary():
    """(logged, won, lost, void, can't settle, net dollars over won and lost, unreadable)."""
    bets = _own_bets()
    grades = {g["bet_id"]: g for g in ledger.read_rows(config.my_bet_grades_path())}
    won = sum(1 for g in grades.values() if g["result"] == "won")
    lost = sum(1 for g in grades.values() if g["result"] == "lost")
    void = sum(1 for g in grades.values() if g["result"] == "void_leg")
    unreadable = sum(1 for b in bets if not _readable(b))
    stuck = sum(1 for b in bets if _readable(b) and b["bet_id"] not in grades and not _identified(b))
    net = sum((Decimal(g["dollars"]) for g in grades.values() if g["result"] in ("won", "lost")), Decimal(0))
    return len(bets), won, lost, void, stuck, net, unreadable


def open_past_due(store, now):
    """Open, fully identified bets whose markets all resolved more than 3 days ago."""
    grades = {g["bet_id"] for g in ledger.read_rows(config.my_bet_grades_path())}
    open_bets = [b for b in _own_bets() if _readable(b) and _identified(b) and b["bet_id"] not in grades]
    n = 0
    for b in open_bets:
        legs = b["leg_tickers"].split(" | ")
        due = store.con.execute("SELECT count(*), max(resolve_by) FROM markets WHERE ticker IN (SELECT unnest(?))",
                                [legs]).fetchone()
        if due[0] == len(set(legs)) and due[1] is not None and from_db_ts(due[1]) < now - OPEN_PAST_DUE:
            n += 1
    return n


def my_bets_report(store=None, now=None):
    logged, won, lost, void, stuck, net, unreadable = my_bets_summary()
    if not logged:
        return ["your own bets: none logged yet"]
    money = f", {_dollars(net)}" if won + lost else ""         # no dollar figure from nothing settled (rule 8)
    late = open_past_due(store, now) if store is not None else 0
    return [f"your own bets: {logged} logged; settled {won + lost} (won {won}, lost {lost}){money}; "
            f"void leg {void}; open {logged - won - lost - void - stuck - unreadable}"
            + (f" ({late} past due)" if late else "") + f"; can't settle {stuck} (legs not identified)"
            + (f"; unreadable {unreadable}" if unreadable else "")]


def run_grader(client=None, source="the-odds-api", now_fn=utcnow, out=print):
    client = client or KalshiClient()
    now = now_fn()
    run_id = new_run_id(JOB, now)
    with Store.open() as store:
        store.job_event(run_id, JOB, "start", now, "running")
        summary = {}
        try:
            summary.update(sweep_settlements(store, client, now, set()))
            n_cal, cal_skips, settled_matched = calibrate(store, source, now)
            n_sh, sh_skips = grade_shadow(store, now)
            n_mk, mk_postponed = grade_markouts(store, now, source)
            try:                  # own bets never stop the shadow grading (my_bets_spec.md, amendment 2026-10-24)
                n_mine, mine_lines = settle_my_bets(store, now), my_bets_report(store, now)
            except Exception as exc:
                n_mine, mine_lines = "error", [f"your own bets: step FAILED ({type(exc).__name__}: {exc})"]
            matched = store.con.execute(
                "SELECT count(*) FROM odds_matches WHERE source = ?", [source]).fetchone()[0]
            summary.update({"calibration_graded": n_cal, "shadow_graded": n_sh, "markouts_measured": n_mk,
                            "my_bets_settled": n_mine,
                            "matched_markets": matched, "matched_and_settled": settled_matched,
                            **{f"cal_skip_{k}": v for k, v in cal_skips.items()},
                            **{f"shadow_skip_{k}": v for k, v in sh_skips.items()}})
            lines = [f"GRADER OK run={run_id} " + " ".join(f"{k}={v}" for k, v in summary.items())]
            lines += calibration_report(matched, settled_matched)
            lines += shadow_report()
            lines += markout_report(store, mk_postponed)
            lines += bets_report(store)
            lines += mine_lines
        except BaseException as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
            record_failure(store, run_id, JOB, now_fn(), summary)
            raise
        store.job_event(run_id, JOB, "finish", now_fn(), "ok", summary)
    for line in lines:
        out(line)
    return summary


def main():
    run_grader()
    return 0


if __name__ == "__main__":
    sys.exit(main())
