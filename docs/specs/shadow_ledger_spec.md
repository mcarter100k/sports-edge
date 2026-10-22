# Spec: shadow ledger (`sports_edge/shadow.py`, `sports_edge/ledger.py`)

Status: Phase 1. Protected files. **No trades, ever.**

## When a row is written

The check runs for every matched Kalshi market in every snapshot run, using
that run's snapshot, and only if **all** of these hold:

- the market `status` is `active`;
- `yes_ask` and `no_ask` both exist and are strictly between 0 and 1;
- the latest reference fetch for its event is no older than
  `ODDS_MAX_AGE_MIN` (default 75) at the snapshot time;
- the snapshot time is **before** the reference `commence_utc` (pre-game
  only: an in-play Kalshi price against a pre-game line is not a
  comparison);
- the fee model knows the market's fee type.

Each failed condition is counted per run under a named reason.

For fair probability `f` (de-vigged, for that market's outcome):

- **YES side:** `edge = f − yes_ask − fee_per_contract(yes_ask)`
- **NO side:** `edge = (1 − f) − no_ask − fee_per_contract(no_ask)`

A row is written for **each** side where `edge > SHADOW_EDGE_THRESHOLD`
(default 0.03, i.e. 3 cents per $1 contract after fees). Every qualifying
run writes a row. Rows are never deduplicated at write time; the grader
collapses them (see `grader_spec.md`).

## Rule identity

`rule_id = GAP_v1` covers threshold 0.03, taker buy at best ask,
`SHADOW_CONTRACTS` = 100, non-direct precision $0.01, proportional de-vig,
Pinnacle h2h, and `ODDS_MAX_AGE_MIN` = 75. Changing any of these means a new
`rule_id`. Overriding a parameter by environment variable sets `rule_id` to
`GAP_v1+custom` and records the values, so the rows are never mistaken for
the frozen rule.

## Columns (`data/shadow_ledger.csv`, schema_version 1)

`row_id, schema_version, rule_id, logged_utc, run_id, snapshot_utc,
odds_fetched_utc, book_last_update_utc, commence_utc, sport, competition,
event_ticker, market_ticker, outcome, side, yes_bid, yes_ask, no_bid,
no_ask, price_paid, kalshi_implied_prob, fair_prob, fee_per_contract,
contracts_assumed, edge, threshold, fee_type, fee_multiplier,
fee_waiver_expiration_utc, odds_source, bookmaker, source_event_id,
overround, devig_method`

- `row_id` = sha1 of `run_id|market_ticker|side`, so it is unique and
  stable.
- `kalshi_implied_prob` = the probability implied for the **side bought**
  (`yes_ask` for YES, `no_ask` for NO).
- Every time is UTC ISO-8601 with `Z`.

## Append-only writes (`ledger.py`)

`append_rows(path, header, rows)` takes an exclusive `fcntl` lock and
writes the header only if the file is new. If the file exists with a
different header, it raises `LedgerSchemaMismatch` and writes nothing. It
appends and calls `fsync`. Nothing in the package opens a ledger in `w`
mode, and a test enforces this.
