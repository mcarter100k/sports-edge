"""Nightly grader (docs/specs/grader_spec.md).

python -m sports_edge.grader
"""
import math
import statistics
import sys
from decimal import Decimal

from . import config, ledger
from .kalshi import KalshiClient
from .snapshot import new_run_id, record_failure, sweep_settlements
from .store import Store, from_db_ts
from .timeutil import iso_z, utcnow

JOB = "grader"
INSUFFICIENT_N = 20
P_CLIP = 1e-6

CALIBRATION_HEADER = [
    "market_ticker", "event_ticker", "sport", "source_event_id", "y", "p_kalshi", "p_ref",
    "kalshi_snapshot_utc", "ref_fetched_utc", "commence_utc", "brier_kalshi", "brier_ref",
    "logloss_kalshi", "logloss_ref", "graded_utc",
]
SHADOW_GRADE_HEADER = [
    "row_id", "market_ticker", "event_ticker", "side", "rule_id", "snapshot_utc", "result",
    "payout", "price_paid", "fee_per_contract", "pnl_per_contract", "graded_utc",
]


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
            SELECT fetched_utc, fair_prob FROM reference_odds
            WHERE source = ? AND source_event_id = ? AND outcome = ? AND fetched_utc < ?
            ORDER BY fetched_utc DESC LIMIT 1""", [source, eid, outcome, commence]).fetchone()
        if snap is None or ref is None:
            reason = "no_pre_game_kalshi_quote" if snap is None else "no_pre_game_reference"
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        y = 1 if result == "yes" else 0
        p_k = float((Decimal(snap[1]) + Decimal(snap[2])) / 2)
        p_r = float(ref[1])
        rows.append({
            "market_ticker": ticker, "event_ticker": event, "sport": sport, "source_event_id": eid,
            "y": y, "p_kalshi": f"{p_k:.6f}", "p_ref": f"{p_r:.6f}",
            "kalshi_snapshot_utc": iso_z(from_db_ts(snap[0])),
            "ref_fetched_utc": iso_z(from_db_ts(ref[0])), "commence_utc": iso_z(from_db_ts(commence)),
            "brier_kalshi": f"{brier(p_k, y):.6f}", "brier_ref": f"{brier(p_r, y):.6f}",
            "logloss_kalshi": f"{log_loss(p_k, y):.6f}", "logloss_ref": f"{log_loss(p_r, y):.6f}",
            "graded_utc": iso_z(now)})
    ledger.append_rows(config.calibration_grades_path(), CALIBRATION_HEADER, rows)
    return len(rows), skipped, len(candidates)


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
        out.append({
            "row_id": r["row_id"], "market_ticker": r["market_ticker"], "event_ticker": r["event_ticker"],
            "side": r["side"], "rule_id": r["rule_id"], "snapshot_utc": r["snapshot_utc"],
            "result": result, "payout": payout, "price_paid": r["price_paid"],
            "fee_per_contract": r["fee_per_contract"], "pnl_per_contract": pnl, "graded_utc": iso_z(now)})
    ledger.append_rows(config.shadow_grades_path(), SHADOW_GRADE_HEADER, out)
    return len(out), skipped


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
    ]


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
    lines.append("  Phase 1 makes no edge claim; this is a record, not a verdict.")
    return lines


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
            matched = store.con.execute(
                "SELECT count(*) FROM odds_matches WHERE source = ?", [source]).fetchone()[0]
            summary.update({"calibration_graded": n_cal, "shadow_graded": n_sh,
                            "matched_markets": matched, "matched_and_settled": settled_matched,
                            **{f"cal_skip_{k}": v for k, v in cal_skips.items()},
                            **{f"shadow_skip_{k}": v for k, v in sh_skips.items()}})
            lines = [f"GRADER OK run={run_id} " + " ".join(f"{k}={v}" for k, v in summary.items())]
            lines += calibration_report(matched, settled_matched)
            lines += shadow_report()
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
