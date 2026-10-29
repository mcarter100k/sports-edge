# Spec: watching a slip live (`sports_edge/watch.py`)

Status: owner request 2026-10-28.

On 2026-10-28 the owner wanted live updates on their open slips texted to
their phone. They were done by hand that night, and the first hand-made
watcher failed silently on an SSL error. This tool does the job reliably.

**Facts only:**
- It reports scores, stats, hits, misses and finals.
- It never suggests a pick, a hedge or a cash-out.
- It never places, changes or cancels anything on Kalshi, and never calls
  Kalshi at all.
- It writes nothing under `data/`. Its only write is the existing
  `logs/imessage.log` line that `imessage.send` makes for every text.

```
python -m sports_edge.watch slip.txt [--every 30] [--max-hours 6] [--date 2026-10-28] [--no-text] [--once]
```

| Option | Meaning |
|---|---|
| `--every S` | seconds between polls; default 30, at least 15 |
| `--max-hours H` | stop after H hours; default 6 |
| `--date D` | the games' date (Pacific); default today in Pacific time |
| `--no-text` | print only; send no texts |
| `--once` | one poll: print the board and exit (status checks, reality runs) |

## Input

The same plain-text slip the rater reads (`rate.parse`,
`rate_slip_spec.md`). Only the `NBA` league is supported for now. Any
other league prints `Can't watch: league <X> is not supported` and exits
with code 2. A slip that can't be read exits 2 as in the rater.

## Source: ESPN's public scoreboard

- **Endpoints:** two JSON endpoints, with no key and no account.
  - The day's scoreboard:
    `site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD`.
  - Each paired game's summary, which holds the box score:
    `…/summary?event=<id>`.
- **Every GET goes through `net.https_get`:**
  - certificate verification is on, with the CA fallback;
  - each socket operation has a 30 s limit;
  - the whole response must arrive within 120 s.
- **Data:** it is an unofficial feed. It is display data for the owner,
  never evidence: nothing from it is stored or graded.

## Pairing games (never guess, AGENTS.md rule 6)

**A slip team fits an ESPN team when** either:
- its code equals the ESPN abbreviation (`LAL`); or
- its name, normalized as in matching, equals the ESPN team name
  (`Clippers`).

Names cover codes that differ: the slip says `GS`, `NY`, `SA`, `WAS`,
`NOP` and `UTA`, while ESPN says `GS`, `NY`, `SA`, `WSH`, `NO` and `UTAH`.

**A slip game pairs with an ESPN event** on the date when both of its teams
fit the event's two teams, and **exactly one** event does.

**Otherwise:**
- the game prints `UNMATCHED game <A> vs <B>: <no event | 2 or more events>`;
- its legs are not watched;
- the count of unwatched legs is shown in every message.

## Legs

| Form | Example | Value watched |
|---|---|---|
| Moneyline | `LA Lakers to win` | the score |
| Player stat | `<Name>: <N>+ <stat>` with stat `points`, `threes`, `rebounds` or `assists` | PTS, made threes (the first number of `3PT`), REB, AST |

- **Other leg forms** print `not watched (form not supported)`, for example
  `Walker Kessler: 6+ blocks`.
- **A player is the athlete** in the paired game's box score whose
  normalized name equals the leg's name, accents removed. It must be
  exactly one athlete.
  - **Before the box score exists:** the leg is `waiting (game not started)`.
  - **Once the box score exists but has no single such athlete:** the leg is
    `UNMATCHED player` and is not watched.

**A value that can't be read is never a zero (rules 7–8).** The feed is
unofficial, and its format can change. **The poll fails instead**
(`POLL FAILED`, counted as below) when:
- a box-score block lacks any of `MIN`, `PTS`, `3PT`, `REB` or `AST`;
- an athlete's stat list has a different length from the column names;
- a started or final game lacks a team's score;
- any value is not a number.

**A stat counts as zero only when** ESPN lists the player with an empty
stat list. That means the player has not played yet, or did not play.

## States

| State | Player leg | Moneyline leg |
|---|---|---|
| waiting | game not started | game not started |
| live | value below N; shows value/N and minutes | live; shows lead or deficit |
| **HIT** | value ≥ N, at any time; a hit never reverts | game final and the team won |
| **LOST** | game final and value below N | game final and the team lost |
| **DID NOT PLAY** | game final and ESPN marks the player as not playing | — |

**DID NOT PLAY** is reported as a fact only. The message adds
`check how Kalshi settles it`, because a regular prop settles at a fair
price when the player never takes the court.

**The ticket:**
- **LOST** as soon as any leg is LOST.
- **WON** when every leg is watched and HIT.
- **When unwatched legs remain** and every watched leg hit, the ticket
  says `all watched legs hit; <k> not watched`.

## Messages (texts, through `imessage.send`)

**Every message starts with `SLIP WATCH · <h:mm AM/PM> PT`.** A message is
sent:
1. **At the start:** `Watching <k> of <n> legs`, then every leg with its
   state.
   - If no leg can be watched at the start, the run sends `WATCH FAILED:
     no leg could be watched` and exits 1.
2. **On a change:** each poll that moves any leg into HIT, LOST, DID NOT
   PLAY or not watched (for example an `UNMATCHED player` found once the box
   score exists), or any game into final. One message lists all the poll's
   changes and the open legs.
3. **When the ticket is decided:** the result and every leg. Then the
   watch stops. This applies even on the last poll before the time limit.
4. **When it stops for any other reason:**
   - `WATCH STOPPED: every watched game final`, exit 0. For example, a leg
     whose game ended with no box score;
   - `WATCH FAILED: no leg can be watched`, exit 1, once every leg has
     become not watched;
   - the time limit, exit 0;
   - a failure.

**One poll sends at most one message.** A poll that ends the watch sends
only the ending message (result, stop or failure), which includes that
poll's `FINAL:` lines.

**A poll is applied whole or not at all.** Every leg is read first, and
states are saved only if nothing failed. A failed poll leaves no
half-saved hit behind, so no hit goes untexted.

Nothing else is texted. The same state never produces a second message.
With `--no-text`, or `SPORTS_EDGE_NOTIFY=log` (tests, reality runs), the
messages are printed and logged but not sent.

## Failures fail loudly (AGENTS.md rules 7–8)

- **The first poll must succeed.** If it fails, the run prints and texts
  `WATCH FAILED: first poll: <reason>` and exits 1. This is the lesson of
  2026-10-28: a watcher that never worked must never look like a quiet
  game.
- **A later failed poll** prints `POLL FAILED (<n> in a row): <reason>`.
  - After **5 in a row,** it texts `WATCH FAILED: 5 polls in a row failed;
    stopped` and exits 1.
  - A certificate error (`net.is_permanent`) fails at once.
- **A text that fails to send** prints `TEXT FAILED: <reason>`. The watch
  goes on, and the next message is still sent.
- **Exit codes:**

  | Code | When |
  |---|---|
  | 0 | ticket decided, every watched game final, time limit, or `--once` |
  | 1 | failure |
  | 2 | the slip can't be read or the league isn't supported |

## Relation to the one-advice-path branch

`feat/one-advice-path` would have let only `slate.py` call `imessage.send`.
The owner closed it unmerged on 2026-10-28, so this tool's texts are
allowed. They stay facts only.

## Tests (Gate 1)

The fixtures are trimmed from real ESPN responses saved on 2026-10-28:
- Lakers–Clippers before tip;
- Lakers–Clippers with 17.7 s left;
- Heat–Hawks final;
- the scoreboard.

The tests cover:
- **Pairing:** by code; by name where the codes differ (`WAS`→`WSH`,
  `UTA`→`UTAH`); two candidate events, which is UNMATCHED; no event.
- **Leg parsing:** each stat word; an unsupported form; accents
  (`Nikola Jokić`).
- **States:**
  - a player leg goes from live to HIT mid-game;
  - LOST at final;
  - DID NOT PLAY;
  - a moneyline leg is HIT or LOST at final;
  - a hit never reverts.
- **Messages:**
  - the start message;
  - one message per change;
  - no repeat for an unchanged state;
  - the ticket LOST stops the watch;
  - the ticket WON;
  - the unwatched-leg count.
- **Failures:**
  - the first poll fails (exit 1 and a text);
  - 4 failures then a success keep going;
  - 5 in a row stop it;
  - a certificate error stops it at once;
  - a text failure does not stop the watch;
  - the time limit.
- **Unreadable data** (added after review, 2026-10-28):
  - a missing `PTS` column, a missing score, or a value of `--` fails the
    poll;
  - it never yields `0/N` or a false `TICKET LOST`;
  - five in a row stop the watch loudly.
- **Silent endings** (added after review):
  - a leg found UNMATCHED mid-watch is texted;
  - losing the last watched leg fails loudly;
  - every game final with a leg still waiting stops the watch;
  - a ticket decided on the last poll before the time limit still sends
    its result.
- **Isolation:**
  - `--once` prints the board and sends nothing;
  - no file under `data/` is written.
- **Integration** (marked): one real scoreboard GET parses.

## Reality gate (Gate 2)

Run `python -m sports_edge.watch <slip> --once` through
`scripts/reality_run.py` against the real ESPN feed, with
`SPORTS_EDGE_NOTIFY=log`. The live data directory must be unchanged.
