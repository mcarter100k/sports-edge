# Spec: rating a Kalshi slip (`sports_edge/rate.py`)

Status: owner-approved 2026-10-22 (with changes). **Display only.** It never
places an order, creates an RFQ, writes a ledger row or grades anything
after the fact. It does **not** unpark the Phase 3 combo logger
(`docs/archive/phase3_combo_logger_spec.md`): nothing about a slip is
stored, so the unpark gate is unchanged.

```
python -m sports_edge.rate < slip.txt      (or: python -m sports_edge.rate slip.txt)
```

## Input: the plain-text slip

The format is pinned to the owner's real slip (`tests/fixtures/slip_nba_9leg.txt`).
One item per line; blank lines are ignored.

| Line | Form | Example |
|---|---|---|
| League (optional, once, before the first game) | a label from `config/slate_sports.json` | `NBA` |
| Game | `<team> vs <team>`, each team `<CODE> <Name>` | `CLE Cavaliers vs PHI 76ers` |
| Leg | `<text>` then whitespace then `<price>%`, with a price of 1–99 | `PHI 76ers to win  57%` |
| Combo count | `<N> MARKET COMBO` | `9 MARKET COMBO` |
| Payout | `$<stake> pays $<payout>` (commas allowed), on its own line or after the count | `$30 pays $1,554.40` |

**Moneyline legs.** A leg whose text is `<team> to win`, where `<team>` is
exactly one of its game's two teams, is a moneyline leg. Any other leg is
"not a moneyline", for example `Nikola Jokić: 20+ points`.

**Strict reading.** Any line that fits none of the forms stops the run with
`Can't read the slip: line <n>: <line>` and exit code 2. Nothing else is
printed, and nothing is guessed. The same happens when:
- a leg comes before any game;
- a `<team> to win` names a team not in its game;
- `N` differs from the number of legs;
- the payout is missing.

## Pairing a game with Kalshi and the sharp book (rule 6)

A slip leg is compared with a sharp price only through an existing match
(`odds_matches`, `docs/specs/odds_matching_spec.md`). A slip game pairs with
exactly one Kalshi game event when all of these hold:

1. **The event** is in a series that has matched games for the league's
   odds sport key. It is a two-team `Game` event, active, resolving within
   the next 36 hours.
2. **Each slip team maps to one of the event's two team markets,** by
   either:
   - **code:** the slip code equals the market ticker's team suffix (`PHI`
     matches `KXNBAGAME-26OCT22CLEPHI-PHI`); or
   - **name:** the slip name, normalized as in matching, equals the end of
     the matched sharp-book team name. `SA Spurs` matches `San Antonio
     Spurs`. This is needed because Kalshi's ticker says `SAS` where the
     slip says `SA` (observed live, 2026-10-22).

   The two slip teams map to the two different markets.
3. **Exactly one** event satisfies 1–2. Otherwise the game is not paired,
   and its legs say so.

## Fair chance, a fresh read, and the credit budget

- **Fresh enough:** a moneyline leg uses the latest stored sharp read of its
  matched game when that read is at most 15 minutes old (the same limit as
  GAP_v2).
- **Older reads:** at most **one pull per sport per run**, for the slip's
  paired games, and only if the daily credit budget allows it (the same
  rule as the checks: assume the dearest call seen).
  - **The pull is not stored** in `reference_odds`: a rating must never
    become a game's closing price, or the owner's choice of slips would
    reach the evidence. The call is logged in `odds_calls`, so it counts
    against the budget.
  - **When the budget is spent,** the run prints
    `Can't rate: no odds credits left today`.
- **The fair chance** is the proportional fair chance of the team, as in
  `odds.devig_proportional`.
- **A game already started** is not rated: `game started — not rated`.

## Grades (moneyline legs with a fair chance)

**The gap.** *d* = fair chance − price − Kalshi taker fee per contract. The
fee assumes buying that leg alone: floor($10 ÷ price) contracts, the
series' multiplier, and the fee table's precision. It is in cents.

| Grade | *d* |
|---|---|
| A | ≥ 5¢ under fair |
| B | 3¢ to 5¢ under |
| C | 0¢ to 3¢ under |
| D | over fair by less than 3¢ |
| F | 3¢ or more over fair |

The bands are A: *d* ≥ 5; B: 3 ≤ *d* < 5; C: 0 ≤ *d* < 3; D: −3 < *d* < 0;
F: *d* ≤ −3.

**When there's no grade, the leg says why:**
- fee not confirmed (no fee table, or the series is unconfirmed);
- not a moneyline;
- not paired;
- odds not set up;
- odds too old with no credits left;
- game started.

## The combo

- **The markup line is always printed,** even when nothing else can be:
  - **Payout's chance:** stake ÷ payout.
  - **Legs' chance:** the product of the slip's own leg prices.
  - **How much more or less you pay:** payout's chance ÷ legs' chance − 1,
    as a percentage.
  - **"You'd win about 1 in N",** where N = 1 ÷ legs' chance, rounded to a
    whole number.
- **The fair line** is the product of the legs' fair chances, against the
  payout's chance, before fee.
  - **When it is printed:** only when every leg is a graded-eligible
    moneyline with a fair chance and no two legs share a game.
  - **Otherwise** it names what blocks it: legs without a sharp price, and
    same-game legs by game.
  - **The combo itself gets no grade:** its fee is unconfirmed (combo fee
    parked, `fee_model_spec.md`), and the line says so.

## Output

**What it is:** plain English with no jargon (no CLV, Brier, de-vig, EV,
overround, edge or value).
- **Line 1:** `Slip: <N> legs · $<stake> pays $<payout>`.
- **One line per leg:** `<n>. <leg> · <price>% · <fair, grade, or reason>`.
- **Then:** the markup line, the fair or blocked combo line, and the "Can't
  rate" line when it applies.

**Exit codes:**

| Code | When |
|---|---|
| 0 | a rating was printed, including "can't rate" |
| 2 | the slip can't be read |
| 1 | any other error |

## Amendment 2026-10-28: same-game warnings (owner request)

**Why:** on 2026-10-28 a combo with `LA Lakers to win` beside Reaves,
Garland and Lopez legs from the same game lost the way such slips lose.
The Lakers won by 18, both teams' starters sat the 4th quarter, and
Garland finished one point short. The combo price treats legs as
independent; same-game legs are not.

**Display only.** A warning never changes a leg's grade, the markup line
or the combo line, and nothing is stored.

**Player leg:** any leg that is not a moneyline leg.

**Where:** after the combo line and before the "Can't rate" line, in the
slip's game order. Each game gets at most one line per warning kind.

| Kind | When | Line |
|---|---|---|
| Win against players | the game has a moneyline leg and at least one player leg | `Warning: <A> vs <B>: "<team> to win" pulls against <k> player leg(s) in the same game. A win is likeliest as a blowout, and in a blowout the starters often rest late.` |
| Player stack | the game has two or more player legs and no moneyline leg | `Warning: <A> vs <B>: <k> player legs share one game. A blowout would cut all their minutes at once.` |
| Lopsided game | the game has at least one player leg, and the favourite's chance is 75% or more | `Warning: <A> vs <B>: <team> is about <p>% to win (<source>). If it turns into a blowout, the starters often rest late — <k> player leg(s) here.` |

**The favourite's chance, first that applies:**
1. **A moneyline leg on the slip for that game.** Its price backs its team.
   The other team gets 100 − price.
   - Source text: `slip price`.
2. **The latest stored Kalshi price** of the paired game (pairing as above).
   - Each team gets the mid of its market's latest `price_snapshots` row,
     at most 2 hours old.
   - Source text: `Kalshi price`.
3. **Otherwise the check is not run,** and the line says so (rule 8):
   `Note: <A> vs <B>: blowout risk not checked (<reason>).`
   - Reasons: `game not paired`, or `no recent Kalshi price`.

**The 75% threshold** is roughly a 7-point NBA favourite. It is a starting
value, not tested evidence. Calls made with it go in the owner's judgment
log under the tag `same_game_blowout` until 20 or more games say whether
it holds.

**Tests (Gate 1):**
- the owner's 2026-10-28 Lakers–Clippers slip gives the "win against
  players" warning and the "lopsided game" warning, with the source
  `slip price`;
- a two-player-leg game gives the "player stack" warning;
- a lopsided game with no moneyline leg uses the stored Kalshi price, or
  prints the "not checked" note when the price is older than 2 hours or the
  game is not paired;
- a 74% favourite gives no lopsided warning; 75% does;
- a slip whose games each have one leg prints no warning;
- grades, the markup line and the combo line are unchanged by warnings.
