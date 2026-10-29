# Proposal: closing the gaps (prop odds, injury news, history)

Status: **PROPOSED, 2026-10-28.** Nothing is built, bought or spent. The
items marked **Owner** are decisions only the owner can make.

## Why these gaps

Everything here is exploratory, not registered evidence. It comes from
2026-10-22 to 2026-10-28:
- **Game winners:** Kalshi tracks the sharp books. The shadow rule logged
  no side 3¢ or more under fair after the fee.
- **College football spreads:** at the books' exact line, Kalshi's mid
  equalled the books' fair price on 37 games (mean gap 0.0¢).
- **Weather:** cheap brackets lost about 49% after fees (840 city-days).
  This agrees with the registered calibration study's weather result.
- **NBA player props:** the only place gaps showed. On exact-line legs,
  Kalshi mids averaged about 1.5¢ under the books' no-vig fair, with a few
  legs 3–4¢. That was a manual, one-night scan, and it proves nothing yet.

So the gaps worth closing are the ones that would test the prop finding
honestly, and the ones that let a small player act on news before a slow
price moves.

## Gap 1: player-prop odds

**Today:** the system has sharp odds for moneylines only. Prop odds were
copied by hand from a public page.

**Options** (vendor pages read 2026-10-28; claims marked C):

| Source | Props and alternate lines | Sharp book for props | Cost |
|---|---|---|---|
| the-odds-api (current vendor) | yes, including `_alternate` markets; US books, DFS books, Kalshi itself | Pinnacle props: not found | $59/month for 100K credits |
| SportsGameOdds | yes | Pinnacle claimed on Pro (C) | $299/month |
| OpticOdds, OddsJam | yes (C) | — | about $5K/month (C) |
| Pinnacle direct | public API closed 2025-07-23; research access by application | — | free if granted |

**Credits** (INFERRED from the vendor's documented formula):
- points, rebounds, assists and threes, each main plus alternate, is 8
  markets per event per call;
- polled hourly for 6 hours on an average 7.5-game NBA night, that is
  about 360 credits a day, or about 11K a month;
- that fits the $59 plan. Today's budget is 15 credits a day.

**Steps:**
0. **Owner:** approve spending 1 credit on
   `/events/{id}/markets?bookmakers=pinnacle` for one NBA game. It answers
   whether this vendor carries Pinnacle props.
1. **Pre-register the test** under `research/registry/`, before any data:
   - **Claim:** Kalshi NBA prop mids sit below the books' no-vig fair at the
     exact line.
   - **Reference:** Pinnacle if available. Otherwise the median no-vig
     price of at least 3 US books, which makes the reference itself a
     hypothesis.
   - **Exact-line rungs only.** Alternate lines are one-sided and would
     need an interpolation model, which is out of Phase 1 scope.
   - **Independent N:** one leg per player per game, with power computed
     first.
2. **Spec, then code: a prop reference source.**
   - Player-name matching amends `odds_matching_spec.md`. `matching.py`
     is **PROTECTED**, so it needs owner review.
   - Props get a **new `rule_id`** in the shadow ledger. Moneyline rows are
     never relabelled.
3. **Record and grade** after settlement, like the moneyline shadow rule.

**Risks:**
- There may be no sharp book for props. Pinnacle and Circa hang few,
  low-limit props (C).
- A consensus of retail books can be wrong together.

## Gap 2: injury and lineup news

**Today:** news is searched by hand, minutes to hours late.

**Steps:**
1. **Free: poll the official NBA injury report.**
   - It is published as a PDF at `ak-static.cms.nba.com/referee/injury/...`.
     Filenames step every 15 minutes (ET).
   - Teams file by 5 PM local the day before a game, with a game-day
     update 11 AM–1 PM local.
   - Poll every 15 minutes from noon ET to the last tip.
   - Keep the raw PDFs append-only, and diff each one against the last.
   - **Rule 8:** "no status changed" must be told apart from "the parser
     read nothing". A parse that finds no rows is a failure, not a quiet
     day.
2. **Tie it to the slip watcher.** A status change for a player on a
   slip being watched is texted (facts only).
3. **Owner, optional and paid:** cover the gap between the last report and
   tip (confirmed starters, late scratches) with RotoWire (quote needed)
   or the X API (about $50–150/month, INFERRED).

**Risks:**
- The report format has changed before (C).
- Confirmed starters arrive about 30 minutes before tip, outside the
  report.

## Gap 3: history

Nothing to build or buy. The snapshot job keeps recording every 15
minutes, and the registered calibration study already uses a frozen
backfill. The weekly heartbeat and the job alerts keep the recording
honest.

## Order

| # | Step | Cost | Needs |
|---|---|---|---|
| 1 | Injury-report poller (Gap 2 step 1) | $0 | spec, then build, then the gates |
| 2 | Pinnacle-props check (Gap 1 step 0) | 1 credit | **Owner** OK |
| 3 | Prop test pre-registration (Gap 1 step 1) | $0 | auditor review |
| 4 | Prop source and shadow rule (Gap 1 steps 2–3) | $59/month | **Owner**: budget; protected-file review |
| 5 | Late-news feed (Gap 2 step 3) | about $50–150/month or a quote | **Owner**: budget |

## Owner decisions

1. **Budget:** $59/month (the-odds-api 100K), $299 (SportsGameOdds Pro, for
   claimed Pinnacle props), or none for now.
2. **May we spend 1 credit** on the Pinnacle-props check?
3. **If no sharp book prices props,** is a no-vig consensus of retail books
   acceptable as the reference? It would be pre-registered as a hypothesis.
4. **Should we apply for Pinnacle's research API access?**
5. **Scraping:** reading NBA.com, ESPN or RotoWire pages is NOT CHECKED
   against their terms. The slip watcher already reads ESPN's public JSON
   for display only.
6. **Sports beyond the NBA.** NFL props roughly double the credits
   (INFERRED).
