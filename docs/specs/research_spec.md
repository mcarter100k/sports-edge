# Spec: the research base (`research/`)

Status: Milestone 1, owner-directed 2026-10-22. Normal review, except the
files listed as PROTECTED in AGENTS.md (`research/harness.py` and
`research/registry/*/result.md`), which have their own spec.

## Boundaries

- `research/` is never imported by `sports_edge/`. A test enforces this.
- `research/` may import `sports_edge/` read-only helpers (`kalshi`,
  `store`, `timeutil`). It never writes to the live database, the live CSV
  ledgers, `logs/` or `state/`.
- **Research data lives outside the repo**, in `$SPORTS_EDGE_RESEARCH_DIR`
  (default `~/sports-edge-research`):
  - `backfill/raw/`: API responses, gzipped JSON lines, as received;
  - `backfill/tables/`: normalized Parquet;
  - `datasets/<name>/<id>/`: frozen datasets.

  Keeping it out of the repo keeps the test session's repo hash fast and
  stable. The test fixture points the variable at a temporary folder.
- `research/studies/` is the free zone: report-only scripts.

## `research/stats.py`

Standard library only. Every function has tests with pinned values.

| Function | Definition |
|---|---|
| `brier(p, y)` | mean of (p − y)² |
| `log_loss(p, y, clip=1e-6)` | mean of −[y ln p + (1 − y) ln(1 − p)], p clipped to [clip, 1 − clip] |
| `brier_decomposition(p, y, edges=None)` | Murphy: reliability − resolution + uncertainty. Bins are the given edges, or each distinct forecast value when `edges` is None. Returns those three terms, `brier`, and `within_bin` = brier − (rel − res + unc), which is exactly 0 when bins are distinct values. |
| `reliability_table(p, y, edges)` | per bin: lo, hi, n, mean forecast, observed frequency, Wilson 95% interval |
| `wilson_interval(k, n, conf=0.95)` | Wilson score interval |
| `bootstrap_ci(values, stat, groups=None, n_boot=2000, seed=0, conf=0.95)` | percentile interval. With `groups`, whole groups are resampled (cluster bootstrap), for example all games on one date together. Seeded, so the same input gives the same output. |
| `n_for_effect(sd, delta, alpha=0.05, power=0.8)` | two-sided z test: ⌈((z₁₋α/₂ + z_power) · sd / δ)²⌉ |
| `detectable_effect(sd, n, alpha=0.05, power=0.8)` | the inverse: (z₁₋α/₂ + z_power) · sd / √n |
| `power_at(sd, delta, n, alpha=0.05)` | Φ(\|δ\|√n / sd − z₁₋α/₂) |
| `z_test_p(mean, se)` | two-sided normal p value |
| `benjamini_hochberg(pvalues, q=0.05)` | (rejected flags, adjusted p values) in input order |

## `research/datasets.py`

- `export_live(name, tables=None, db=None)`: opens the live database through
  `Store.open(read_only=True)`, which takes the same lock as the scheduled
  jobs and so never reads a half-written run. It writes each table to
  Parquet with rows in a fixed order (every column, ascending), using one
  thread. Then it freezes them (below).
- `freeze(name, tables)`: `tables` maps a name to a Parquet file. It writes
  `datasets/<name>/<id>/` with the files and a `manifest.json` holding, per
  table: row count, file sha256 and content sha256. The content sha256
  covers the rows in a fixed order, so it does not depend on the Parquet
  writer. `id` is the first 16 hex characters of the sha256 of the
  canonical JSON of {table: [rows, content sha256]}. The same data always
  gets the same id.
- `open_dataset(dir)`: re-hashes every file against the manifest and fails
  on any difference. It returns a DuckDB connection with one view per
  table.

## `research/backfill.py` (Kalshi only, zero odds credits)

`python -m research.backfill` reads settled markets and hourly candles from
Kalshi's public market data. GET only, through `sports_edge.kalshi.KalshiClient`.

**Scope, fixed here before any outcome is read:**
- **Sports game series:** KXNFLGAME, KXNCAAFGAME, KXNBAGAME, KXNHLGAME,
  KXMLBGAME. Every settled market, live and historical.
- **Daily high temperature series:** KXHIGHNY, KXHIGHCHI, KXHIGHMIA,
  KXHIGHAUS, KXHIGHLAX, KXHIGHDEN, KXHIGHPHIL. Settled markets with a close
  time on or after 2025-10-22T00:00:00Z.

**Endpoints.**
- **Markets:** `/markets?status=settled` (settled after Kalshi's historical
  cutoff) and `/historical/markets` (before it), per series.
- **Candles:** hourly (`period_interval=60`; one-minute candles are refused
  for a whole market life). Live markets use `/markets/candlesticks`, at
  most 100 markets and 10,000 candles per call. Historical markets use
  `/historical/markets/{ticker}/candlesticks`.
- **Window per market:** [max(open, close − 14 days), close].

**Politeness.**
- At most `RESEARCH_MAX_RPS` requests a second in total (default 5), enforced
  by **one limiter shared by all worker threads**, so workers never burst
  together.
- `RESEARCH_WORKERS` threads (default 6) keep requests in flight, because
  one request at a time is bounded by its round trip of about 1.2 s.
- **Amended 2026-10-22:** per-worker limits that added up to 8 a second
  burst past Kalshi's limit and returned HTTP 429.
- No requests from 30 s before to 4 min after each quarter hour, while the
  live snapshot job runs.
- The client's retry on 429 and 5xx applies.

**Storage.**
- Raw responses are appended to `backfill/raw/<series>/*.jsonl.gz`.
- Normalization writes `backfill/tables/{markets,outcomes,candles}.parquet`.
  The two schemas (live `*_dollars` fields, historical plain fields) map to
  one set of columns.
- **Outcomes are a separate table**, so a study can be designed from
  `markets` and `candles` without reading results.
- Reruns skip markets whose candles are already stored.

## `research/registry/`

- `TEMPLATE.md`: the pre-registration form. Hypothesis, primary metric,
  unit of N and how events are made independent, smallest effect worth
  detecting with the sd assumed, the N from `n_for_effect` and the N
  available, decision time and the leakage rule, rejection rule, multiple
  testing correction, dataset id and manifest sha256, and what was looked
  at before registering.
- One folder per study: `prereg.md` (committed before the study runs),
  `dataset_manifest.json` (a copy of the frozen manifest), `result.md`
  (PROTECTED: written by the study script, never by hand).

## Review (`.claude/agents/auditor.md`, `docs/reviews/`)

- **The reviewer** is a fresh session limited to Read, Grep and Glob. It
  cannot write.
- **When:** it reviews each pre-registration before the study runs, and
  each result after.
- **Output:** saved verbatim to `docs/reviews/<date>_<study>_<stage>.md`,
  with a header naming the commit reviewed.
- **Verdicts:** APPROVE, REVISE (with numbered findings) or REJECT. A study
  does not run on REVISE or REJECT until the findings are answered in a new
  commit and reviewed again.

## `research/studies/match_coverage.py`

Read-only. It reports, per sport, Kalshi game events seen by the odds step
in a window: matched, unmatched by reason, and the match rate. It also
lists the most frequent `name_mismatch` details.
