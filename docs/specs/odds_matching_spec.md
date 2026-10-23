# Spec: reference odds adapter and matching (`sports_edge/odds.py`, `sports_edge/matching.py`)

Status: Phase 1. Protected files.

## Interface

```python
class ReferenceSource(Protocol):
    name: str
    def list_sports(self) -> list[RefSport]: ...            # key, group, title, active
    def list_events(self, sport_key) -> list[RefEventStub]: ...  # id, home, away, commence (free)
    def fetch_moneylines(self, sport_key, event_ids) -> list[RefEvent]: ...  # priced (costs credits)
```

`RefEvent` holds source_event_id, sport_key, group, commence_utc, home,
away, bookmaker, book_last_update_utc, fetched_utc, and outcomes as
{name: decimal odds}.

## Implementation: The Odds API v4 (`TheOddsApi`)

Source: `the-odds-api.com/liveapi/guides/v4/` and its bookmaker list (read
2026-10-21).

- Host `https://api.the-odds-api.com`. The key comes from `ODDS_API_KEY` in
  `.env`.
- `GET /v4/sports`: free (does not count against quota).
- `GET /v4/sports/{key}/events`: free. Used for matching.
- `GET /v4/sports/{key}/odds?markets=h2h&oddsFormat=decimal&bookmakers=pinnacle&eventIds=…`:
  costs credits. The docs give cost = markets × regions, and the
  `x-requests-last` header reports the actual cost. Each call is recorded in
  `odds_calls` along with `x-requests-remaining`.
- **Sharp book:** Pinnacle (`pinnacle`, region `eu`). The bookmaker list
  notes "Odds are from public website which may incur a delay", which is
  one reason for the staleness limits below.
- Plans: Starter is free with 500 credits a month; 20K is $30 a month.
  **Budget guard:** `ODDS_DAILY_CREDITS` (default 15, which keeps the free
  tier under 500 a month). Credits used today are summed from `odds_calls`.
  Once the next call would exceed the budget, the step prints
  `ODDS BUDGET REACHED` and stops calling. This is counted, not an error.
- **Cadence:** the odds step runs at most once per `ODDS_INTERVAL_MIN`
  (default 60). It prices only matched events that commence within
  `ODDS_HORIZON_H` (default 24 h), soonest first.

## De-vig

Proportional: `fair_i = (1/o_i) / Σ_j (1/o_j)` over the **complete**
outcome set (2 outcomes, or 3 including `Draw`). The event is rejected
(and logged) if any price is ≤ 1.0, an outcome is missing, or the overround
`Σ 1/o` is outside [1.00, 1.15]. The method is recorded on every row
(`devig_method = proportional`).

## Sport vocabulary (data, not leagues)

`config/sport_groups.json` maps an Odds API `group` to a Kalshi series tag,
for example `"Ice Hockey": "Hockey"` or `"American Football": "Football"`.
It maps sports, never competitions. Leagues on both sides are discovered at
run time. An Odds API group with no entry is never polled, and is listed
once per run as `UNMAPPED GROUP`.

## Matching rules (never guess)

A Kalshi event **E** matches a reference event **R** only if **every** rule
holds:

1. **E is a moneyline game:** `product_metadata.competition_scope == "Game"`,
   `mutually_exclusive` is true, it has 2 or 3 binary markets, and each
   market has a non-empty `yes_sub_title`. With 3 markets, exactly one
   `yes_sub_title` is `Tie`.
2. **Same sport:** R's group maps to a tag on E's series.
3. **Same outcome count:** 2-way with 2-way, or 3-way (with `Draw`) with
   3-way (with `Tie`). The free event listing does not say whether a draw is
   priced, so this rule is enforced **when prices arrive**. The set of
   reference outcomes the Kalshi markets map to must equal the set Pinnacle
   priced. Otherwise the event is logged `UNMATCHED … reason=outcome_count`
   and is never compared on those prices.
4. **Time:** R.commence_utc falls within [E.resolve_by − 8 h, E.resolve_by].
   Observed on 2026-10-21 for NBA, NFL, NHL and EPL: `expected_expiration_time`
   ≈ start + 3 h.
5. **Names:** after normalising (casefold, strip accents, `&` → `and`, drop
   punctuation, collapse spaces), each Kalshi team name K matches a
   reference team name N only if K == N, N starts with K followed by a
   space, or `config/team_aliases.json` lists the pair. The two Kalshi
   teams must match two **distinct** reference teams, and each must match
   exactly one.
6. **Unique both ways:** exactly one R satisfies rules 2–5 for E, and that R
   satisfies them for no other E.

`Tie` ↔ `Draw` is the only fixed outcome mapping. `config/team_aliases.json`
is keyed by Kalshi sport tag (`{"Basketball": {"Los Angeles L": "Los Angeles Lakers"}}`),
so an alias never applies outside its sport. It starts empty, and every
entry is added by hand after an `UNMATCHED name_mismatch` line has been
inspected.

## Logging unmatched markets loudly

Every Kalshi game event in a polled sport that resolves within
`ODDS_HORIZON_H` + 8 h, and has no match, is unmatched. **The first time**
an event is unmatched for a given reason, it gets a line on stderr (and so
in the day log):

`UNMATCHED <event_ticker> sport=<tag> reason=<no_candidate|ambiguous|name_mismatch|outcome_count> markets=<n> detail=<…>`

It also gets a row in `unmatched`. Later runs do not repeat the line. They
count it instead: every run's summary prints `unmatched=<total currently
unmatched>` and the count per sport, and the heartbeat reports the weekly
total. Why only once: hourly matching of about 1,000 soccer and tennis games
would otherwise print thousands of identical lines a day, and a line that
always appears stops being read. If the reason changes, it is a new line.

Reference events that started up to 8 hours ago are still listed for
matching, so a game already under way is not reported as unmatched. Only
games that have not started are priced.

A match that
later changes is a conflict: it is logged as `MATCH CONFLICT`, and that
event is never compared again.

---

## Amendment 2026-10-22: Kalshi games from the database, targeted lookups (owner-approved)

Kalshi game events for matching come from the `markets` table (filled by the
daily scan and by targeted lookups). The rules above apply unchanged.
`odds_matches` also records the reference `sport_key`.

**Targeted Kalshi lookup.** When an hourly odds pull finds a reference game
starting within the next 24 h that no Kalshi event fits:

1. Find the Kalshi series that have matched games from that same `sport_key`
   before (learned from `odds_matches`; no league list).
2. For each such series, fetch `GET /events?series_ticker=<s>&status=open&with_nested_markets=true`,
   at most 20 series per cycle, and add the events to `markets`.
3. Match again, for those reference games only.
4. If a game is still unmatched, or no series has matched that `sport_key`
   before, print
   `UNMATCHED-REFERENCE <source_event_id> sport_key=<k> reason=<no_kalshi_series_known|no_kalshi_event> detail=<home vs away, start>`
   once per game and reason. This is logged like `UNMATCHED`, never guessed.

---

## Amendment 2026-10-22: power de-vig stored alongside proportional (owner review finding 4)

Every reference row stores two fair probabilities:

- `fair_prob`: proportional, as above. **The signal uses this one.**
- `fair_prob_power`: the power method. Find k ≥ 1 such that Σ_i (1/o_i)^k = 1,
  then fair_i = (1/o_i)^k. Solved by bisection to 1e-12. When the overround
  is 1 exactly, k = 1, and the two methods agree.

The same rejections apply to both. The grader scores both (see
`grader_spec.md`), so the data decides between them.

---

## Amendment 2026-10-22: sport scope, check schedule, credit planner, `reference_events` (owner request; see `slate_alerts_spec.md`)

- **Scope.** Only the sport keys in `config/slate_sports.json` are listed,
  matched or priced. No other key costs a call. An in-scope key that the
  API does not report as active prints `SLATE SPORT NOT LISTED <key>`.
- **Matching** is still at most once per `ODDS_INTERVAL_MIN` (60). It uses
  only the free `/sports` and `/events` calls.
  - After each matching pass, one `reference_events` row is appended for
    each matched reference game. The row records its listed start, home
    and away.
  - Each priced game also gets a row at fetch time.
  - The latest row gives the start time used for clusters and checks.
- **Pricing** no longer runs hourly for every matched game within 24 h.
  - It runs on every 15-minute cycle, but only for games whose check is
    due (clusters, offsets by `ODDS_PLAN`, and the credit planner, all
    defined in `slate_alerts_spec.md`).
  - Each due game gets one `check_log` row per run, with its status.
- **No credit on unpriceable games.** A game is fetched only if at least one
  of its matched markets resolves a fee from the table. Otherwise it is
  logged `fee_unknown`.
- **Budget.** `ODDS_DAILY_CREDITS` defaults to 15 on the free plan and 600
  on the paid plan. The guard is unchanged: assume the dearest call seen,
  and never cross the cap.
- **Return value.** The odds step returns `checked_events`, the reference
  ids priced this run. The shadow step evaluates only those games.

---

## Amendment 2026-10-22: two NHL aliases, "St." = "State", and a quieter unmatched log (owner-approved after the live odds run)

**Found in the live odds run (2026-10-22).** Four of the day's games in
followed leagues failed rule 5 on names alone:
- Kalshi's `New York R` and `New York I` against `New York Rangers` and
  `New York Islanders`;
- Kalshi's `Appalachian St.` and `Kennesaw St.` against `Appalachian State
  Mountaineers` and `Kennesaw State Owls`.

**Aliases (`config/team_aliases.json`, Hockey only):**
- `New York R` → `New York Rangers`
- `New York I` → `New York Islanders`

**Normalization, rule version `match_v2`:**
- **The rule:** after the existing steps, the whole word `st` reads as
  `state`, on both sides. So `Appalachian St.` becomes `appalachian state`,
  which is a prefix of `appalachian state mountaineers`.
- **Names like `St. Louis`** become `state louis` on both sides, so they
  still match each other.
- **Unchanged:** every other part of rules 1–6, including "each Kalshi team
  must match exactly one reference team". A name that fits both teams of a
  game, such as `Kansas` against `Kansas Jayhawks` and `Kansas State
  Wildcats`, still refuses.
- **Rule version:** matches recorded from now on carry `match_v2`. No match
  was ever recorded live under `match_v1`.

**The unmatched log.** Every unmatched game is still recorded in the
`unmatched` table and counted. Only the printing changes.
- **One `UNMATCHED …` line,** the first time, for a game that is both:
  - in scope: its Kalshi series has matched games for a sport key in
    `config/slate_sports.json`;
  - within the odds window: it resolves by now + `ODDS_HORIZON_H` + 3 hours,
    the time the reference listing covers.
- **Everything else** is one line per run:
  `UNMATCHED (not listed): <reason>=<n>, …`.
- **`UNMATCHED-REFERENCE` lines** (in-scope reference games with no Kalshi
  game) are unchanged.

## Amendment 2026-10-22: alias batch 1, NBA and NFL shared-city teams (PROPOSED, owner review)

**Match coverage before this batch** (`python -m research.studies.match_coverage`,
on a copy of the live data, 2026-10-22 8:5x PM PT). Kalshi series that have
paired at least once:

| Series | Reference key | Matched games | Never matched | Rate | Reasons |
|---|---|---|---|---|---|
| KXEPLGAME | soccer_epl | 1 | 0 | 100% | |
| KXLALIGAGAME | soccer_spain_la_liga | 1 | 0 | 100% | |
| KXMLBGAME | baseball_mlb | 1 | 0 | 100% | |
| KXNBAGAME | basketball_nba | 13 | 1 | 93% | name_mismatch 1 |
| KXNCAAFGAME | americanfootball_ncaaf | 8 | 31 | 21% | name_mismatch 31 |
| KXNFLGAME | americanfootball_nfl | 1 | 0 | 100% | |
| KXNHLGAME | icehockey_nhl | 14 | 0 | 100% | |
| **Total** | | **39** | **32** | **55%** | |

There are also 280 Kalshi games in series that have never paired: 124
soccer, 79 basketball, 75 hockey and 2 football. Most are leagues outside
`config/slate_sports.json`.

**The college football misses are not alias problems; they are a timing
problem** (corrected 2026-10-22).

**The correction.** An earlier draft of this amendment said these games
were absent from the reference listing. That was wrong. It was read from
`reference_events`, which stores only games starting within the odds
horizon (24 h). The full listing (`/events`, 0 credits, 2026-10-22) has 67
NCAAF games, including the missed Akron vs Kent State, Baylor vs Kansas,
Boston College vs Georgia Tech and Bowling Green vs Buffalo, all Saturday
2026-10-24 between 17:00Z and 19:30Z.

**The cause: Kalshi placeholder times.**
- **The placeholder:** those Kalshi games carry
  `expected_expiration_time = occurrence_time = 2026-10-24 03:00Z`. That
  is the end of the ticker's date, Oct 23, in Eastern time, about 14 hours
  before the real kickoff.
- **Why they fail:** rule 4 requires the reference start within
  [resolve_by − 8 h, resolve_by]. The real kickoff is after resolve_by, so
  no game can fit.
- **Why it says name_mismatch:** the reason recorded is `name_mismatch`
  only because Friday games were in that wrong window.
- **The fix belongs elsewhere:** a change to rule 4 for placeholder times,
  proposed separately. Kalshi's "St." form already matches through the
  `st` = `state` rule.

**The real abbreviation cases.** Kalshi shortens a team name to the city
plus a letter when two teams share a city. The observed Kalshi titles
(`yes_sub_title` in `markets`, game series only):

| Sport tag | Kalshi title | Seen in | Reference name |
|---|---|---|---|
| Basketball | `Los Angeles C` | KXNBAGAME (KXNBAGAME-26OCT23LACLAL, UNMATCHED name_mismatch) | `Los Angeles Clippers` |
| Basketball | `Los Angeles L` | KXNBAGAME (same game) | `Los Angeles Lakers` |
| Football | `Los Angeles C` | KXNFLGAME | `Los Angeles Chargers` |
| Football | `Los Angeles R` | KXNFLGAME | `Los Angeles Rams` |
| Football | `New York G` | KXNFLGAME | `New York Giants` |
| Football | `New York J` | KXNFLGAME | `New York Jets` |

- **The reference names were observed,** not assumed, in The Odds API
  `/v4/sports/{basketball_nba,americanfootball_nfl}/events` on 2026-10-22
  (`x-requests-last: 0`, no credits).
- **Without these aliases, each fails rule 5:** for example
  `los angeles clippers` does not start with `los angeles c` followed by a
  space. A single game between the two Los Angeles NBA teams, or a Giants
  or Jets game, could never pair.
- **The sport tag scopes each alias,** so Basketball `Los Angeles C` can
  never reach the Chargers, nor Football `Los Angeles C` the Clippers.
- **Each alias still fails safe:** it maps one exact Kalshi title to one
  exact reference name, so it can only add a pairing between those two
  names. Rules 2–6 (sport, time, distinct teams, unique both ways) still
  apply.

**Not in this batch:** the MLB shared-city titles (`Chicago C`,
`Chicago WS`, `Los Angeles A`, `Los Angeles D`, `New York M`,
`New York Y`). They appear in Kalshi's MLB futures series, but no MLB game
listing with those teams has been observed in the reference source yet.
They wait for an observed listing, per the rule above.

**Rule version:** unchanged, `match_v2`. Aliases are configuration, as in
the NHL amendment.
