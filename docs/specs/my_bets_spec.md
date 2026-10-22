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
