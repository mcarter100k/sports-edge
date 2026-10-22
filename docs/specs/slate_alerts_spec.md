# Spec: the slate, pre-game checks and alerts (owner request 2026-10-22)

**Status:**
- **Part A (built on `feat/phase1`):** the Python side. That covers the
  check schedule, the GAP_v2 amendment, the slate view, the messages, bet
  logging and the stage line.
- **Part B (PROPOSED, protected, not built):** the wrappers and plists
  that run Part A on a schedule, plus the machine permissions it needs.
  These go to the owner for review.

Read-only throughout: nothing here places an order, creates an RFQ or
authenticates to a trading endpoint.

Related amendments, all dated 2026-10-22:
- `shadow_ledger_spec.md`: GAP_v2 changes (stake, depth, threshold, rows
  only at a check).
- `odds_matching_spec.md`: sport scope, check schedule and credit planner,
  `reference_events`.
- `snapshot_job_spec.md`: lean cleanups.
- `storage_spec.md`: two new tables.
- `fee_model_spec.md`: the combo fee key.
- `grader_spec.md`: taken and not-taken bets.

---

## Owner decisions this implements

| Decision | Value |
|---|---|
| Practice stake | $10 per bet |
| Daily loss cap | $30 |
| Sports | NFL, college football, NBA, NHL, European soccer, MLB. MLB is in scope but unpriced while its fee is unconfirmed, and no odds credits are spent on it |
| Markets | Moneyline only, pre-game only. No tennis |
| Delivery | iMessage to the owner's own number (`osascript`); full sheet in iCloud Drive at `~/Library/Mobile Documents/com~apple~CloudDocs/sports-edge/` |
| Odds plan | `ODDS_PLAN=free` (default) or `paid`. One setting switches the schedule |

## Sport scope (`config/slate_sports.json`)

The file maps Odds API sport keys to the league label shown in messages. A
key that is not in the file is never listed, matched or priced. The keys
were read from the-odds-api.com/sports-odds-data/sports-apis.html on
2026-10-22.

| Sport | Keys |
|---|---|
| NFL | `americanfootball_nfl` |
| College football | `americanfootball_ncaaf` |
| NBA | `basketball_nba` |
| NHL | `icehockey_nhl` |
| MLB | `baseball_mlb` |
| European soccer | `soccer_epl`, `soccer_spain_la_liga`, `soccer_germany_bundesliga`, `soccer_italy_serie_a`, `soccer_france_ligue_one`, `soccer_netherlands_eredivisie`, `soccer_portugal_primeira_liga`, `soccer_uefa_champs_league`, `soccer_uefa_europa_league`, `soccer_uefa_europa_conference_league` |

The owner can edit the list. A key in the file that the API does not list as
active is printed once per run as `SLATE SPORT NOT LISTED <key>`, so a typo is
visible rather than silent.

**MLB, and any series whose fee is unknown, spends no credits.** Before a
check spends a credit on a game, at least one of the game's matched markets
must resolve a fee through `fees.resolve_multipliers`. If none does, the
check is logged as `fee_unknown` and nothing is fetched. This is the
existing fail-closed fee rule applied before the spend, not a separate MLB
rule. While `config/fee_schedule.json` does not exist, no game can be
priced and no credit is spent.

## Kickoff clusters and check times

- **Slate:** the in-scope matched games, each with its start time from the
  latest `reference_events` row.
- **Cluster:** sort the games by start. The first game anchors a cluster,
  and every game starting no more than **30 minutes** after the anchor
  joins it. The next game after that anchors the next cluster. The
  cluster's start is the anchor's start.
- **Check offsets** (minutes before the cluster start):

| Plan | Offsets | Daily credit budget (`ODDS_DAILY_CREDITS` default) |
|---|---|---|
| free | 30 | 15 (500 a month) |
| paid | 60 and 15 | 600 |

- **When a check is due:** a check of game *g* at offset *o* is due on a
  15-minute run at time *now* if all of these hold:
  - cluster start − *o* ≤ *now* < *g*'s start;
  - `check_log` has no row for (*g*, *o*).

  If more than one offset is due (a run was missed), only the smallest
  offset runs. The others are logged `superseded`.
- **What a check does:** it pulls odds only for the due games, one call per
  sport key (each call costs credits). The shadow step then runs on those
  games with this run's Kalshi snapshot. This removes the hourly pricing of
  every game within 24 hours, which is what used to spend the budget
  before dawn.
- **Credit planner** (both plans; only free is normally tight):
  - Go through due (cluster, sport key) groups soonest first.
  - A group is fetched if credits used today (Pacific day), plus the
    estimated cost, is within the budget. The estimated cost is the
    dearest odds call seen so far, and at least 1.
  - Otherwise every game in the group is logged `no_credits`. Later,
    cheaper groups can still fit (first fit).
  - The 4–9 AM soccer windows are ordinary clusters and are planned the
    same way.
- **`check_log` status values:**

| Status | Meaning |
|---|---|
| `checked` | Odds were fetched, and Pinnacle returned a price for this game |
| `no_price` | Fetched, but no Pinnacle price came back |
| `no_credits` | Not fetched: the daily budget was spent |
| `fee_unknown` | Not fetched: no matched market has a fee the table confirms |
| `superseded` | A later offset ran instead |

## Picks (GAP_v2, amended in place; see `shadow_ledger_spec.md`)

- **A pick is exactly a shadow-ledger row written at a check.** There is
  no second filter.
- The rule is GAP_v2 as amended:
  - contracts = floor($10 ÷ best ask);
  - the depth must cover that many contracts, walking the order book when
    the best level is short;
  - the gap after the taker fee must be **≥ 3¢**.
- Rows are written only for games whose odds were fetched in that same run,
  so every ledger row is a pick and every pick is a ledger row.

**Pick numbers.** A pick's number (#n) is its position among first-fire
ledger rows logged that Pacific day, ordered by `logged_utc`, then market,
then side. Numbers are derived from the ledger and need no extra state.

**Pick message** (one iMessage per pick, under 300 characters):

```
#2 Cleveland over Philadelphia (Yes on Cleveland) · NBA · 4:10 PM PT · Pay up to 45¢ · now 42¢ · fair 49¢ · line steady · price 1 min old
Reply "took 42" or "skip" (add #2 if several)
Practice · 0 of 100 games
```

- **Wording by side:**

| Case | Text |
|---|---|
| Yes on a team | "A over B (Yes on A)" |
| No on a team, two-way | "B over A (No on A)" |
| No on a team, three-way | "B or a draw vs A (No on A)" |
| Yes on Tie | "Draw: A vs B (Yes on Tie)" |
| No on Tie | "No draw: A vs B (No on Tie)" |

- **Pay up to X¢:** the highest whole cent *p* (1–99) where the side's
  fair chance − *p* − the taker fee per contract at *p* (for floor($10 ÷ *p*)
  contracts, with the row's multiplier and precision) is ≥ 3¢.
- **now Y¢:** the price the row paid. That is the best ask, or the
  average fill when the book was walked (then "now 42.3¢ avg").
- **fair Z¢:** the side's fair chance, one decimal at most.
- **Line:** the Kalshi price of the side bought, compared with one hour
  earlier. That is the ledger's `kalshi_move_1h`, negated for No.
  - **Steady:** under 2¢ either way. Shown as "line steady".
  - **Toward:** +2¢ or more. Shown as "moved toward <team>, check news".
  - **Against:** −2¢ or less. Shown as "moved against, skip".
  - **No earlier price:** shown as "no earlier price".

  *Owner choice to confirm:* "line" means the Kalshi price here, because
  on the free plan the sharp book is read only once per game, so its
  movement is unknown.
- **Price age:** the minutes from the Kalshi snapshot to when the message
  is built.

**Combo** (only when the check has picks from 2 or more **different** games):
- One line covers the first pick of each game:
  `Combo #1+#3: accept a quote at X¢ or less, else let it expire.`
- X is the highest whole cent where X + the combo taker fee per contract
  ≤ the product of the legs' fair chances.
- If the fee table has no confirmed `combo` entry, the line reads:
  `Combo #1+#3: fee unconfirmed — no price.`
- There is no stake suggestion. Combos are never logged or graded, and
  never built from one game.

## Safety: when no picks are shown

| Situation | Message (per cluster, unless noted) |
|---|---|
| The check run failed, or `health.json` shows the snapshot job failing | `Don't bet now: prices not checked (system problem). <games>` |
| The check finished more than 15 minutes before the message would be sent | `Don't bet now: prices not checked (prices are N min old). <games>` |
| `fee_unknown` | `Don't bet now: prices not checked (fee not confirmed for <league>). <games>` |
| No fee table at all | Once per day: `Don't bet today: prices not checked (no fee table).` |
| Odds not configured | Once per day: `Don't bet today: prices not checked (odds not set up).` |
| `no_credits` | Once per day, at the first such cluster: `Out of odds credits today: N later time slots not checked — don't bet them.` |
| `no_price` | `<games>: no sharp price available, not checked.` |
| Checked, no rows | `<slot> · <games>: checked, no price gaps · prices N min old` |
| Daily cap reached | Picks are not listed: `Daily cap reached — no more bets today. (N price gaps logged for the record.)` |

- **Never show old picks.** A message only lists rows from the run that
  just finished. A pick is dropped once its game has started.
- **Postponed games.** If the latest listed start is 60 minutes or more
  later than the start recorded at the check, the sheet marks the pick
  "postponed — void" and no message repeats it.
- **Every message ends with the stage line.**

## Stage line (fixed in writing before any data)

The unit is a **game** (`event_ticker`), using the grader's first-fire bets
over all picks (`shadow_grades.csv`). Per game:
- **beat-the-close** = the mean over the game's bets of (closing fair chance
  of the side − price paid). This is the grader's closing-line field.
- **return** = Σ profit per contract ÷ Σ (price + fee) over the game's bets.

With *n* games, a mean *m* and a standard deviation *s* over games, the
**95% interval** is *m* ± 1.96 *s* / √*n*.

| Stage | Needs |
|---|---|
| Practice | fewer than 100 games, or no stage below is met |
| Early signal | ≥ 100 games, and the beat-the-close interval is above 0 |
| Supported | ≥ 500 games, Early signal holds, and the mean return > 0 |
| Confirmed | ≥ 1,000 games, Early signal holds, and the return interval is above 0 |

**Auto-demote to Practice:**
- **Not beating the final price:** at 100 or more games, the mean
  beat-the-close is ≤ 0.
- **20-bet losing stretch:** cumulative return, taken bet by bet in
  `snapshot_utc` order with 1 unit = 1 stake, is at least 20 units below
  its running peak right now.

The line shows the reason, for example "Practice (demoted: 20-bet losing
stretch)".

**Shown as:**
- `Practice · 12 of 100 games`
- `Early signal · 140 games · Supported needs 500`
- and so on.

The words are always "price gap". Never edge, lock or value.

**Pre-registered variants (rule 10).** Every report shows all of them,
never only the best:
- Rule: GAP_v2 is the only live rule.
- Fair chance: proportional (the signal) and power (record-only). The
  grader reports both.
- Bet sets: all picks, taken, and not taken. All three are reported.
- Any new variant is added to this table, with its definition, before it
  sees data.

## Bet logging by iMessage reply

**Grammar** (case-insensitive, the whole message):

| Reply | Meaning |
|---|---|
| `took 42`, `took 42c`, `took 42¢`, `took .42`, `took 0.42` | Bought at 42¢ |
| `took #3 42`, `took 3 42`, `took 3 at 42` | The same, for pick #3 |
| `skip`, `skip 3`, `skip #3` | Did not bet |

- **Prices:** 1–99¢.
- **Without a pick number:** the reply applies to the latest pick sent,
  but only if the latest check that had picks had exactly one. Otherwise it
  is logged as `unclear`, and the next message asks for the number.
- **Messages read:** those in the chat with `IMESSAGE_TO`, starting at
  midnight Pacific the previous day. Only messages that match the grammar
  are used, so the system's own messages never count as replies.
- **Source:** `~/Library/Messages/chat.db`, opened read-only. If it cannot
  be opened, every message carries "Bet log unreadable — daily cap not
  tracked" (rule 8). On 2026-10-22 it could not be opened ("authorization
  denied"); see Part B.
- **Storage:** `data/bet_log.csv`, append-only through `ledger.append_rows`,
  one row per reply message (keyed by message GUID, never logged twice).
  - **Columns:** `logged_utc`, `message_guid`, `message_utc`,
    `pick_date`, `pick_no`, `row_id`, `market_ticker`, `event_ticker`,
    `side`, `action` (`took`, `skip` or `unclear`), `price`, `contracts`,
    `raw_text`.
  - **Contracts:** floor($10 ÷ price taken).
  - **Corrections:** a second reply for the same pick is logged too. The
    latest reply for a pick is the one used.
- **Taken vs not taken:**
  - **Taken:** picks whose latest reply is `took`, graded at the price
    taken.
  - **Not taken:** every other pick, graded at the logged price and $10.

  The grader reports both (see `grader_spec.md`).
- **Daily cap:** the settled result of today's taken bets is −$30 or worse.
  The result is computed from `settlements` at the time of each message, not
  waiting for the nightly grader. Unsettled bets count as 0.

## Morning message (~8:00 AM Pacific; absorbs the daily digest)

One message, about a minute to read, with no picks:

```
Sports-edge · Thu Oct 22
Practice · 0 of 100 games
Today: 9 games, 4 time slots (PT): 4:30 AM soccer 3 · 4:10 PM NBA 2 · 7:00 PM NBA 3 · 7:30 PM NHL 1
Yesterday: 3 checks, 2 price gaps in 2 games · results: 1 won, 1 lost, 0 open · on paper +$4.10 on $20
You took 1: −$10.00 · skipped 1
Unchecked yesterday: 4 games in 2 time slots (out of credits 3, not running 1)
System OK · full sheet: iCloud Drive/sports-edge/sheet.txt
```

- **No matches today:** "Today: no matched games (odds not set up)", or
  another reason (rule 8).
- **"Unchecked yesterday"** (free plan only) counts in-scope matched games
  that started yesterday with no `checked` row:
  - by `check_log` status;
  - "not running" when no row exists at all, meaning the machine was
    asleep or off.
- **The digest's fields** (games watched, price gaps, scored, money,
  closing price, system, credits) go to the sheet. `digest.py` stays as the
  library they come from, and its own scheduled notification is retired by
  the Part B proposal.

## The sheet (`<iCloud>/sports-edge/sheet.txt`)

- **What it is:** a plain-text view, rewritten atomically after every alert
  and every morning message. It is not evidence.
- **Contents:**
  - the time it was built (Pacific) and the age of the newest price;
  - the stage line;
  - today's clusters, with each game, its start, and its check status and
    time;
  - open picks, with every field from the message, for games not yet
    started;
  - combos;
  - picks marked postponed;
  - today's bet log and the daily cap state;
  - yesterday's results, in full;
  - the digest fields;
  - unchecked counts.
- **Location:** set by `SPORTS_EDGE_SHEET_DIR`. Tests and reality runs
  point it at a temporary folder.

**Jargon:** no message or sheet line contains CLV, Brier, de-vig, EV,
overround, edge, lock or value. A test enforces this.

## Delivery (`sports_edge/imessage.py`)

- **Command:**
  `osascript -e 'on run argv' … 'send (item 2 of argv) to participant (item 1 of argv) of account (1st account whose service type = iMessage)' …`.
  The number and text are passed as arguments, never pasted into the
  script.
- **`IMESSAGE_TO`** is the owner's number or Apple ID, set in `.env`. If
  it is missing, sending fails loudly (exit 1).
- **`SPORTS_EDGE_NOTIFY=log`** records the message without sending it.
  Tests and reality runs use this.
- **Logging:** every attempt is appended to `logs/imessage.log`. If the send
  fails, the command exits nonzero, and the Part B wrapper raises a loud
  notification instead.

## Commands (`python -m sports_edge.slate <cmd>`)

| Command | Does | Writes |
|---|---|---|
| `show` | Prints the slate | nothing |
| `alert` | After a snapshot run: reads replies, sends the messages for checks due since the last alert, rewrites the sheet | `bet_log.csv`, `state/slate_sent.json`, `logs/imessage.log`, the sheet |
| `morning` | Reads replies, sends the morning message, writes the digest report and the sheet | the same, plus `logs/digest_<date>.txt` |
| `wake` | Prints the first and last check of the next Pacific day still ahead, and the wake time (first check − 15 min), as `wake=MM/dd/yy HH:mm:ss first=… last=…` | nothing |

- **`state/slate_sent.json`** records, per message key, when it was sent, so
  a message is never sent twice. It is operational state, not evidence.
- **Exit codes:** 1 on any exception or failed send. Problems found are
  content, not failures.

---

## Part B — PROPOSED (protected; owner review before build)

1. **`run_snapshot.sh`:** wrap the run in `caffeinate -i`. After `run_job
   snapshot`, always run `run_job slate_alert sports_edge.slate alert`, even
   when the snapshot failed, so that it can send "Don't bet now". The exit
   is nonzero if either step failed.
2. **Replace the digest job:**
   - `run_morning.sh` and `com.quis.sportsedge.morning.plist` fire at 8:00.
     They run `slate morning`.
   - On failure they send a loud `MORNING FAILED` notification (notify.sh)
     and exit 1.
   - `run_digest.sh` and the digest plist are removed from `install.sh` and
     `uninstall.sh`. The digest was never installed.
3. **Wake** (needs root):
   - After `slate morning` and after each `slate alert`, run
     `sudo -n /usr/bin/pmset schedule wake "<wake>"` when the computed wake
     time has changed (tracked in `state/wake.json`).
   - **Owner action:** a sudoers entry limited to that command, for
     example `quis ALL=(root) NOPASSWD: /usr/bin/pmset schedule wake *`.
   - If `sudo -n` fails, send a loud notification: "wake not scheduled".
   - **Observed 2026-10-22:** on battery the system sleeps after 1 idle
     minute; on AC it never sleeps. A wake alone keeps the Mac up for about
     a minute. The proposal therefore also runs
     `caffeinate -i -t <seconds until the last check + 15 min>` from the
     first run after the wake.
4. **Replies:** reading `chat.db` needs Full Disk Access for the
   interpreter that runs `slate alert`. On this machine that is the venv's
   Python, which resolves to Homebrew's `python3.14`. Granting it is the
   owner's call. Until then, messages say the bet log is unreadable.
5. **`.env`:** set `IMESSAGE_TO`, and optionally `ODDS_PLAN=paid`.
6. **Messages permission:** the first `osascript` send prompts for
   Automation access to Messages. Allow it once while logged in.

**PENDING once Part B is installed:**
- the first 8:00 AM morning message;
- the first pre-game check message;
- the first reply logged from the phone;
- the first scheduled wake.

Each is recorded in `docs/validation_log.md` with its date and evidence.

---

## Amendment 2026-10-22: found while building, before any merge

1. **A check never goes back to an earlier offset.** Once any offset is
   logged for a game, a larger offset (an earlier check time) is never run
   for it. Before this fix, the paid plan would have run a missed 60-minute
   check after the 15-minute one. The cluster tests found this.
2. **Alerts are keyed by run.** The key is `<cluster start>|<offset>|<run_id>`.
   A game matched after its cluster was checked is checked in a later run,
   and gets its own message.
3. **"Odds not set up" is said only in the morning message.** With no odds
   key nothing is matched, so no cluster exists to attach a "today" alert
   to.
4. **Message wording** (to keep every alert under 300 characters):
   - **Reply hint:** `Reply "took 42" or "skip"` when the check has one pick.
     With several picks it is `Reply "took 2 42" or "skip 2"`.
   - **Unreadable bet log:** the line reads `Bet log unreadable: cap not tracked`.
   - **Compact fallback:** if a pick message would still run over 300
     characters, it is rebuilt in compact form:
     - names cut to 10 characters;
     - no league;
     - no "again";
     - "moved toward this pick".

     If that still runs over, the alert fails loudly. The measured worst
     case is a three-way No pick with long soccer names, a walked price, a
     demoted stage and an unreadable bet log. That case is 336 characters
     in full form and fits in compact form.
5. **No "edge" anywhere a person reads.**
   - **Message and sheet headers:** `Kalshi slate · <day>` and `Slate sheet`.
     The product name contains the word, so it is not used.
   - **The digest report line** now reads "Nothing here claims that a price
     gap is real."
   - **The grader's closing line** now reads "Phase 1 claims nothing from
     these numbers".
6. **`wake`** reports the first and last check times still ahead in the next
   24 hours. The wake time is the first of these minus 15 minutes.
7. **The sheet and `state/slate_sent.json`** are written to a new temp file
   (exclusive create) and then renamed. Nothing in the module opens a file in
   an overwrite mode, so the package's write-mode guard is unchanged.

---

## Amendment 2026-10-22: independent review findings, fixed before any merge

1. **Each group's `check_log` rows are written right after its own fetch.**
   A failure later in the run therefore cannot leave a paid-for check
   unlogged and cause it to be paid for again on the next run.
   - **Known limit:** if the shadow step fails after a check was logged,
     that check's rows are lost. The run is recorded as failed, the
     snapshot alert fires, and the slate says "Don't bet now: prices not
     checked (system problem)" for that slot. It is not re-checked.
2. **Clusters are formed per Pacific calendar day, from that day's first
   game, including games that have already started.** A started game still
   anchors its slot. Before this fix, rebuilding clusters from unstarted
   games only shifted later slots and ran checks up to an hour early.
   - **Scope:** checks, alerts, the sheet, wake times and "unchecked" counts
     all use the same day clusters.
   - **Edge case:** a game first matched earlier in the day than games
     already known can still re-anchor that day's later slots.
3. **Reply prices.**
   - A number of 1 or more is cents: `took 42`, and also `took 42.3`.
   - A number below 1 is dollars: `took .42` or `took 0.42`.
   - A trailing `c` or `¢` always means cents.

   The walked-price hint ("took 42.3") is therefore parseable.
4. **"No number needed"** applies when the latest check run that wrote
   ledger rows wrote exactly one pick, repeats included, across all of its
   slots. The same rule decides whether a message's hint includes the
   number, and how an unnumbered reply is matched.
5. **A numbered reply** (`took 3 42`) names the most recently messaged pick
   numbered 3, whether that number was assigned on the reply's Pacific day
   or the day before. So a pick sent at 11:50 PM can be answered after
   midnight.
6. **"Checked, no price gaps"** is said only for games the shadow step
   actually compared, meaning at least one market reached the rule with no
   skip. The run summary records them as `compared_events`. A game that
   was fetched but not compared reads
   `Don't bet now: prices not checked (could not compare). <slot>`. This
   happens, for example, on an outcome mismatch, or when a daily run has no
   snapshot for a game matched in that same run.
7. **The 3¢ rule is applied to the unrounded gap.** The row stores the gap
   rounded to 6 decimals. Before this fix, a gap of 0.0299995 rounded up
   and passed.
8. **Team names are shortened only after the opponent is chosen.** If two
   shortened names would read the same, both are shown in full.
9. **`chat.db` dates are compared in the unit each row uses**: nanoseconds
   for modern rows, seconds for old ones. Only messages from the previous
   Pacific midnight on are read.
10. **Message sends are recorded per text,** so a failure partway through
    a group never resends the texts that already went out.

---

## Amendment 2026-10-22: owner rulings before merge

**Approved as written:**
- a drawdown is 20 stakes below the running peak;
- after-midnight pick numbering;
- logs may keep technical terms.

The jargon rule applies to messages and the sheet only.

### 1. Replies come from a text file, not `chat.db`

There is no Full Disk Access, so `chat.db` is never read and the code that
read it is removed.

- **The file:** `<sheet folder>/replies.txt`, which is
  `iCloud Drive/sports-edge/replies.txt` by default.
- **Who writes it:** the owner's Apple Shortcuts automation appends one
  line per reply. The README gives the steps.
- **Line format:** `<timestamp> <reply>` or just `<reply>`.
  - The timestamp is optional, and is ISO 8601 with an offset, as Shortcuts
    writes "Current Date" in ISO 8601.
  - The reply uses the existing grammar (`took 42`, `took 3 42`, `skip`,
    `skip 3`).
  - Any other line is ignored.
- **A line without a timestamp** is timed when the system first reads it.
- **Each line is logged once.** Its id is the sha1 of the timestamp, the
  text and its occurrence number (the nth identical line), and that id goes
  in `message_guid`.
- **The file is read, never written.**
- **When it cannot be read** (missing, or only an iCloud placeholder
  `.replies.txt.icloud` because it has not been downloaded), the bet log is
  "unreadable". Messages then say `Bet log unreadable: cap not tracked`,
  and the reason is shown in the sheet.

### 2. Daily cap = money at risk today

- **Counted:** each bet taken today (by Pacific pick date, latest reply per
  pick):
  - **$10** while it is open;
  - **$10** if it settled lost;
  - **$0** if it settled won or void.
- **Why the nominal $10:** the cap then stops at exactly three open bets,
  as directed. The actual cost of a $10 practice bet is floor($10 ÷ price)
  contracts plus the fee, which can come to slightly under $10.
- **At $30 or more:** pick alerts are replaced by
  `Daily cap reached — no more bets today.`, followed by
  `(N price gaps logged for the record.)` when there are picks.
- **The sheet** shows the amount at risk.

### 3. Same-outcome pairs: show only the cheaper one (display only)

**The pair.** In a game with exactly two teams and no Tie market, "Yes on
A" and "No on B" back the same result.

**Pick identity.**
- A pick is now one **result**: the event plus the team it backs. That
  replaces one market + side.
- Both ledger rows keep their own bet key, first-fire grading and row id,
  so the ledger, the grader and "not taken" are unchanged.
- In games with a Tie market, every market + side is its own result, as
  before.

**Shown.**
- Each alert and the sheet show one row per result: the cheaper one at that
  check, meaning the lower price paid + fee per contract. A tie goes to the
  larger gap.
- Numbers are assigned per result, in order of the result's first logged
  row that day. So a hidden twin never takes a number.

**Replies.**
- A reply to #n maps to the row shown for that result in the latest check
  that showed it.
- The bet log records that row's first-fire row id, so grading uses the
  contract actually shown.

### 4. "Line" wording

- **The sharp book was read twice for this game.** This is in practice the
  paid plan's 15-minute check after its 60-minute check.
  - The move is the side's fair chance now, minus at the previous reference
    fetch of the same game.
  - Steady is under 2¢ either way. Shown as `line steady`.
  - +2¢ or more: `line moved toward <team>, check news`.
  - −2¢ or less: `line moved against, skip`.
- **Otherwise, which on the free plan is always:**
  - The move is the Kalshi price of the side bought over the last hour
    (the ledger's `kalshi_move_1h`).
  - Shown as `Kalshi price steady`, `Kalshi price rising` or
    `Kalshi price falling`.
  - Shown as `no earlier Kalshi price` when there is none.
  - Nothing in this wording implies news.

### 5. Free-plan soccer

- `config/slate_sports.json` lists `_paid_only` keys. On the free plan
  they are never listed, matched or priced.
- **Free-plan soccer:** EPL, La Liga and the Champions League.
- **Paid only:** Bundesliga, Serie A, Ligue 1, Eredivisie, Primeira Liga,
  Europa League and Conference League.
- **Unchanged:** NFL, college football, NBA, NHL and MLB (MLB still
  spends nothing while its fee is unconfirmed).

### 6. Part B: approved without the wake

**Removed:** `wake.sh`, `pmset`, the sudoers entry, the `slate wake` command
and `caffeinate`. The Mac stays on AC, where it never sleeps (observed), and
the weekly heartbeat's shortfall check covers missed runs.

**Built as proposed** in `docs/proposals/slate_part_b.md` §1–3:
- `run_job` passes arguments through;
- `run_snapshot.sh` always runs `slate alert` after the snapshot;
- `run_morning.sh` and `com.quis.sportsedge.morning` fire at 8:00;
- the digest wrapper and plist are removed from the scripts and from
  install and uninstall.

### 7. Protected list

`sports_edge/checks.py` is added to AGENTS.md's protected list.

### Correction (same day)

The specs above call the wrappers, plists, `snapshot.py`, `odds_step.py`,
`store.py` and `config.py` "protected". That came from `main`'s AGENTS.md. On
feat/phase1 the protected list was narrowed (`a843253`). Of the files this work
changes, only `shadow.py`, `fees.py` and `grader.py` are on it. The owner
added `checks.py` to it on 2026-10-22.
