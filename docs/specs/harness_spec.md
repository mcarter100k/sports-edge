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
- **`kalshi_quote(con, ticker, t, max_age)`:** the (bid, ask) that
  `kalshi_mid` uses, for filters such as a maximum spread. `kalshi_mid` is
  defined from it, so the two never disagree.
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

## Isolation, the leakage test and fitting (amended 2026-10-22, owner review)

This replaces the search-path design. A source used to see shadow views
placed first on DuckDB's search path, and it could still reach the real
tables by naming `main.<table>`.

### The isolation connection

`Isolation(con)` is a **separate DuckDB connection.**
- **What it holds:** `quotes` and `reference` rows with a timestamp at or
  before the current decision time t, and `units`.
- **What it does not hold:** there is **no `outcomes` table**, and no other
  schema or attached database. Nothing later than t exists in it, so no
  table name can reach later data.
- **How it moves forward:** decisions are processed in time order. Moving to
  a later t inserts the rows in (previous t, t]. It never moves backwards.
- **The source data:** time-sorted Parquet copies in a private temporary
  folder, deleted afterwards.
- **What a source may use:** only `con`. Reading files or module globals is
  outside this guarantee.

### Scoring uses isolated predictions

`score()` and `score_walk_forward()` score the predictions a source makes
on the isolation connection. So the scored numbers cannot use data after t
through `con`, by construction.

### The leakage test

`check_no_peeking(con, source, decisions, sample=None, seed=0)`:
- **The comparison:** for each checked decision, it compares the source's
  prediction on the isolation connection with its prediction on the full
  scoring connection.
- **When it raises:** any difference beyond 1e-12, or an error on the
  isolation connection (for example a query of `outcomes`), raises
  `LeakageError` naming the source, the ticker and t.
- **What it catches:** a source whose output would change if it could see
  later data, including one that queries `main.quotes` by name.
- **The limit:** a source that reads outcomes in a way that never changes
  its output is not flagged, and does no harm.

**Every decision in registered runs.**
- `score(..., registered=True)`, the default, checks **every** decision.
  `sample` may be set only with `registered=False`, for development runs.
- Passing `sample` to a registered run raises `HarnessError`.
- The result records `leakage_checked` and whether the run was registered.

### Fit and predict across walk-forward folds

`score_walk_forward(con, decisions, make_source, max_age, train_days, test_days, embargo_days=1, expanding=True, step_days=None, ...)`:
1. **Folds:** `walk_forward` over the decisions' cluster dates (the units
   table), with `embargo_days ≥ 1` (required). The embargo keeps outcomes
   that settle overnight out of the test dates.
2. **A fresh source per fold:** `make_source()` builds a new instance for
   each fold, so nothing learned in one fold carries into another.
3. **Fitting:** `source.fit(train_con)` is called if the source has `fit`.
   `train_con` is a separate connection holding only:
   - `quotes` and `reference` rows before the cutoff (midnight Los Angeles
     time after the last training date);
   - `outcomes` and `units` for tickers whose cluster date is a training
     date.
4. **Prediction:** the fold's test decisions are predicted on the isolation
   connection, never on `train_con`, and leakage-checked as above.
5. **Scoring:** pooled over every fold's test decisions, exactly as in
   `score()`.

**The guarantee:** a source cannot fit on test-period quotes, references or
outcomes, because they are absent from every connection it is given before
or while predicting a test decision.

## Tests (Gate 1)

Added 2026-10-22:
- A source querying `main.quotes` explicitly is caught.
- A source querying `outcomes` is caught (the table is absent from the
  isolation connection).
- During every isolated prediction, the isolation connection's latest
  timestamp is at or before t, and it has no `outcomes` table.
- A source that memorizes outcomes in `fit` scores exactly 0.5 on every
  test decision in walk-forward. As a positive control, the same source fit
  on all the data predicts perfectly.
- The training connection holds no row at or after the cutoff and no
  outcome of a test ticker.
- `make_source` is called once per fold.
- A registered run checks every decision; `sample` raises in a registered
  run.

- `kalshi_mid` picks the latest two-sided quote at or before t within the
  age, and rejects later, one-sided and stale quotes.
- `check_no_peeking` passes an honest source. It **fails** a source that
  reads the last quote overall, one that reads outcomes, and one that reads
  `reference` past t. These are the positive controls.
- Unit counting: two markets of one game are one unit.
- The intervals resample clusters and are seeded.
- The Pinnacle baseline reads "not available" when absent.
- `walk_forward` keeps the order and embargo, with no overlap.
