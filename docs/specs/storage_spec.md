# Spec: storage (`sports_edge/store.py`)

Status: Phase 1. Protected file.

**Engine:** one DuckDB file, `$SPORTS_EDGE_DATA_DIR/sports_edge.duckdb`
(default `data/`). It has one dependency and needs no server.

**One writer at a time.** Every job takes an exclusive `fcntl` lock on
`$SPORTS_EDGE_DATA_DIR/.db.lock` before opening the database, and waits up
to 10 minutes. If the lock can't be had, the job fails loudly. It never
skips silently.

**Times:** every timestamp column is a DuckDB `TIMESTAMP` holding UTC. Not
`TIMESTAMPTZ`: reading those back into Python needs the extra `pytz` package
(tested 2026-10-21), and the zone is fixed anyway. Code converts to aware UTC
datetimes on the way in and out.
**Money:** `DECIMAL(10,6)` dollars. **Counts:** `DECIMAL(18,2)`.

## Tables

| Table | Kind | Key / rule |
|---|---|---|
| `series` | metadata, upserted | `series_ticker`. Title, tags, fee_type, fee_multiplier, first/last seen |
| `markets` | metadata, upserted | `ticker`. Event, series, sport, competition, competition_scope, titles, status, open/close/expected-expiration/occurrence times, effective fee type and multiplier (event override applied), first/last seen |
| `price_snapshots` | **evidence, append-only** | one row per market per run: run_id, ts_utc (fetch time of the page that carried it), tier, status, yes/no bid/ask, last price, bid/ask sizes, volume, 24h volume, open interest |
| `settlements` | **evidence, insert-once** | `ticker`. result, settlement_value, settlement_ts, recorded_utc, source. Only **finalized** markets are recorded. If a later read disagrees, a row goes to `settlement_conflicts` and the original is never overwritten |
| `reference_odds` | **evidence, append-only** | one row per outcome per fetch: fetched_utc, source, bookmaker, sport_key, source_event_id, commence_utc, home, away, outcome, decimal_odds, fair_prob, overround, devig_method, book_last_update_utc |
| `odds_matches` | **evidence, insert-once** | (market_ticker, source). event_ticker, source_event_id, ref_outcome (the reference outcome name this market's YES maps to), rule_version, matched_utc. A later different match goes to `match_conflicts`, and that event is never compared again |
| `unmatched` | append-only | run_id, ts_utc, kalshi_event_ticker, sport, reason, detail |
| `odds_calls` | append-only | ts_utc, sport_key, endpoint, http_status, credits_last, credits_used, credits_remaining |
| `job_runs` | append-only | run_id, job, phase (`start`/`finish`), ts_utc, status, summary JSON |

`markets` and `series` are working metadata and can be rebuilt from the
API. Every other table is evidence.

**Bulk writes** go through a temporary CSV and `read_csv` with explicit column
types (93k rows in 0.16 s, against 3.8 s for 5k rows with `executemany`).

**All-or-nothing per run:** a snapshot run collects every page first, then
writes markets, snapshots and settlements in one transaction. If pagination
fails partway, the run writes no snapshots and fails.

**Size (measured, not assumed):** about 93k open sports markets once an
hour plus about 16k every 15 minutes comes to roughly 3.4M snapshot rows a
day. The weekly heartbeat reports the database size and free disk, and alerts
below 5 GB free. Disk free on 2026-10-21 was 18 GiB.

---

## Amendment 2026-10-22

`reference_odds` adds `fair_prob_power DOUBLE` (power-method de-vig,
record-only).
