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
`ODDS_HORIZON_H` + 8 h and has no match gets a line on stderr (and so in
the day log):

`UNMATCHED <event_ticker> sport=<tag> reason=<no_candidate|ambiguous|name_mismatch|outcome_count|not_moneyline> detail=<…>`

It also gets a row in `unmatched`. The run summary prints the unmatched
count per sport, and the heartbeat reports the weekly total. A match that
later changes is a conflict: it is logged as `MATCH CONFLICT`, and that
event is never compared again.
