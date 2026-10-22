# Validation log

Append-only. A correction is a new dated entry; earlier entries are never
rewritten. Statuses: VERIFIED, OBSERVED, INFERRED, PENDING, NOT CHECKED.

## PENDING LIVE CONFIRMATION

Entries are cleared with a date and the evidence that reality produced.

- (none yet)

---

## 2026-10-21 — Phase 1 specs: source facts

- VERIFIED (live call): `GET https://external-api.kalshi.com/trade-api/v2/series?category=Sports`
  with no credentials returned HTTP 200 and 4,029 series. Fee pairs:
  quadratic/1 = 3,904, quadratic_with_maker_fees/1 = 106, quadratic/0.5 = 18,
  quadratic_with_maker_fees/0.5 = 1. No `flat`.
- OBSERVED (live scan): 8,325 open sports events, 93,241 open sports
  markets, 16,385 resolving within 48 h (by the earlier of expected
  expiration and close time). 2,449 events have competition_scope `Game`.
- OBSERVED: `GET /markets?min_close_ts=…&max_close_ts=…` across all
  categories returned HTTP 504. The design does not depend on it.
- NOT READ: the Kalshi fee schedule PDF (HTTP 429 to every automated
  fetch). The 0.07 taker coefficient is pinned by the docs' fee-rounding
  worked example. See `docs/specs/fee_model_spec.md` open items.

## 2026-10-22: GAP_v2 amended in place before any live run

- Change: the book last-update age is no longer a gate. It remains a
  record-only field (`book_update_age_s`) on every row. The only staleness
  gate is the reference fetch age ≤ 15 min. The `book_line_stale` skip is
  removed.
- Why amended in place: GAP_v2 has written no live rows (no live ledger
  exists; launchd is not installed), so no evidence carries the earlier
  definition. Owner-directed.
- Evidence for this change: see the run report of 2026-10-22 (suite result
  and the mutation test that re-adds the gate).
