# Spec: daily digest (owner request 2026-10-22)

**Status:**
- **Part A, the digest report: built** in `sports_edge/digest.py`. That
  module is not on the protected list; it reads the store, the ledgers and
  the health state, and writes only a new report file under `logs/`, like
  `heartbeat.py`.
- **Part B, scheduling it at 7:30 AM Pacific: PROPOSED, not built.** It
  needs a new launchd plist and a shell wrapper. Both paths are protected
  (`scripts/*.sh`, `scripts/launchd/*.plist`), so they wait for owner
  review.

The request called the digest "not a protected-file change". That is true
of Part A only.

---

## Part A — the report

`venv/bin/python -m sports_edge.digest`
- **Output:** the first line is the digest line. The second line is
  `report: <path>`.
- **Report file:** written to `logs/digest_<Pacific date>.txt`. It is
  created exclusively and never overwritten; a second run the same day
  writes `digest_<date>_2.txt`, and so on.
- **Exit code:** exit 1 on any exception. A digest that reports problems
  still exits 0; problems are content, not a failure.

**Wording (plain English, the owner's style):**

```
Games watched yesterday: N (by sport) · Underpriced bets spotted: N · Finished games scored: N ·
If you'd bet all of them: up/down X¢ per $1 (N games — too early to trust under 20) ·
Beat the final pre-game market price: N of N times · Skipped: N (prices too old, or not enough
contracts for sale) · System: OK/problems · Odds credits used: N of N
```

**Windows.**
- **"Yesterday"** is the previous Pacific calendar day, the same day the
  odds budget counts against (`odds_step.py`).
- **"Finished games scored"** counts grades written from Pacific midnight
  today up to the run. That is this morning's 3:30 AM grader run, which
  grades yesterday's finished games. Each grade is counted in exactly one
  digest.
- **The money and closing-price lines** are cumulative over every graded
  bet so far. They are the only lines whose N grows toward a claim.

**Fields.** An "event" is one game. The two sides of one game are one event
(AGENTS.md rule 9). A **bet** is a first-fire row: the earliest row per
market + side + rule_id. That is the grader's unit (`grader_spec.md`),
mirrored here and pinned by a parity test.

| Field | Definition | When the path cannot produce a number (rule 8) |
|---|---|---|
| Games watched yesterday | Distinct events with at least one price snapshot yesterday, broken down by sport | "no snapshot run finished yesterday" |
| Underpriced bets spotted | Distinct events with a first-fire row first logged yesterday | "none possible — no fee table" (or "fee table invalid"), because every fee fails closed and the logger cannot write a row |
| Finished games scored | Distinct events among grades written today before the run | "grader has never run" |
| If you'd bet all of them | Over all graded first-fire bets: 100 × Σ P&L ÷ Σ (price paid + fee), in cents per $1, with "up" or "down"; N = distinct events; "— too early to trust under 20" while N < 20 | "no finished bets yet" |
| Beat the final pre-game market price | Graded first-fire bets with a recorded closing value: count with clv > 0, "N of M times" | "no closing prices yet" |
| Skipped | Sum over yesterday's snapshot runs of the run summary's `skip_reference_stale` + `skip_insufficient_depth` | "no snapshot run finished yesterday" |
| System | "OK", or "problems — " and the problems: a job failing or never succeeded (`health`), fee table missing or invalid, no snapshot run yesterday | — |
| Odds credits used | Credits charged yesterday (`odds_calls.credits_last`) "of" the daily budget (`ODDS_DAILY_CREDITS`) | "not configured" when there is no odds key |

**The report file** holds the digest line, the window in Pacific time with
the zone shown, the by-sport breakdown, the bet and event counts behind
each number, the median P&L per bet, and the definitions above. Nothing in
it is called an edge.

## Part B — scheduling (PROPOSED; protected; owner review before build)

- `scripts/launchd/com.quis.sportsedge.digest.plist`, firing at Hour 7,
  Minute 30. launchd uses local time, and this machine runs on
  America/Los_Angeles, which the heartbeat checks.
- `scripts/run_digest.sh`: sources `common.sh`, runs the module, and calls
  `notify DIGEST "<digest line>" "<report path>"`. The click opens the
  report through notify's existing `-open file://`.
- **Fail loudly (rule 7):** on any error, a loud `DIGEST FAILED`
  notification and exit nonzero.
- **Owner decision needed:** `notify.sh` cuts the message at 300 characters
  (220 on the osascript fallback). A full digest line is about 330
  characters, so the banner would end mid-line. The options are to show a
  shortened banner (System, bets, money) with the full line in the report,
  or to change `notify.sh`'s limit, which is a protected edit.
- **PENDING once built:** the first 7:30 AM firing, recorded in
  `docs/validation_log.md`.

---

## Amendment 2026-10-22: two definitions corrected by the reality gate (before any merge)

The first reality run, on a real snapshot of 8,343 open games, showed that
two Part A definitions above were wrong:

1. **"Games watched"** counted every game in the daily full scan,
   including categories such as Chess and "Cities". That is the catalogue,
   not what the system watches; with the by-sport list the line ran to 738
   characters. **Now:** distinct games snapshotted in the matched tiers
   (15-minute and hourly), which are the games compared with reference
   odds. That is the same tier definition as the heartbeat. When there are
   none, the line reads "none matched to reference odds". The full-scan
   total, by category, moves to the report file.
2. **"Skipped: 0"** was printed when nothing had been checked: with no odds
   key the shadow step never runs. **Now:** the runs' `shadow_checked`
   counts are summed, and when the sum is zero the line reads "nothing
   checked (no games with reference odds)".

**Line length, measured:** 481 characters in the all-problems state, so a
`notify.sh` banner cuts it either way. The Part B owner decision stands.
