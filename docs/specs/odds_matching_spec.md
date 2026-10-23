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

## Amendment 2026-10-22: Kalshi placeholder times (rule version `match_v3`, PROPOSED, owner review)

**The problem (observed 2026-10-22).** Kalshi lists many Saturday college
games with a placeholder time. For example, KXNCAAFGAME-26OCT23AKRKENT has
`expected_expiration_time = occurrence_time = 2026-10-24 03:00Z`. That is
23:00 Eastern on the ticker's date (Oct 23). The game kicks off at
2026-10-24 17:00Z, 14 h after `resolve_by`. Rule 4 (reference start within
[resolve_by − 8 h, resolve_by]) can never pass. In week 43, 27 of the 45
college games that match a reference game uniquely by names were blocked
this way. See `docs/proposals/college_scope.md`.

**1. The detection rule (exact).** A Kalshi game event is a
*placeholder event* only if all of the following hold:
1. **Ticker date:** its event ticker carries a date as the first
   `-YYMMMDD` token after the series prefix, for example `-26OCT23` =
   2026-10-23. Digits may follow (a start time, as in `-26OCT231430`); the
   date is the first seven characters.
2. **The same placeholder in every market:** every market of the event
   has `expected_expiration_time` equal to `occurrence_time`, and that
   value is the same for all of them.
3. **The placeholder value:** that value is exactly 23:00:00 in
   America/New_York on the ticker date, converted to UTC: 03:00Z under
   daylight time, 04:00Z under standard time. The conversion is
   `timeutil.eastern_to_utc`.

An event that fails any part is not a placeholder event, and rule 4 applies
unchanged.

**2. The widened window, only after the normal window finds nothing.** For
a placeholder event:
- **Normal first:** candidates are first sought with rule 4 as written.
- **Widen only on an empty result:** only if that finds **no** candidate are
  candidates sought again with the time rule
  [resolve_by − 8 h, resolve_by + 32 h]. That covers the ticker's date and
  the following day in every US time zone.
- **Why two steps:** a genuine game whose real end happens to be 23:00 ET
  (an 8 p.m. start) matches in the normal window exactly as today. A
  false detection can therefore never turn a good match into an
  ambiguous one, for example two MLB games between the same teams on
  consecutive days.

**3. Every uniqueness rule is kept.** Rules 1, 2, 3, 5 and 6 apply to the
widened candidates exactly as before:
- both Kalshi teams must match two distinct reference teams;
- exactly one reference game may fit the Kalshi event;
- that reference game may fit no other Kalshi event.

Two candidates in the widened window refuse as `ambiguous`, and a
reference game also claimed by another event refuses.

**4. The game time is the reference start.** Once matched, a placeholder
event's time comes from the reference game everywhere downstream. That is
already true:
- check scheduling and clusters use the reference listing's latest start
  (`checks.latest_starts`);
- the pre-game rule uses the reference read's `commence_utc`
  (`shadow.evaluate`);
- markouts use the latest listed start.

Kalshi's `resolve_by` is used only to choose which matched markets are
snapshotted (`snapshot.matched_event_tickers`: resolve_by > now − 2 days;
near tier: resolve_by ≤ now + 48 h). A placeholder that is too early keeps
the market snapshotted at least as long as before, and 2 days covers a
real start 14 h later.

**5. The listing window.** The odds step rebuilds candidate Kalshi games
from markets resolving in [now, now + horizon + 8 h]. For placeholder
events only, the lower bound becomes now − 32 h. A placeholder event
therefore stays a candidate until its widened window has passed. Without
this, a Saturday game would be considered only for the ~10 hours when both
its placeholder and its reference listing are in range.

**6. Logging.** An unmatched placeholder event's detail gains
`placeholder=1`. Reasons are unchanged (`no_candidate`, `name_mismatch`,
`ambiguous`), with `in_window` counted over the window that was searched
last.

**7. Rule version:** matches recorded from now on carry `match_v3`.
Earlier rows keep `match_v2`; nothing is relabelled.

**8. Tests the implementation must have:**
- **A placeholder event pairs correctly:** the Akron vs Kent State case,
  placeholder 03:00Z, reference start 14 h later.
- **A non-placeholder event with a wrong time still refuses:** the same
  teams and reference game, with the Kalshi time at 02:00Z (not 23:00 ET).
- **Two candidate games still refuse:** a placeholder event with two
  reference games of the same teams inside the widened window gives
  `ambiguous`.
- **A false detection cannot break a normal match:** an event whose real
  end is 23:00 ET, with a same-day candidate and a next-day duplicate,
  matches the same-day game.
- **Detection:** EDT (03:00Z) and EST (04:00Z) both detected; a minute off,
  occurrence ≠ expected, markets disagreeing, or no date in the ticker are
  not detected.
- **The listing window:** a placeholder event 20 h past its `resolve_by`
  is still a candidate; a non-placeholder event is not.
