# Spec: logging your own bets (`sports_edge/mybet.py`)

Status: owner request 2026-10-22. Lean and record-only.
- **Separate from the system's evidence:** never written to the shadow
  ledger, the bet log for picks, or the stage line.
- **Read-only towards Kalshi:** it never places, changes or cancels
  anything.

```
python -m sports_edge.mybet < bet.txt      (or: python -m sports_edge.mybet bet.txt)
```

## Input

**The slip:** in the rate format (`docs/specs/rate_slip_spec.md`), read
by `rate.parse` with the same strict rules.

**One extra line,** anywhere in the text, saying what was actually placed:

| Form | Examples |
|---|---|
| `placed <cost> pays <payout>` | `placed $29.93 pays $2,000.00`, `placed 30 pays 1554.40` |

The `$` signs are optional, and commas are allowed.

**Strict reading.** A missing or repeated `placed` line, or a slip that
can't be read, stops the run with exit code 2, and nothing is written.

## What is recorded: `data/my_bets.csv`

**Append-only,** through `ledger.append_rows`. One row per run. The same
bet placed twice is logged twice.

| Column | Meaning |
|---|---|
| `bet_id` | the first 12 characters of the sha1 of the log time and the slip text |
| `logged_utc` | the time it was logged, UTC ISO-8601 with `Z` (rule 5) |
| `logged_pt` | the same time in Pacific, as people read it, with the zone shown |
| `league` | as written on the slip |
| `n_legs` | the number of legs |
| `legs` | each leg as `<text> @ <price>%`, joined by ` \| ` |
| `leg_tickers` | each leg's Kalshi market, in the same order; blank where not identified |
| `cost`, `payout` | from the `placed` line, in dollars |
| `implied_chance` | cost ÷ payout |
| `legs_product` | the product of the slip's own leg prices |
| `markup` | implied chance ÷ legs' product − 1 |

## Identifying each leg's Kalshi market (never guessed)

**The game.** The slip game must pair with exactly one Kalshi game event,
by the rate spec's rules: team code equals the ticker suffix, or the name
equals the end of the matched sharp-book name.

**Moneyline legs** (`<team> to win`) take that team's game market.

**Player legs** (`<Player>: <N>+ <stat>`, for example `Kevin Durant: 20+
points`) take the single market that meets all of these:
1. its event shares the game's key: `26OCT23HOUSAS` in
   `KXNBAGAME-26OCT23HOUSAS`, as in `KXNBAPTS-26OCT23HOUSAS`;
2. its event title ends in `: <stat>`, ignoring case (`Points`, `Threes`,
   `Rebounds`, `Assists`, …);
3. its title is exactly `<Player>: <N>+`, as Kalshi writes it, for example
   `Nikola Jokić: 20+`.

**A leg that doesn't qualify is "not identified".** That covers no unique
game, no unique market, or another leg type. The bet is still logged. The
command says which legs were not identified, and that the bet then won't
settle automatically.

## Settling (the grader; see `grader_spec.md`)

| Outcome | When | Dollars |
|---|---|---|
| **Lost** | as soon as any identified leg settles `no` | − cost |
| **Won** | every leg identified and settled `yes` | + (payout − cost) |
| **Void leg** | a leg settles other than yes or no, and none lost | blank; check Kalshi, since its combo rules decide |

- **Where results are written:** one row per bet in
  `data/my_bet_grades.csv`. It is append-only, written once per `bet_id`,
  with the columns `bet_id`, `result`, `dollars`, `legs_settled` and
  `graded_utc`.
- **A bet with an unidentified leg** can't settle as won, and is counted
  as "can't settle". It can still settle as lost, if an identified leg
  lost.

## The morning line

```
Your own bets: N, settled N, +/−$X.
```

- **Counts:** N is all bets logged so far. Settled counts won plus lost.
  `$X` is the net of settled bets.
- **When nothing is logged:** the line reads `Your own bets: none logged
  yet.`, per rule 8.
- **Other rows:** void and can't-settle bets add ` (N void, N can't
  settle)`.
- **Kept separate:** never mixed into the stage line or the price-gap
  numbers.

## Amendment 2026-10-23: answers the independent review (REVISE) and the live gate

Review: `docs/reviews/2026-10-23_my_bets.md`. The owner decided the
protected-market rule on 2026-10-23.

**1. Protected player markets.** Kalshi lists each NBA player market twice:
the normal series (for example `KXNBAPTS`, "NBA Player Points") and a
Protected one (`KXNBAPTSPROT`, "NBA Player Points Protected"; series ending
`PROT`). A player leg means the **normal** market, unless the slip has a
line `protection on`. Then every player leg means the Protected market.
- `protection off` may also be written.
- More than one `protection` line is an unreadable slip (exit code 2).

**2. When a slip can be logged.** A game may pair with a Kalshi game event
resolving from 36 hours before the log time to 36 hours after, in any market
status (open, closed or settled). A bet can then be logged after its games,
as well as when it is placed. Every uniqueness rule is unchanged: two
fitting events, or two fitting markets, leave the leg not identified.

**3. Settling.** These rules replace the table above:

| Outcome | When | Dollars |
|---|---|---|
| **Lost** | every leg identified, and any leg settled `no` | − cost |
| **Won** | every leg identified and settled `yes` | + (payout − cost) |
| **Void leg** | every leg identified and **settled**; none `no`; at least one `scalar` (Kalshi's stored value for a void) | blank; check Kalshi |

- **Open:** a bet with every leg identified but not every leg settled, and
  no leg settled `no`. It stays open. A void leg never closes a bet while
  another leg is open.
- **Can't settle:** a bet with any leg not identified is **never graded**.
  It is counted only as "can't settle". It is never in "settled" or in the
  net dollars, whatever its identified legs did. Counting its losses but
  never its wins would bias the record.
- **One grade per `bet_id`,** even if the same `bet_id` appears twice in
  `my_bets.csv`.
- **A row that can't be read** (a blank or non-numeric cost or payout) is
  skipped and counted as unreadable in the grader's line. It never stops the
  grader, so the shadow ledger's grading always runs.

**4. Reading the `placed` line.** Each amount must contain a digit (`,`
alone is not an amount). The payout must be greater than the cost.
Otherwise the slip is unreadable (exit code 2).

**5. The lines.**
- **Morning:** with nothing settled, the line leaves out the dollar figure:
  `Your own bets: N, settled 0.`
- **Morning, when it's too long:** if the message would go over its length
  limit, the my-bets line is dropped first. The grader's line and the sheet
  still carry it.
- **Grader:** adds `unreadable N` when N > 0.

**6. Reading the database:** `mybet` opens it read-only.

**7. Tests the implementation must have:**
- E void with F open, then F no on a later run, graded `lost`;
- a void leg stored as `scalar`;
- the twin Protected event in the fixture, with player legs refused without
  rule 1 and taken by it;
- `protection on` choosing the Protected market;
- a game already settled still pairing, and two fitting events refusing;
- a bet with an unidentified leg whose identified leg lost, never graded;
- the same `bet_id` twice graded once;
- a malformed row not stopping the grader;
- `main` returning 2 for an unreadable slip, including `placed , pays 5`;
- `data/bet_log.csv` and `data/shadow_markouts.csv` untouched, and the
  slate's real stage line unchanged.

## Amendment 2026-10-24: answers the re-review (REVISE)

Review: `docs/reviews/2026-10-23_my_bets_r2.md`. These rules replace the
matching parts of the 2026-10-23 amendment.

**1. Never the wrong game of a matchup.** A slip carries no date. A game
that pairs with exactly one Kalshi game event is still refused if the
`markets` table holds **another** game event of the same series, in any
status, whose two team codes are the same two codes, resolving within 7 days
of the paired event. Every leg of that game is then not identified.
- **Output:** the command prints each paired event ticker. The ticker
  carries the date, so the owner can see which game was taken.

**2. Settling waits for every leg.** A bet with every leg identified is
graded only once **every** leg has a stored settlement:

| Settlements | Result | Dollars |
|---|---|---|
| all `yes` | **won** | + (payout − cost) |
| any value other than `yes` or `no` (the store keeps `yes`, `no` and `scalar`) | **void_leg** | blank; out of the net, whatever the other legs did |
| otherwise (at least one `no`) | **lost** | − cost |

- **Lost waits too.** Lost is no longer graded early. A void leg can then
  never be left out of the net only when the bet would have won.
- **Open past due.** A bet whose legs are not all settled stays open. The
  grader's line counts `open past due N`: open bets whose markets all
  resolved more than 3 days ago. A settlement that never reached the store
  is then visible (rule 8).

**3. Rows that can't be read.** A row missing `bet_id`, `leg_tickers`, `cost`
or `payout`, or with an amount that isn't a finite number, counts as
unreadable. The grader's own-bets step runs in its own error guard, so it
can never fail the shadow grading.

**4. The lines.**
- **The grader's line,** like the morning line, leaves out the dollar
  figure when nothing has settled.
- **The sheet** carries the own-bets line. It stays there when the morning
  message drops it.

**5. Protected markets, exactly.** A Protected player market is used only
when its series is exactly the normal market's series plus `PROT` (for
example `KXNBAPTSPROT` for `KXNBAPTS`), for the same player, line and game.
This is checked on the `series_ticker` column.

**6. Tests the implementation must have:**
- the bet's game resolved 37 h before logging, with a rematch 12 h after,
  not identified;
- `[no, scalar]` graded `void_leg`, and `[no, open]` left open;
- open past due counted;
- a short row and a `NaN` amount, unreadable, with the grader still OK;
- the sheet carrying the line;
- `rate.pair_games` with its defaults refusing a finalized event and an
  event already resolved;
- a spread leg, and a game with no Kalshi event, not identified through
  `mybet`.

## Amendment 2026-10-24b: answers the third review (APPROVE, minor findings)

Review: `docs/reviews/2026-10-24_my_bets_r3.md`.

**1. The logging limit, stated.** The rematch rule can only catch a wrong
game resolving within 7 days of the paired one, and pairing looks 36 hours
either side of the log time.
- **Log within 5 days of the game:** right after placing is best. A slip
  logged later may pair with a different game between the same two teams.
- **What the rule refuses:** the rematch rule refuses some bets entirely.
  These include MLB series games, playoff series games, home-and-home sets
  and games Kalshi relists. The command then names the rival event, so the
  reason is visible.

**2. The own-bets lines can never stop another job.**
- **`my_bets_line` never raises.** On any error it reads `Your own bets:
  can't be read (<error>)`. The alert, morning and show runs carry on.
- **Unreadable grade rows.** A grade row whose `dollars` isn't a finite
  number is left out of the net, and a row with no `result` is ignored.

**3. A failed own-bets step is visible.**
- **Morning:** the morning's system problems include `own bets step failed`
  when the latest grader run recorded `my_bets_settled=error`.
- **Separate guards:** the settling and the report have one each, so rows
  already written are counted even if the report fails.
