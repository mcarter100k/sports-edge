# Spec: the scoring harness (`research/harness.py`)

Status: PROTECTED (AGENTS.md, added 2026-10-22). Owner review before merge.
The harness decides what a study measures. A change to it is a new spec
commit first, and a study that ran on the old harness keeps its result.

## What it scores

Any **probability source**: an object with a `name` and
`predict(con, ticker, decision_utc) -> float | None`. It is scored at a
**decision time** against:
- the outcome;
- the **Kalshi mid** at that time;
- the **Pinnacle fair** at that time.

The baselines are computed by the harness, never by the source.

## Point-in-time tables (DuckDB views on the connection)

| View | Columns | Meaning |
|---|---|---|
| `quotes` | ticker, ts_utc, yes_bid, yes_ask | a Kalshi quote known at ts_utc (a backfill candle's close at its `end_period_utc`, or a live price snapshot) |
| `reference` | ticker, fetched_utc, fair_prob | the de-vigged Pinnacle chance for that Kalshi market's outcome, known at fetched_utc |
| `outcomes` | ticker, y | 1 if the market resolved yes, 0 if no; voids absent |
| `units` | ticker, unit_id, cluster | the independent event a market belongs to (a game, a city-day) and its cluster for intervals (the Los Angeles date of the decision) |

An adapter builds these views from a frozen dataset. The harness only
reads them.

## Baselines at decision time t

- **`kalshi_mid(con, ticker, t, max_age)`:** from the latest quote with
  t − max_age ≤ ts_utc ≤ t and 0 < yes_bid ≤ yes_ask < 1, (bid + ask) / 2.
  Otherwise None. A quote stamped exactly t is known at t: a candle's
  `end_period_utc` is the end of its hour.
- **`pinnacle_fair(con, ticker, t, max_age)`:** the latest reference with
  t − max_age ≤ fetched_utc ≤ t. Otherwise None.
- **`KalshiMidSource(max_age)`:** a source whose prediction is
  `kalshi_mid`.

## `score(con, decisions, source, max_age, seed=0, n_boot=2000)`

`decisions` is a list of (ticker, decision_utc). For each decision with an
outcome and a unit, the harness records the source's p, the Kalshi mid, the
Pinnacle fair and y. Then, separately for the source and for each baseline:
- **Coverage:** rows and **units**.
- **Brier and log loss, per unit.** Each unit's rows are averaged first, so
  a two-sided game counts once. Reported as the mean over units.
- **Paired difference** (source − baseline, Brier and log loss), on units
  where both exist. A 95% cluster-bootstrap interval resamples whole
  clusters.
- **When a baseline has no value on any row,** the result says
  `not available (no <baseline> prices at decision time in this dataset)`.
  It never shows a zero (AGENTS.md rule 8).

## `walk_forward(dates, train_days, test_days, step_days=None, embargo_days=0, expanding=True)`

Splits over sorted distinct dates: [(train_dates, test_dates)].
- Every test date is later than every train date plus the embargo.
- Test windows do not overlap.
- Rolling (`expanding=False`) keeps only the last `train_days` dates.

## `check_no_peeking(con, source, decisions, sample=50, seed=0)`

**The leakage test.** For up to `sample` decisions, drawn with the seed, it
predicts three times:
1. on the real views;
2. on **poisoned** views: every quote and reference row after t is replaced
   by a deterministic, unrelated value, and every outcome is flipped;
3. on **truncated** views: rows after t are removed and `outcomes` is
   empty.

If any prediction differs between the three (beyond 1e-12), it raises
`LeakageError` naming the source, ticker and t.

- Poisoned and truncated views live in their own schemas and are put first
  on DuckDB's search path, so a source querying `quotes` sees them. The
  real views are restored afterwards, even on error.
- `modes` (default both) chooses the poisoned and truncated variants, so
  each can be tested on its own.
- A source that reads files or globals outside the connection is not
  covered. Sources must read only through `con`.
- `score()` runs this check first and refuses to score a source that fails
  it.

## Tests (Gate 1)

- `kalshi_mid` picks the latest two-sided quote at or before t within the
  age, and rejects later, one-sided and stale quotes.
- `check_no_peeking` passes an honest source. It **fails** a source that
  reads the last quote overall, one that reads outcomes, and one that reads
  `reference` past t. These are the positive controls.
- Unit counting: two markets of one game are one unit.
- The intervals resample clusters and are seeded.
- The Pinnacle baseline reads "not available" when absent.
- `walk_forward` keeps the order and embargo, with no overlap.
