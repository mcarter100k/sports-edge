# Proposal: college football in the slate (owner decision)

Status: **PROPOSED, owner decision.** Nothing is changed yet.
`config/slate_sports.json` is untouched. The fix recommended in option B
changes `sports_edge/matching.py` (PROTECTED), so it would start with an
amendment to `docs/specs/odds_matching_spec.md`.

## Correction first

The owner's request rested on my earlier report that unpaired college games
"aren't in the reference listing at all". **That was wrong.** I read it
from `reference_events`, which stores only games starting within the
24-hour odds horizon. The full free listing (`/v4/sports/americanfootball_ncaaf/events`,
0 credits, 2026-10-22 evening PT) has 67 college games. The missed Akron vs
Kent State, Baylor vs Kansas, Boston College vs Georgia Tech and Bowling
Green vs Buffalo are all in it, on Saturday 2026-10-24.

## What actually blocks college games

**Kalshi placeholder times.**
- **The placeholder:** Saturday college games carry
  `expected_expiration_time = occurrence_time = 2026-10-24 03:00Z`. That
  is the end of the ticker's date, Oct 23, in Eastern time, about 14 hours
  *before* the real kickoff.
- **Why they fail:** the matching rule's time test (rule 4: the reference
  start within [resolve_by − 8 h, resolve_by]) can never pass for them.
- **Why they read name_mismatch:** the unmatched reason is `name_mismatch`
  only because other games were in the wrong window.
- **When it clears:** in settled history, Kalshi's expected expiration sits
  near the actual end of play (median close minus expected: +0.6 h,
  NCAAF). So the placeholders appear to be replaced at some point. When is
  NOT CHECKED: a game listed today is to be watched until kickoff.

## Counts per week (2026-10-22, live data and the free listing)

| ISO week (by game date) | Kalshi college games | in the reference listing (unique name match) | matched today by the time rule | name-matchable but starting after Kalshi's expected expiration | Kalshi placeholder times |
|---|---|---|---|---|---|
| 43 (Oct 19–25) | 104 | 45 | 8 (5 active + 3 settled) | 27 | 30 |
| 44 (Oct 26–Nov 1) | 113 | 17 (the reference lists games through the week) | 0 (outside the 24 h horizon) | 10 | 35 |

- **Matched today:** 8 college games in week 43. They were all Wednesday,
  Thursday or Friday games, whose Kalshi times were real.
- **Pairable with a timing fix:** about 45 a week. The reference source
  lists about 49 college games in a full week, and the matching rule's
  names, sport and uniqueness tests already pass for 45 of them.
- **Never pairable:** about 59 of Kalshi's 104 games a week have no
  reference listing under any name. That is the source's coverage, and no
  configuration or alias fixes it.

## Options

**A. Remove college football from the slate** (`config/slate_sports.json`:
delete `americanfootball_ncaaf`). This is the requested change.
- **Effect:** about 8 matched games a week today are lost, and about 45 a
  week if timing were fixed. It also saves the credits college checks
  would cost once pricing starts: none so far, because without a fee table
  no check has been priced.
- **When it is right:** college is not a market of interest, or its
  liquidity is too thin to matter.

**B. Keep college, and fix matching for placeholder times. Recommended.**
1. **Spot the placeholder:** an event whose `expected_expiration_time`
   equals its `occurrence_time` and falls on the hour at 03:00Z or 04:00Z
   (the end of the ticker's date in Eastern time).
2. **Widen the window only for those events:** allow the reference start in
   [placeholder − 8 h, placeholder + 30 h]. That covers the ticker's date
   and the next day, in any US time zone.
3. **Keep everything else:** rules 1–3, 5 and 6 unchanged. That means both
   teams must match two distinct reference teams, and the match must be
   unique both ways. Two meetings of the same college teams within 38
   hours do not happen, so the wider window cannot pair the wrong game.
4. **A new unmatched reason** `kalshi_placeholder_time`, and a separate
   reason `not_in_reference_listing` when no reference game matches the
   names at any time. Coverage reports then separate "the source does not
   list it" from "we failed to pair it".
5. **New rule version:** `match_v3`.

**Pricing safety for option B:**
- Checks are scheduled from the *reference* start time, as they are now.
- The pre-game rule (`ts < commence_utc`) uses the reference start. So a
  wrong Kalshi placeholder cannot make a check run during a game.

## What I need from you

Choose A or B. For B, I will write the matching spec amendment, then the
code with tests, including a positive control on the Akron vs Kent State
case. It will be held for your review because `matching.py` is protected.
