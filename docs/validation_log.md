# Validation log

Append-only. A correction is a new dated entry; earlier entries are never
rewritten. Statuses: VERIFIED, OBSERVED, INFERRED, PENDING, NOT CHECKED.

## PENDING LIVE CONFIRMATION

Entries are cleared with a date and the evidence that reality produced.

- **Daily digest at 7:30 AM Pacific** (added 2026-10-22): the first scheduled
  notification after `install.sh` loads `com.quis.sportsedge.digest` at merge.
  Until then the wrapper has run only under `scripts/reality_run.py`.
  *Proposed for replacement* by the 8:00 AM morning iMessage (see
  `docs/proposals/slate_part_b.md`).
- **Slate and alerts, Part B built (approved without the wake), not installed**
  (added 2026-10-22; updated the same day). Pending until installed and observed:
  - the first 8:00 AM morning iMessage;
  - the first pre-game check iMessage;
  - the first "Don't bet now" from a real failing run;
  - the first reply appended by the owner's Shortcut and logged from
    `replies.txt`.
- **First real pick** (added 2026-10-22). This needs an odds key in `.env`
  and `config/fee_schedule.json` built from the saved fee PDF. Until both
  exist, no check can price a game.

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

## 2026-10-22: daily digest report (Part A) built; scheduling not built

- **Built:** `sports_edge/digest.py` (not on the protected list), per
  `docs/specs/daily_digest_spec.md` Part A and its amendment. **Not built:**
  the 7:30 AM launchd plist and wrapper (Part B). Those are protected and
  wait for owner review. The request's premise ("not a protected-file
  change") holds for Part A only.
- **Gate 1:** 11 digest tests, plus 1 fee test pinning that
  `quadratic_with_combo_maker_fees` fails closed against a table without a
  maker fee.
- **Mutation checks:** 13 digest mutants and 1 fee mutant, each killed and
  each reverted byte-identical.
  - The first window-edge mutant survived. Shifting both edges swapped
    which boundary row counted, so the total did not change. The test was
    made asymmetric (two games at the start edge, one at the end) and all
    three edge variants are now killed.
  - A test fixture used `fee_unknown` where the run summary writes
    `skip_fee_unknown`; it was corrected before the mutant that adds fee
    skips to "Skipped" was run.
- **Gate 2 (reality, `scripts/reality_run.py`):**
  - A real read-only snapshot found 8,343 games, 93,288 markets and 22
    categories, with 0 live files changed and no failure markers.
  - The digest at the real time printed "no snapshot run finished
    yesterday", which is true: the only run was today.
  - With the clock moved forward 24 hours, the first version showed two
    definition faults. Both were corrected in the spec amendment.
  - Final output on real data: every zero names its cause (no matched
    games, no fee table, grader never run, nothing checked, odds not
    configured). A second run the same day wrote `_2` and left the first
    report unchanged.
- **NOT CHECKED in reality:** the populated money, closing-price and
  bets-spotted lines. They need an odds key, a fee table and graded rows,
  none of which exist yet. Only Gate 1 covers them.

## 2026-10-22: digest banner and 7:30 AM schedule built (owner-approved Part B); not installed

- **Owner rulings applied:**
  - Banner format with `notify.sh` unchanged.
  - Part B approved, to be installed with the other jobs at merge.
  - The August 20 combo fee date is news reporting, UNCONFIRMED until the
    owner checks kalshi.com/fee-schedule. Recorded as a correction in
    `fee_model_spec.md`, and in the Phase 3 spec, in commits made before
    this code.
- **Built:**
  - The banner in `sports_edge/digest.py`. Line 1 of its output is the
    banner, held to 190 characters and enforced.
  - `scripts/run_digest.sh`: always notifies, is loud on any failure, and
    exits 1.
  - `scripts/launchd/com.quis.sportsedge.digest.plist`, firing at 7:30.
  - The new label in `install.sh` and `uninstall.sh`.
- **Gate 1:**
  - 5 banner tests, including the worst-case length plus notify's suffix
    within the 220-character fallback cut.
  - 8 wrapper tests under `/bin/bash` 3.2.
  - 3 new launchd configuration tests.
  - **14 mutants each killed and reverted byte-identical:** 7 in the
    wrapper, 3 in the plist and installer, 4 in the banner. The config test
    first missed a plist pointing at the wrong wrapper (it checked only that
    the script existed); it was tightened before the mutation run.
  - **Suite:** 215 passed, 1 skipped.
- **Gate 2 (reality):** the real wrapper under `/bin/bash`, on the real
  snapshot data, through `reality_run.py`:
  - **Without `.env`:** exit 1 and a loud alert, "DIGEST FAILED | exit 1:
    .env missing …".
  - **With the test-only `SPORTS_EDGE_NO_ENV_FILE`:** exit 0, posting
    "Sports-edge: no games scored yet · system problems →
    digest_2026-10-22_2.txt".
  - 0 live files changed.
- **Not installed** (owner ruling): `install.sh` was not run.
- **PENDING:** the first 7:30 AM firing (top of this log).
- **NOT CHECKED in reality:** a banner with real numbers. It needs odds, a
  fee table and graded bets.

## 2026-10-22 02:01–02:09 PDT: step 1 under standing approval (batch probe and reality gate)

- VERIFIED (live): `GET /events?tickers=…&with_nested_markets=true` returned
  every requested event for batches of 10, 50, 100 and 200 in one request each
  (0.4–0.6 s). A batch of 400 also returned everything, in 2 requests (the
  client followed the 200-per-page cursor). Events that contain finalized
  markets come back with no status filter. The default batch stays 50.
- VERIFIED (reality_run, scratch folder, live data hashes unchanged): the daily
  cycle exited 0. Tier daily, 8,243 sports events, 93,043 markets, 93,043
  snapshot rows, 22 sports, 1,709 settlements from the scan, 173 Kalshi
  requests, 2 min 56 s.
- VERIFIED: the settlement sweep records when there is something to record.
  The daily run's sweep added 0. A direct check found 216 swept markets known
  to us, 204 already recorded, and the rest settled at or after 09:04:05Z,
  after that sweep ran. The grader's sweep then added 20, plus 3 single
  lookups.
- VERIFIED: the hourly cycle with no matches made 1 Kalshi request (exchange
  status only), did no full scan, wrote 0 snapshot rows, and printed
  "matched events: 0 (no odds matches yet)".
- OBSERVED: the heartbeat module reported NEEDS ATTENTION (exit 1), naming
  odds not configured, fee table missing, 0 matched sports, and "never
  succeeded". The last is because health state is written by the wrapper
  scripts, which were not used in this run, while job_runs shows 2 ok runs.
- PENDING: the measured odds cycle (waiting on the key), the fee table
  (waiting on the PDF), the matched-only fetch with real matches (needs the
  key), and everything time-dependent once launchd is installed.

## 2026-10-22: slate and pre-game alerts, spec committed before code

- **Spec:** `docs/specs/slate_alerts_spec.md`, plus amendments to the
  shadow ledger, odds matching, snapshot, storage, fee and grader specs.
- **GAP_v2 is amended in place** (owner direction). It has written no live
  row: no `data/shadow_ledger.csv` exists, and launchd is not installed.
  The changes:
  - stake $10, so contracts = floor(10 ÷ ask);
  - depth must cover that size, walking the book;
  - a gap ≥ 3¢ after the taker fee;
  - rows only at a check.

  The ledger moves to schema_version 3, because columns were added.
- **VERIFIED (live, 2026-10-22, no credentials):**
  `GET /markets/KXNBAGAME-26OCT22CLEPHI-CLE/orderbook?depth=5` returned HTTP
  200 with `orderbook_fp.yes_dollars` and `no_dollars` bid levels. The best
  No bid (0.58 × 570,333.51) equals the market's `yes_ask_size_fp` at Yes
  ask 0.42.
- **OBSERVED (machine, 2026-10-22):**
  - **Sleep:** after 1 minute on battery, never on AC. It was running on
    battery at the time.
  - **Scheduling a wake:** `sudo -n` asks for a password, so `pmset
    schedule` cannot run unattended.
  - **Replies:** `~/Library/Messages/chat.db` returns "authorization
    denied" without Full Disk Access.
  - **OS:** macOS 13.0.

## 2026-10-22: slate and pre-game alerts (Part A) built; Part B proposed

- **Commits on feat/phase1, specs first:**

  | Commit | What |
  |---|---|
  | `a6a4251` | specs and amendments |
  | `916cc84` | spec amendment from the build |
  | `e8a6486` | code |
  | `ac87d98` | spec amendment from the independent review |
  | `4606d7a` | review fixes, and the Part B proposal |

- **GAP_v2 amended in place** before any live row:
  - stake $10, so contracts = floor(10 ÷ ask);
  - book walk when the best level is short;
  - gap ≥ 3¢ on the unrounded value;
  - rows only for games checked in the same run;
  - ledger schema_version 3.
- **VERIFIED (Gate 1):** 327 passed, 1 skipped. Mutation checks, each
  reverted byte-identical:
  - **Build mutants:** 40 written, 39 killed alone. Mutant 27, which removes
    the group-level "already sent" check, survives because the per-text
    send record (added later) also blocks a resend. Removing both together
    is killed.
  - **Review mutants:** each of the 12 restores a bug found by the
    independent review. All 12 were killed.
  - **One build mutant first survived:** the 20-bet losing-stretch edge was
    tested only at 30 losses. The test now pins exactly 20 and 19.
- **Independent review:** a separate reviewer reported 9 correctness
  findings and 1 lower-priority one. All 10 were reproduced or traced and
  fixed, each with a regression test. Among them:
  - checks re-paid after a partial failure;
  - slots shifting once their first game started, which ran checks up to
    an hour early;
  - an unparseable reply hint for walked prices;
  - unnumbered replies mapped to the wrong pick;
  - the `chat.db` date filter reading every message ever sent.
- **VERIFIED (Gate 2, `scripts/reality_run.py`, real Kalshi, temporary
  folder, 0 live files changed, no failure markers):**
  - **Daily cycle, first run:** 8,323 events and 93,329 markets recorded,
    **0 price rows** (none matched), 0 settlement lookups. The comparable
    earlier run saved 93,043 price rows.
  - **Daily cycle on the fixed code:** 8,300 events, 93,282 markets, 0
    price rows, 0 lookups.
  - **Live book walk:** `KXNBAGAME-26OCT22CLEPHI-CLE` filled
    `0.42×627067.69;0.43×4.31`. The best ask equals the snapshot's Yes ask.
    An order larger than the book returns no fill.
  - **The slate's alert, morning and wake commands** on that real data. The
    morning names its causes ("nothing checked (odds not set up)").
    **Corrected by this gate:** the first morning printed "0 checks, 0
    price gaps" and "no matched games yesterday", which are zeros from a
    path that could not produce anything (rule 8). Fixed before commit,
    with tests.
  - **Real `~/Library/Messages/chat.db`:** read through the real code, it
    failed with "unable to open database file". The bet log is reported
    "unreadable", as specified.
- **VERIFIED (positive control):** the reality runner's new iCloud
  sheet-folder hash catches a file written there (tested on a scratch
  copy).
- **NOT CHECKED in reality:**
  - an iMessage actually sent (no `IMESSAGE_TO`; log mode only);
  - reading real replies, including the `attributedBody` decoding (no Full
    Disk Access);
  - `pmset` wake (needs root);
  - any odds call, check, pick, combo or stage above Practice (no odds
    key, no fee table).

  Gate 1 covers all of these.
- **OBSERVED:** the live checkout `/Users/quis/sports-edge` has no `data/`,
  `logs/`, `state/` or `.env`. The live-data hash therefore compared 0 files
  before and after. It would still catch any file created there.

## 2026-10-22: owner rulings before merge (items 2–8) and Part B built

- **Correction (leading, rule 10):** the earlier run report and specs listed
  the protected files from `main`'s AGENTS.md. On feat/phase1 the list was
  narrowed in `a843253`:
  - **Protected on this branch:** `ledger`, `fees`, `grader`, `shadow`,
    `matching`, and the three config tables.
  - **Of the files this work changed:** only `shadow.py`, `fees.py` and
    `grader.py` are protected.
  - **Now added:** `checks.py` (owner ruling 8).
  - Recorded in the slate spec and the Part B proposal.
- **Built** (spec `7131c7e` and correction `c32c0a6`, then the code in
  `dcd0f04`):
  - replies read from `iCloud Drive/sports-edge/replies.txt`; the
    `chat.db` code was removed;
  - the daily cap counts money at risk;
  - a same-result pair is shown once, the cheaper;
  - the line wording depends on how many times the sharp book was read;
  - free-plan soccer is EPL, La Liga and the Champions League;
  - Part B without the wake: `run_job` passes arguments, the 15-minute job
    always runs `slate alert`, and an 8:00 `run_morning.sh` replaces the
    digest job.
- **Found by the reality gate, fixed:**
  - **Unmatched replies:** the spec said an unclear reply makes the next
    message ask for the pick number, but no code did it. Now a message is
    sent once per such reply, and the morning message lists it.
- **VERIFIED (Gate 1):** 337 passed, 1 skipped. Mutation checks, each
  reverted byte-identical:
  - **Rulings and wrappers:** 17 of 17 killed.
  - **Unclear-reply message:** 1 of 1 killed.
  - **Earlier sets rerun:** 36 of 40 build mutants and 10 of 12 review
    mutants applied and were killed.
    - The survivor is build mutant 27 (the group-level "already sent"
      check), which is backed up by the per-text record. Removing both is
      killed.
    - Three stale patterns were re-aimed at the current code, and all three
      were killed.
    - Two cover code that no longer exists: the old cap formula and the
      `chat.db` date filter.
- **VERIFIED (Gate 2, `reality_run.py`, real Kalshi, real `/bin/bash`
  wrappers, temporary folder, 0 live files changed, no failure markers):**
  - **`run_snapshot.sh`:**
    - the daily scan recorded 8,267 events and 94,257 markets, with 0 price
      rows and 0 lookups;
    - `slate alert` then ran;
    - `health.json` recorded `snapshot` and `slate_alert` as succeeded.
  - **`run_morning.sh` with a `replies.txt`:**
    - the file was read and left unchanged;
    - `skip` was logged once (as `unclear`; no picks exist), and a
      non-reply line was ignored;
    - after a second run, the morning text named the unmatched `took 41`;
    - the sheet shows "money at risk today $0 of $30".
- **NOT CHECKED:**
  - the owner's Shortcut, on a phone;
  - an iMessage actually sent;
  - any odds call, check or pick (no odds key and no fee table on this
    machine).

## 2026-10-22: owner-approved cuts A–D

- **Commits:** spec `967cb8f`, then code `95785c2`.
- **Removed:**
  - `digest.py` and its tests;
  - the combo line and the fee table's `combo` key (parked);
  - the sheet's yesterday and how-to sections.
- **Archived:** the digest and combo-logger specs, moved to `docs/archive/`.
- **`slate.py`:** 763 → 718 lines.
- **VERIFIED (Gate 1):** 318 passed, 1 skipped. The 19 fewer than before are
  the removed digest and combo tests.
- **Mutation baseline after the cuts:** 66 of 66 killed, each reverted
  byte-identical.
  - The sets: build 36, review 12, rulings 18.
  - The patterns for removed code were dropped, and the re-aimed ones
    written in.
  - The mutant-27 check (the group-level "already sent" guard together with
    the per-text record) is now a standing double mutant.
- **VERIFIED (Gate 2, real `/bin/bash` wrappers, real Kalshi, temporary
  folder, 0 live files changed, no failure markers):**
  - `run_snapshot.sh`: an hourly run (1 Kalshi request, nothing matched),
    then `slate alert`.
  - `run_morning.sh`: printed `SLATE MORNING OK sheet=…` and wrote no
    `digest_*.txt`. The two that exist in the folder are from runs before
    the cut, at 04:50 and 06:04.

## 2026-10-22: slate.py audit applied (owner-approved MOVE/CUT plan, option b)

- **Commits:** spec `5096b94`, then the code.
- **`slate.py`:** 718 → 500 lines. The audit estimated about 480; the
  measured result is 500, because the moved functions leave call sites and
  arguments behind.
- **Moved:**

  | To | What |
  |---|---|
  | `grader.py` | stage line, paper results |
  | `shadow.py` | pay up to |
  | `fees.py` | table state |
  | `checks.py` | 7 check and plan readers |
  | `bets.py` | result pairing, picks in a run, open picks, money at risk |
  | `store.py` | run finishes, newest snapshot |
  | `timeutil.py` | Pacific day |

- **Cut:** two unused Context fields, and the repeated fee and odds checks.
- **VERIFIED (Gate 1):**
  - 318 passed, 1 skipped, as before the move.
  - The test diff renames call sites only (`slate.stage` →
    `grader.stage`, `slate.pay_up_to` → `shadow.pay_up_to`) and moves the
    `JARGON` list into the test. No assertion or expected value changed.
- **Mutants: 66 of 66 killed**, each reverted byte-identical.
  - Ten patterns were re-pointed to the moved code, each with the same
    mutation against the same test file.
- **VERIFIED, no behaviour change, on real data:**
  - The old code (`e0bb32d`) and the new code ran `slate show` and
    `slate morning`, at the same fixed time, on separate copies of the
    reality-run data.
  - Output, sheet and bet log were identical.
  - **Positive control:** a one-character change to the sheet header was
    detected (2 differing lines).
- **VERIFIED (Gate 2):** real `run_snapshot.sh` (snapshot, then
  `slate alert`) and `run_morning.sh` under `/bin/bash`, against real
  Kalshi, in a temporary folder. 0 live files changed, no failure markers.

## 2026-10-22: correction on mutation results, then owner review items 1–2

- **Correction (leading, rule 10):** every mutation count reported earlier
  this day (66 of 66, and the earlier sets) came from a harness that could
  run stale bytecode, so those counts were not certified.
  - **What happened:** a mutant written, run and then restored within the
    same second, at the same file size (`min` → `max` in `bets.cheaper`),
    left a `__pycache__` file that Python still trusted.
  - **How it was found:** two tests failed on the committed code. The cached
    `bets.cheaper` was confirmed to still call `max`. Clearing the caches
    made them pass.
  - **The risk:** a stale mutant could make a later mutant look killed.
  - **The fix:** the harness now deletes every `__pycache__` before applying
    and after restoring each mutant, and runs pytest with
    `PYTHONDONTWRITEBYTECODE=1`.
  - **Rerun from scratch:** **71 of 71 killed**, each restored
    byte-identical. The sets: build 40, review 13, rulings 18. No cache was
    left behind, and the suite passed right after.
- **Item 1:** `checks.latest_starts` (now one `arg_max` per game within the
  source) and `checks.checked_slots` read only the given odds source.
  - **Test:** a second source with newer rows is ignored.
  - **Mutants:** removing either filter is killed.
- **Item 2:** confirmed in the code. `walk_book` charges `fee_for_order`
  per level (each level's price × contracts), sums those fees, and returns
  the average fill price. `evaluate` computes the gap on that average and
  that summed fee per contract.
  - **New test:** a two-level book, 10 at 0.42 then 13 at 0.44. Fees:
    0.3949 per level, against 0.3922 as one order at the best price and
    0.3950 as one order at the average. The row carries the per-level
    figure and the average price.
  - **Mutants, all killed:** fee at the best price; one fee at the average
    price; gap on the best price.
- **VERIFIED (Gate 1):** 320 passed, 1 skipped.
- **VERIFIED, no behaviour change on real data, rerun from clean caches:**
  the old code (`e0bb32d`) and the new code gave identical output, sheet
  and bet log.
- **VERIFIED (Gate 2):** real `run_snapshot.sh` and `run_morning.sh` under
  `/bin/bash`, against real Kalshi, in a temporary folder. 0 live files
  changed, no failure markers.

## 2026-10-22: grader fixes from owner review (closing line, clv after fees)

- **Spec `13f6537` (before the code):**
  - The closing reference must be strictly after the row's `snapshot_utc`
    **and** its own `odds_fetched_utc`, and before the start; otherwise
    `clv` is blank.
    - **Why both:** on a daily run the snapshot comes from the scan that
      precedes the odds read, so "after the snapshot" alone would let the
      entry read serve as its own close.
  - `clv` = closing side fair − price paid − fee per contract.
  - The stage counts only games with a closing price.
  - **The free plan reads each game once,** so it never produces a closing
    price. Its Practice line says "(free plan adds none)", per rule 8.
  - **Accepted note:** a walked fill may cost over $10, while the cap counts
    the nominal $10.
- **VERIFIED (Gate 1):** 324 passed, 1 skipped. New tests:
  - a game read once has a blank `clv`;
  - the row's own read, coming after its snapshot, is not a close;
  - a 1¢ price beat is −0.72¢ after the fee;
  - 150 graded games with no closes stay in Practice (2 positive closes do
    not promote, and 2 negative closes do not demote);
  - the free-plan wording.
  - The alert-length worst case now uses the longest possible stage line
    (69 characters), and the compact form still fits.
- **Mutants, from clean caches: 77 of 77 killed** (build 46, review 13,
  rulings 18), each restored byte-identical, with no caches left behind. Six
  new ones:
  - the entry read allowed as the close (`>` → `>=`);
  - the close measured from the snapshot only;
  - `clv` without the fee;
  - promotion counting all graded games;
  - demotion counting all graded games;
  - the free-plan note dropped.
- **VERIFIED (Gate 2):** real `run_grader.sh`, `run_snapshot.sh` and
  `run_morning.sh` under `/bin/bash`, against real Kalshi, in a temporary
  folder. 0 live files changed, no failure markers.
  - The grader swept 2,297 settlements and printed the taken and not-taken
    lines.
  - The morning showed "Practice · 0 of 100 games with a closing price
    (free plan adds none)".
- **NOT CHECKED in reality:** the closing line on real reads. That needs an
  odds key and two reads of one game, which the free plan never makes.

## 2026-10-22: slip rating (`sports_edge/rate.py`), display only

- **Spec `d304aaa`.** The owner's 9-leg NBA slip is pinned verbatim as
  `tests/fixtures/slip_nba_9leg.txt`. This does not unpark the Phase 3
  combo logger: nothing about a slip is stored.
- **Design choice (flagged to the owner):** the one pull per sport that
  `rate` may make is **not** stored in `reference_odds`. That keeps a rating
  from becoming a game's closing price. The call is logged in `odds_calls`
  for the budget.
- **OBSERVED (live Kalshi, 2026-10-22):** NBA game markets carry the team
  code as the ticker suffix (`KXNBAGAME-26OCT22CLEPHI-PHI`), and San Antonio
  is `SAS` where the slip says `SA`. Hence the second pairing rule: the slip
  name equals the end of the matched sharp-book name.
- **VERIFIED (Gate 1):** 354 passed, 1 skipped.
  - **The slip:** read exactly, and 9 off-format variants stop with exit 2.
  - **The markup line on the real slip:** 1.93% against 1.64%, 17% more,
    1 in 61.
  - **Grades:** the band edges, and all five letters through the real fee
    function.
  - **Pairing:** SA Spurs pairs by name, and two fitting games pair neither.
  - **The stale-read pull:** one call per sport, not stored, and it counts
    against the budget.
  - **Other cases:** "Can't rate: no odds credits left today"; no fee
    table; a game already started; the combo fair line.
- **Mutants, from clean caches: 91 of 91 killed**, each restored
  byte-identical.
  - The sets: build 60, review 13, rulings 18.
  - The 14 new ones cover the grade edges, the fee in the gap, the
    name-pairing rule, ambiguity, the 15-minute limit, the budget, strict
    reading, the markup direction, "1 in N", the game-started check, the
    reason order, and the shared budget helper in both directions.
- **VERIFIED (Gate 2, `reality_run.py`, 0 live files changed):**
  - **The real command on the real slip:** printed every line. The
    moneyline legs said "odds not set up", which is true on this machine.
  - **The pairing probe on real Kalshi markets:** CLE–PHI, DEN–OKC,
    DAL–IND, MIN–ORL and TOR–WAS paired with the right events by code.
    HOU–SA did not; it needs the sharp-book name, which needs an odds key.
- **NOT CHECKED in reality:** a real fair chance, a real grade or a real
  pull. These need an odds key, a fee table and matched games.
- **PENDING, after merge (owner ruling):** the user-level `/rate-slip`
  skill.

## 2026-10-22: feat/phase1 history rewritten (owner ruling)

- **What changed:** the attribution trailer line was removed from the 22
  commit messages that carried it. Nothing else changed:
  - every commit's tree is byte-identical, in order (48 of 48);
  - the final tree is `3ff3622a13c7b732d48469889c63687d9da49d13` before and
    after;
  - authors, committers, dates and every other message line are unchanged;
  - `main` is untouched (`08a6bf8`).
- **Heads:** old `a732499918456b0bdf4dffb52430e4d5853cdc8c`, new
  `8fbefc4e80cdbab5a23543d538514a6325b094fb`.
- **Push:** force-pushed to `origin/feat/phase1` only, with a lease on the
  previous remote head `8a8501e`.
- **Commit ids cited earlier in this log and in the specs are the old
  ones.** The map from old to new, oldest first:

| Old | New |
|---|---|
| `df857d7` | `df857d7` |
| `52b1a11` | `52b1a11` |
| `da878bc` | `da878bc` |
| `0e0316a` | `0e0316a` |
| `57108f0` | `57108f0` |
| `76e50dd` | `76e50dd` |
| `6438992` | `6438992` |
| `9530fbb` | `9530fbb` |
| `a843253` | `a843253` |
| `c81ed81` | `c81ed81` |
| `ea2093e` | `ea2093e` |
| `ecf3d71` | `ecf3d71` |
| `830a1d0` | `830a1d0` |
| `5870c81` | `5870c81` |
| `50de6e6` | `50de6e6` |
| `a7c5e1c` | `a7c5e1c` |
| `cfcd6b6` | `cfcd6b6` |
| `fa14514` | `fa14514` |
| `8831895` | `8831895` |
| `be9eee1` | `be9eee1` |
| `0565e46` | `0565e46` |
| `23f0dd1` | `23f0dd1` |
| `55e192c` | `55e192c` |
| `a045cab` | `a045cab` |
| `a6a4251` | `87f359f` |
| `916cc84` | `ac28dc4` |
| `e8a6486` | `b42711b` |
| `ac87d98` | `b78f494` |
| `4606d7a` | `8d43e75` |
| `6233c50` | `b8a874b` |
| `7131c7e` | `f8283c2` |
| `c32c0a6` | `86a6e06` |
| `dcd0f04` | `5abdca8` |
| `251e5f4` | `f744b79` |
| `967cb8f` | `8ace043` |
| `95785c2` | `cbbadd7` |
| `e0bb32d` | `7ffe6ad` |
| `5096b94` | `8d083c7` |
| `1029414` | `ec04540` |
| `745609e` | `629713f` |
| `d6ad729` | `111acd2` |
| `c76d49b` | `2bb77de` |
| `79aa925` | `bc2a244` |
| `13f6537` | `621f37f` |
| `42f744e` | `c03bcc3` |
| `8a8501e` | `f58a409` |
| `d304aaa` | `82428ab` |
| `a732499` | `8fbefc4` |

## 2026-10-22: live odds gate (steps 1–2), matching fixes, and the merge waiver

**Merge to main approved by owner before the final live-gate step (3:41 PM PT check). Two of three gate steps passed live (matching, paid odds call). The remaining step runs after merge; install.sh is held until it passes.**

- **Setup:** the odds key is in `/Users/quis/sports-edge/.env` (gitignored, permissions 600). Every run used `reality_run.py` with a temporary data folder, a cap of `ODDS_DAILY_CREDITS=20`, and nothing sent.
  - **Checked in every run:** 0 live files changed, and the key appears in no output or log.
- **VERIFIED, step 1 (real daily cycle with matching, 19:39Z):**
  - **Kalshi:** 8,663 events and 99,539 markets.
  - **Free calls only:** 9 calls, 0 credits.
  - **Pairing:** 16 games paired correctly in NBA, NHL, NFL, college football, EPL and La Liga, out of 741 Kalshi games considered. Checks due: 0.
  - **The 304 unmatched games:** mostly leagues not followed, or games beyond the 24-hour listing. Four of the day's followed games failed on names alone: NHL `New York R` and `New York I`, and college football `Appalachian St.` and `Kennesaw St.`
- **VERIFIED, step 2 (the first measured paid call, 19:45Z):**
  - **The call:** `rate` on the owner's slip made one real NBA pull. The reported cost was 1 credit, with 498 left on the account (1 had been used before the gate).
  - **Fair chances:** PHI 56.0% (slip 57%) and OKC 72.8% (slip 74%).
  - **Not stored:** `reference_odds` stayed at 0 rows, as specified.
- **Fixes from step 1, owner-approved, spec `2120124` and code `d87f5f3`:**
  - **Aliases:** `New York R` → Rangers and `New York I` → Islanders.
  - **"St." reads as "State":** rule version `match_v2`.
  - **The unmatched log:** one line each only for in-scope games within the odds window; the rest as one count per reason.
  - **Tests:** each fix pairs the right game; "Kansas" against "Kansas Jayhawks" and "Kansas State Wildcats" still refuses.
  - **Suite:** 362 passed, 1 skipped.
  - **Mutants:** 98 of 98 killed, from clean caches.
- **OBSERVED (live Kalshi API, 2026-10-22):** every followed game series is `quadratic_with_maker_fees`, multiplier 1. `KXMLBGAME` and 18 other MLB series are 0.5. The fail-closed cross-check therefore prices a followed series only once the fee table, cited to a page, gives it a maker multiplier above 0.
- **PENDING:**
  - the 3:41 PM PT check (step 3), run against `main` after the merge;
  - the fee table, waiting on the owner's two source files in `docs/sources/`;
  - `install.sh`, held until step 3 passes and the owner says "install".

## 2026-10-22: owner waiver, launchd install, and the missing venv

**Owner waiver:** install now, before the 3:41 PM PT check. The scheduled
runs from here on are the live gate, and failures alert on their own.

- **The live checkout had no `venv/`.** `scripts/common.sh` runs
  `$PROJ/venv/bin/python`, so every wrapper would have failed to start.
  - **Fix (owner-directed, ~2:00 PM PT):** created
    `/Users/quis/sports-edge/venv` with Homebrew Python 3.14.7 (the same
    interpreter as the phase1 worktree's venv) from `requirements-dev.txt`.
    Packages match the phase1 venv: duckdb 1.5.6, pytest 9.1.1, iniconfig
    2.3.1, packaging 26.3, pluggy 1.7.0, Pygments 2.21.0. `venv/` is
    gitignored.
  - **OBSERVED:** the suite in the live checkout with the new venv: 362
    passed, 1 skipped, with no cache files left.
  - **OBSERVED:** `common.sh` sourced without `SPORTS_EDGE_PYTHON` resolves
    `PY=/Users/quis/sports-edge/venv/bin/python` (Python 3.14.7, duckdb
    1.5.6). Neither `.env` nor the shell sets `SPORTS_EDGE_PYTHON`.
- **OBSERVED, install (2:01 PM PT):** `scripts/launchd/install.sh` exited 0
  and printed `loaded` for all four labels. `launchctl list` shows all four,
  and `launchctl print` shows each plist in `~/Library/LaunchAgents`,
  working directory `/Users/quis/sports-edge`, `runs = 0`. The system time
  zone is America/Los_Angeles.
- **Next firings (PT), read from the plists:**

| Job | Schedule | Next |
|---|---|---|
| `snapshot` (then `slate alert`) | :00, :15, :30, :45 | Thu Oct 22, 2:15 PM |
| `grader` | 3:30 AM daily | Fri Oct 23, 3:30 AM |
| `morning` | 8:00 AM daily | Fri Oct 23, 8:00 AM |
| `heartbeat` | Sundays 9:00 AM | Sun Oct 25, 9:00 AM |

- **No iMessage can be sent.** `IMESSAGE_TO` is not set in `.env` or the
  shell. `imessage.send` raises `SendFailed` before `osascript` runs (code
  read, INFERRED for the live jobs). The consequences, by design (rule 7):
  - **Slate alert:** any `slate alert` run that has a message to send fails.
    `run_snapshot.sh` then exits nonzero and a loud Mac notification fires.
    While there is no fee table, that is expected about once per newly
    checked slot ("no fee table").
  - **Morning job:** fails at 8:00 AM with a loud `MORNING FAILED`.
  - **Failure alerts:** Mac notifications (terminal-notifier or
    `osascript display notification`), not iMessages, so they still arrive.
- **PENDING:**
  - the first scheduled snapshot run (2:15 PM PT) and the 3:41 PM PT check
    on the installed jobs;
  - the first grader, morning and heartbeat firings above;
  - the iMessage items at the top of this log, until `IMESSAGE_TO` is set.

## 2026-10-22: delivery without iMessage (owner request; not a protected file)

Spec `29504c7`, code `d7da9e1`, on `feat/notify-delivery`, merged before
`feat/board` as directed.

- **VERIFIED, Gate 1:**
  - Suite: 368 passed, 1 skipped. Six tests were added, and one ("missing
    number fails loudly") was replaced, because the rule is reversed.
  - Delivery mutants: 10 of 10 killed, from clean caches, each restored
    byte-identical. They include the two the owner named: a missing number
    fails the job, and an alert is dropped instead of shown.
  - Standing set rerun on this branch: 98 of 98 killed.
- **OBSERVED, Gate 2** (`reality_run.py`, wrapper code from the branch,
  copy of the live data; 0 live files changed in both runs):
  - **Log mode:** `run_morning.sh` exited 0 with `SLATE MORNING OK`.
  - **One real notification** (`SPORTS_EDGE_NOTIFY=on`, no `IMESSAGE_TO`):
    `run_morning.sh` exited 0. `alerts.log` shows `SLATE terminal-notifier:`
    with a banner of exactly 300 bytes ending `Yesterday:… → sheet.txt`.
    `imessage.log` shows `notified (IMESSAGE_TO not set)`.
- **PENDING:**
  - the owner seeing that banner (about 2:45 PM PT) and confirming a click
    opens a sheet;
  - a click opening the real iCloud sheet, whose path contains a space
    ("Mobile Documents");
  - the first live slate banner after merge. On the free plan with no fee
    table, that is "Don't bet today: prices not checked (no fee table)." at
    the first check of the day;
  - the 8:00 AM PT morning banner on Fri Oct 23, from the installed job.

## 2026-10-22: the reality gate runs the `--code` checkout in wrappers too (owner-approved fix)

Spec `6138fbb` (new `docs/specs/reality_gate_spec.md`), code `eb6a198`.

- **VERIFIED, Gate 1:**
  - Suite: 372 passed, 1 skipped (4 new tests).
  - Mutants: 6 of 6 killed, from clean caches, each restored
    byte-identical: never set, set for the live checkout too, not applied,
    each of the three settings missing or wrong.
- **VERIFIED, Gate 2, with a control that fails without the fix.** The same
  command was run with no manual settings:
  `--code <feat/board worktree> --with-live-data -- /bin/bash <worktree>/scripts/run_grader.sh`.
  - **Fixed `reality_run.py`:** the report shows
    `code path: …sports-edge-board (wrappers run …sports-edge-board)`. The
    grader log has `markouts_measured=0` and
    `markouts: 0 measured (no shadow rows logged yet)`, so `feat/board`'s
    code ran.
  - **Old `reality_run.py` (`main`):** no markouts field or line; `main`'s
    code ran.
  - **Both:** 0 live files changed, no failure markers, exit 0.
- **NOT CHECKED:** which code earlier branches' wrapper gates ran.

## 2026-10-22: a daily run prices the games it matches (owner-approved fix)

Spec `756681f`, code `0e3e69d`. `snapshot.py` is not on the narrowed
protected list.

- **VERIFIED, Gate 1:**
  - Suite: 375 passed, 1 skipped (3 new tests).
  - Mutants: 4 of 4 killed, from clean caches, each restored
    byte-identical. One of them restores the old behaviour (no matched
    fetch after the scan); the new tests fail on it.
  - Standing set rerun on this branch: 98 of 98 killed.
- **VERIFIED, Gate 2,** with the fixed `reality_run.py` and no manual
  settings: `--code <worktree> -- /bin/bash <worktree>/scripts/run_snapshot.sh`
  on an empty folder (3:01 PM PT).
  - **Result:** `tier=daily matches_added=44 events_matched=21
    matched_after_scan=21 snapshot_rows=44`. 44 price rows (tier `daily`),
    and 0 matched markets without a price row. `SLATE ALERT OK`.
  - **Control (the same setup on the old code, 2:20 PM PT gate, and the
    live 2:15 PM PT run):** `snapshot_rows` was 0.
  - 0 live files changed, no failure markers.
- **PENDING:** the next live daily run (3:00 AM PT Fri Oct 23) writing price
  rows for any game it matches.

## 2026-10-22: board and markouts specs (feat/board), and the live venv

- **Specs committed before any code:** `c150e91` (the board, near misses,
  older checks, record-only markouts, `data/shadow_markouts.csv`) and
  `f148864` (board gaps under the daily cap are listed without numbers).
  All other spec choices were approved by the owner.
- **Accepted for now (owner ruling):** markouts use the game's latest listed
  start (`checks.latest_starts`). The grader's closing-line value uses the
  start stored on the row (`commence_utc`). The two differ only when a start
  moves by under 60 minutes. A move of 60 minutes or more makes the row
  postponed, and markouts skip it.
- **The live checkout had no `venv/`** before the 3:41 PM PT check.
  `scripts/common.sh` runs `$PROJ/venv/bin/python`, so every wrapper would
  have failed to start.
  - **Fix (owner-directed, ~2:00 PM PT):** created
    `/Users/quis/sports-edge/venv` with Homebrew Python 3.14.7 (the same
    interpreter as the phase1 worktree's venv) from `requirements-dev.txt`.
    Packages match the phase1 venv: duckdb 1.5.6, pytest 9.1.1, iniconfig
    2.3.1, packaging 26.3, pluggy 1.7.0, Pygments 2.21.0. `venv/` is
    gitignored, and `main` stayed clean at `8220c82`.
  - **OBSERVED:** the suite in the live checkout with the new venv: 362
    passed, 1 skipped. No cache files were left.
  - **OBSERVED:** `common.sh` sourced without `SPORTS_EDGE_PYTHON` resolves
    `PY=/Users/quis/sports-edge/venv/bin/python`, which runs Python 3.14.7
    with duckdb 1.5.6. Neither `.env` nor the shell sets
    `SPORTS_EDGE_PYTHON`.
  - **PENDING:** the 3:41 PM PT check itself, run with this venv.

## 2026-10-22: the board and markouts built (feat/board); grader.py held for owner review

**Owner waiver:** Step 2 started after the install, before the 3:41 PM PT
check. Code `6405d61` (the board) and `b0c3d16` (markouts, `grader.py`),
built to specs `c150e91` and `f148864`. Who-moved-first was dropped by the
review and is not built.

- **VERIFIED, Gate 1:**
  - Suite: 389 passed, 1 skipped (27 new tests).
  - New mutants: 35 of 35 killed, from clean caches, each restored
    byte-identical.
  - Standing set rerun on this branch: 98 of 98 killed (65 + 13 + 20).
- **OBSERVED, Gate 2** (`scripts/reality_run.py --code <worktree>`, live
  Kalshi API; 0 live files changed and no failure markers in every run):
  - `slate board` on a copy of the live data printed
    `BOARD (price gaps as of each game's check, 2:19 PM PT)` and
    `none (no fee table)`.
  - `run_grader.sh` on a copy of the live data: `GRADER OK … markouts_measured=0`
    and `markouts: 0 measured (no shadow rows logged yet)`.
  - `run_snapshot.sh` on an empty folder: `SNAPSHOT OK` (99,653 markets, 21
    games matched), then `SLATE ALERT OK messages=0`. The sheet opens with
    the BOARD, reading `none (no fee table)`.
- **Finding: `--code` alone does not change the code a wrapper runs.**
  `common.sh` changes into `/Users/quis/sports-edge` before `python -m`, and
  Python puts the current folder ahead of `PYTHONPATH`. The first grader
  gate run therefore ran `main`'s code: its output had no markouts line.
  The runs above set `SPORTS_EDGE_PROJ=<worktree>`,
  `SPORTS_EDGE_PYTHON=/Users/quis/sports-edge/venv/bin/python` and
  `SPORTS_EDGE_NO_ENV_FILE=1`; the markouts line then appeared. Wrapper
  gates on earlier branches may have run `main`'s code (NOT CHECKED).
- **Finding: a first daily run stores no prices for markets it matches.**
  The empty-folder run above matched 44 markets and wrote 0
  `price_snapshots` rows. The live 2:15 PM PT run matched the same 44. The
  snapshot spec says a game matched in a run is snapshotted in that run.
  PENDING: the 2:30 PM PT live run's price rows for these markets.
- **PENDING:**
  - the first real board gap (needs the fee table);
  - the first real near miss on the board;
  - the first markout row in `data/shadow_markouts.csv` (needs a logged
    row whose game has started);
  - owner review of `grader.py` before merge.

## 2026-10-22: grader.py approved; feat/board merged to main

- **Owner review:** `grader.py` on `feat/board` (markouts, `b0c3d16`) was
  approved by the owner after the branch was pushed to `origin`.
- **Merged** into `main` with a merge commit. Every validation-log entry
  from both sides is kept (resolved earlier in `d285ccf`).
- **Gate 2 on the merged code** (`d285ccf`, fixed `reality_run.py`, 3:06 PM
  PT): `slate board` printed `none (no fee table)`, and the grader wrapper
  printed `markouts: 0 measured (no shadow rows logged yet)`. 0 live files
  changed, no failure markers.
- **PENDING:** the first scheduled run on the merged code. The sheet should
  open with the BOARD, and the 3:30 AM PT grader should print the markouts
  line.

## 2026-10-22: first scheduled run on the merged board and notification delivery (3:30 PM PT)

- **OBSERVED, the live 3:30 PM PT run on `main` `a1ebb2b`:**
  - **Snapshot:** exit 0, `tier=near`, `checks_due=9`, `events_priced=0`
    (no fee table, so nothing is priced).
  - **Slate alert:** `SLATE ALERT OK messages=1`, exit 0. The message was
    delivered as a Mac notification, not an iMessage. `alerts.log`:
    `SLATE terminal-notifier: Don't bet today: prices not checked (no fee table). · Practice · … → sheet.txt`.
    `imessage.log`: `notified (IMESSAGE_TO not set)`.
  - **The live iCloud sheet** opens with
    `BOARD (price gaps as of each game's check, 3:30 PM PT)` and
    `none (no fee table)`.
- **Cleared:** the first live slate banner after merge (PENDING in the
  delivery entry), and the sheet opening with the BOARD on a scheduled run
  (PENDING in the merge entry).
- **Still PENDING:** the owner confirming that clicking a banner opens the
  iCloud sheet (path with a space); the 3:30 AM PT grader's markouts line;
  the 8:00 AM PT morning banner.

## 2026-10-22: the status command, and `.env` for every command (owner request)

Spec `b91f2bc`, code `7fc18fd`. No protected file is changed.

- **VERIFIED, Gate 1:**
  - Suite: 415 passed, 1 skipped (13 new tests). `tests/conftest.py` now
    also sets `SPORTS_EDGE_NO_ENV_FILE` for every test, so no test can load
    the real `.env`.
  - New mutants: 17 of 17 killed, from clean caches, each restored
    byte-identical. They cover commands never loading `.env`, any import
    loading it, an already-set value replaced, the skip switch ignored,
    `SPORTS_EDGE_PROJ` ignored, quotes kept, the `export` prefix, a
    read-write database, and each status line rule.
  - The board and markout set (35) and the standing set (98) were rerun on
    this branch: all killed.
- **VERIFIED, `.env` loading, with a control.** Run through
  `reality_run.py` on a copy of the live data, with `ODDS_API_KEY` removed
  from the environment and `SPORTS_EDGE_PROJ` set to the live checkout:
  - `slate board` printed `none (no fee table)`: the key came from `.env`.
  - **Control,** with `SPORTS_EDGE_NO_ENV_FILE=1`: `none (odds not set up)`.
- **OBSERVED, `status` on the live data from a clean environment (4:08 PM
  PT):** exit 0, no stderr, 12 live files hashed before and after, 0
  changed (the database file included). Output:
  ```
  SPORTS-EDGE · 4:08 PM PT
  Running fine · last check 4:00 PM PT
  Today: 18 games tracked · 0 checked · 0 picks
  Why no picks: no fee table
  Odds credits: 0 of 15 used today
  ```
- **OBSERVED, `run_snapshot.sh` on this branch** (empty folder, 4:09 PM
  PT): `SNAPSHOT OK tier=daily events_matched=28 matched_after_scan=28
  snapshot_rows=58`, then `SLATE ALERT OK`. 0 live files changed, no
  failure markers.
- **Owner's header:** line 1 is `SPORTS-EDGE`, as specified. The jargon
  test exempts only that product name.
