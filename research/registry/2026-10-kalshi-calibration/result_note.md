# Reading note: 2026-10-kalshi-calibration result

Written 2026-10-23, after the result review
(`docs/reviews/2026-10-23_2026-10-kalshi-calibration_result.md`, verdict
REVISE). `result.md` is unchanged: it was written by the study script and
is PROTECTED. This note says what the result means under the registered
rules, and what it does not mean.

- **Every number in the "Post-result descriptive figures" section was
  computed after the result was read.** Those figures are not tests and
  are not evidence for anything.
- The rest of the note restates registered rules, or adds counts from
  timing fields only.

## The registered consequences

**Primary, pooled sports:**
- **The finding:** Δ = +0.0112, the 95% interval is [+0.0024, +0.0205],
  p = 0.017, and B = 0.0089. H0 is rejected by the z test but **not
  claimed**: the interval widened by B is [−0.0065, +0.0294], which
  includes 0.
- **The registered consequence:** none beyond recording it. The follow-up
  threshold (|Δ| ≥ 0.0225) is not met, and would not have been met even
  with a claim, because 0.0112 < 0.0225.
- **What a reader must know:**
  - **The direction** is the reverse of the favorite-longshot hypothesis:
    underdogs won slightly more often than their price said.
  - **The non-claim comes only from the worst-case bound.** B assumes all
    124 rule-4-excluded games with a price had gone the worst possible way.
  - **A reverse bias of tradeable size is not ruled out.** The widened
    interval contains +0.0225. A favorite-longshot bias larger than 0.0065
    lies outside it. That is a reading of the interval, not the registered
    "ruled out" statement, which belongs to the not-rejected path.
  - **The four sports are not separate evidence.** All are positive
    (+0.009 to +0.014), but they are the same data split up, and none is
    rejected.
  - **The sign changes with price** (descriptive, not tested). The lowest
    bucket, 0.00–0.05, is overpriced: priced 0.0270, won 0.0123. 234 of its
    243 games are football. Most buckets from 0.15 to 0.45 are underpriced.
    A pooled Δ near zero or positive can hide both.
  - **The exploratory 5 h and 8 h runs are not replications.** They use
    almost the same games (10,082 and 9,838, against 10,048), priced a few
    hours apart, so their agreement (+0.0117, +0.0129) is close to
    automatic.

**Secondary, weather:**
- **The finding:** Δ = −0.0124, the 95% interval is [−0.0151, −0.0097],
  BH-adjusted p < 0.0001, and B = 0. **Claimed.**
- **The registered consequence** (section 9): |Δ| = 0.0124 < 0.0225, so
  this is **"a clear but untradeable miscalibration. Record it."** No
  follow-up study is registered.
- **The closing line of `result.md` does not apply here.** It says "a
  claimed rejection is a hypothesis for a prospective, fee-adjusted shadow
  test", but section 9 reserves that for |Δ| ≥ 0.0225. The 0.0225
  threshold is per contract at p = 0.5 for one game, while the weather
  value is a per-city-day mean over brackets, so it is not even on the same
  scale. Any weather follow-up needs its own pre-registration, with a cost
  threshold on the weather scale and prices taken at the bid after fees.
- **What the claim means:** the **mid** overstates the sub-0.50 brackets,
  taken together, at local midnight of the measured day. It does **not**
  show that weather traders overprice longshots, and it says nothing about
  a profitable sale (next section).

## Post-result descriptive figures (computed after reading the result; not evidence)

**Weather, by price bucket** (sum of (y − p), brackets priced under 0.50,
13,471 brackets, total −148.4):

| Bucket | Brackets | Σ(y − p) | Σ(y − bid) | Median half-spread | Share with bid 0.01 |
|---|---|---|---|---|---|
| 0.00–0.05 | 4,770 | −66.6 | −36.9 | 0.005 | 46% |
| 0.05–0.10 | 1,934 | −48.6 | −28.4 | 0.005 | 1% |
| 0.10–0.20 | 2,183 | −59.2 | −36.4 | 0.005 | 0% |
| 0.20–0.30 | 1,785 | −30.8 | −11.0 | 0.005 | 0% |
| 0.30–0.40 | 1,598 | +17.3 | +35.3 | 0.005 | 0% |
| 0.40–0.50 | 1,201 | +39.5 | +53.5 | 0.005 | 0% |

**What the table shows:**
- **The deficit sits in the brackets priced under 0.30.** Brackets from
  0.30 to 0.50 run the other way.
- **Much of the lowest bucket is pinned at the tick floor.** Nearly half of
  it has a bid of 0.01, so its mid is set by the quote format, not by a
  view.
- **Selling earns the bid, not the mid.** Measured against the bid, the
  average over all sub-0.50 brackets is **−0.0018 per bracket**, before any
  fee. That is close to nothing.
  - The fee for weather series is not in any cited fee table. At
    multiplier 1 it is 0.07 × p × (1 − p) per contract, for example 0.6
    cents at p = 0.1.
  - Choosing the sub-0.30 buckets after seeing this table would be
    post-hoc selection. It is not evidence of an edge.

**Small cells** in the calibration tables (fewer than 20 units) carry no
information (AGENTS.md rule 9):
- Basketball 0.00–0.05 (n 9);
- Hockey 0.20–0.25 (n 15);
- Baseball 0.10–0.15 (n 1, observed 1.0000);
- Weather 0.90–0.95 (n 11) and 0.95–1.00 (n 5).

**The harness baselines** in `result.md` ("Kalshi mid baseline") equal the
source's scores by construction, and validate nothing.

## Exclusions not covered by B (timing fields and registered counts only)

- **Rule 7 voids:** 11 sports games, decided by reading outcomes. The
  largest possible shift is 11/10,059 ≈ 0.0011.
- **The backfill candle window** ([max(open, close − 14 days), close])
  could remove a game's candles at t if its market closed more than 14 days
  after t. Of the 88 rule-5 sports exclusions, **6** did so (Baseball 5,
  Football 1). The largest possible shift is 6/10,054 ≈ 0.0006.
- **Together** they could widen the sports interval by about 0.0017 more.
  That does not change the non-claim. For weather, t is about a day before
  close, so neither applies.

## Records

- **Zero-count rules** are not printed by the script; its funnel prints
  only rules that fired. Two are at 0:
  - "no expected expiration": 10,331 − 59 − 109 = 10,163;
  - the weather "no measured date in the ticker": 15,342 + 30 = 15,372.

  The second was not in the pre-registration. Its count is 0, so it changed
  nothing.
- `result.md` does not name the script commit. It is `586ca79`. The run
  record is in `docs/validation_log.md`.
- **For future study scripts:**
  - print the registered consequence for every claimed group;
  - print the widened interval on every decision path;
  - flag cells under 20;
  - print zero-count rules;
  - name the script commit in the output.
