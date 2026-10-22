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

The rule's constants and `rule_id` live in `sports_edge/shadow.py` (protected).
`config.py` holds no signal parameter.


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

---

## Amendment 2026-10-22: rule GAP_v2 (owner review findings 1–3)

`GAP_v1` stays frozen as defined above. It wrote no live rows. New rows are
written under **`GAP_v2`**, which changes only the following:

**1. Staleness on two clocks.** Every check uses two reference ages: the
snapshot time minus the reference **fetch** time, and the snapshot time minus
the bookmaker's **last line update** (`book_last_update_utc`). Each is
recorded in seconds on every row (`odds_fetch_age_s`, `book_update_age_s`;
the sign is kept, so a negative value means the reference is newer than the
snapshot). The check is skipped if either absolute age exceeds
`ODDS_MAX_AGE_MIN`, **default 15**. The skips are counted separately as
`reference_stale` and `book_line_stale`.

**2. Depth.** The contracts resting at the price the side would buy at are
recorded on each row as `depth_contracts`: `yes_ask_size` for YES. For NO,
it is `yes_bid_size`, since buying NO at `1 − yes_bid` fills against the YES
bid. A side that clears the threshold but has `depth_contracts <
SHADOW_CONTRACTS` writes no row and is counted as `insufficient_depth`.

**3. Record-only metadata.** None of these fields may affect whether a row
is written. A test changes each of them and shows the rows stay the same.
- `minutes_to_start` = (commence − snapshot) in minutes.
- `kalshi_mid` and `kalshi_move_1h`: the YES mid now, minus the YES mid at
  the latest two-sided snapshot of the same market taken 60–120 minutes
  earlier. Blank if no snapshot exists in that window.
- `ref_move_1h`: the proportional fair probability now, minus the one from
  the latest reference fetch taken 60–120 minutes before this fetch. Blank
  if none.
- `fair_prob_power`: the power-method fair probability (see the odds
  matching amendment). The signal still uses `fair_prob` (proportional).
- Closing line value (CLV) is computed by the grader, because the closing
  line is only known after the start (see `grader_spec.md`).

**Fee precision** comes from the fee table (`rounding.balance_precision`,
cited), and is recorded on each row as `fee_precision`. It is no longer a
rule constant.

**GAP_v2 constants:** threshold 0.03, taker buy at best ask,
`SHADOW_CONTRACTS` = 100, `ODDS_MAX_AGE_MIN` = 15 on both clocks, depth
≥ contracts, proportional de-vig, Pinnacle h2h. An environment override
labels rows `GAP_v2+custom(...)`.

**Ledger schema_version 2** adds these columns: `ref_outcome`,
`odds_fetch_age_s`, `book_update_age_s`, `depth_contracts`,
`yes_bid_size`, `yes_ask_size`, `minutes_to_start`, `kalshi_mid`,
`kalshi_move_1h`, `ref_fair_1h_ago`, `ref_move_1h`, `fair_prob_power`
and `fee_precision`. No version-1 ledger was ever written live.

### Amendment to GAP_v2, 2026-10-22, before any live run (owner direction)

GAP_v2 is amended in place, not versioned to GAP_v3, because it has never
written a live row. **The only staleness gate is the reference fetch age**
(|snapshot − fetch| ≤ `ODDS_MAX_AGE_MIN`, default 15). The bookmaker's
last-update age (`book_update_age_s`) stays on every row, in seconds, and
is **record-only**: it never decides whether a row is written. The
`book_line_stale` skip is removed. Why: `last_update` may mark when
Pinnacle last *changed* the line, so a steady, current line would read as
stale. The measured odds cycle will report how these ages are distributed.
