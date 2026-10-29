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
    interval contains +0.0225. A favorite-longshot bias larger than about
    0.0082 lies outside it: 0.0065, plus the 0.0017 that the exclusions not
    covered by B (rule 7 and the candle window, below) could add. That is a reading of the interval, not the registered
    "ruled out" statement, which belongs to the not-rejected path.
  - **The four sports are not separate evidence.** All are positive
    (+0.009 to +0.014), but they are the same data split up, and none is
    rejected.
  - **The sign changes with price** (descriptive, not tested). The lowest
    bucket, 0.00–0.05, is overpriced: priced 0.0270, won 0.0123. 234 of its
    243 games are football. Most buckets from 0.15 to 0.45 are underpriced.
    A pooled Δ near zero or positive can hide both.
  - **The exploratory 5 h and 8 h runs are not replications.** They very
    likely use almost the same games (INFERRED from the counts; the overlap
    was not computed) (10,082 and 9,838, against 10,048), priced a few
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

All figures in this section are OBSERVED output of the committed report
script `research/studies/kalshi_calibration_postresult.py` (run on dataset
`34babdc762e232b0`). Fee figures use 0.07 × p × (1 − p) at multiplier 1,
because the weather series' multiplier is not in a cited fee table.

**Weather, by price bucket** (sum of (y − p), brackets priced under 0.50,
13,471 brackets, total −148.4):

| Bucket | Brackets (not independent) | Σ(y − p) | Σ(y − bid) | Median half-spread | Share with bid 0.01 |
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
- **Nearly half of the lowest bucket (46%) is quoted at the tick floor**
  (bid 0.01). That may hold its mid up (INFERRED). Nothing here measures
  how much of the deficit the floor causes.
- **Selling earns the bid, not the mid.** Like for like, per bracket (a
  plain average over the 13,471 brackets, not the per-city-day Δ):
  - mean (y − mid) is −0.0110;
  - mean (y − bid) is −0.0018. So a seller at the bid would have *earned*
    0.18 cents per bracket before fees;
  - after the fee at multiplier 1, the same seller **loses 0.56 cents per
    bracket** on average.

  This is a point estimate after the modelled fee, with no interval, and
  the brackets are not independent (several share one city-day). It
  points the conservative way.
- **The brackets under 0.30 are the obvious post-hoc candidate.** A seller
  at the bid there shows +1.06 cents per bracket before fees, and +0.55
  cents after the fee at multiplier 1 (10,672 brackets). This too is a
  point estimate, with no interval, and the brackets are not independent.
  - The after-fee gain is in the buckets under 0.20 (+0.64, +1.08 and
    +0.86 cents per bracket). The 0.20–0.30 bucket loses 0.64 cents per
    bracket after the fee.
  - This slice was chosen after seeing the table, so it is a hypothesis,
    not evidence.
  - It could only be tested under a new pre-registration, on data after
    this dataset's last date, priced at the bid after fees.
  - If Kalshi rounds fees up per order (INFERRED, not in any repo file),
    the small gain in the lowest bucket could disappear.

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
- **The 14-day edge case:** recounted with close − t > 14 days − 6 hours,
  to cover a game that lost only part of its 6-hour lookback. Still 6
  (OBSERVED, from the post-result script).
- **Together** they could widen the sports interval by about 0.0017 more.
  That does not change the non-claim.
- **Weather:** rule 7 removed 2 brackets as "not settled yes or no" (from
  `result.md`). That exclusion reads outcomes and is not covered by B
  (which is 0 for weather). It could move the weather Δ by about 2/2,557 ≈
  0.0008 at most, against an interval whose upper end is −0.0097, so the
  claim stands. The candle window does not apply to weather: t is about a
  day before close (INFERRED from the measured-date design and the
  scheduled close).

## Records

- **Zero-count rules** are not printed by the script; its funnel prints
  only rules that fired. Three are at 0:
  - "not a two-market binary game" and "no expected expiration": 10,331 −
    59 − 109 = 10,163;
  - the weather "no measured date in the ticker": 15,342 + 30 = 15,372.

  The weather rule was not in the pre-registration. Its count is 0, so it changed
  nothing.
- `result.md` does not name the script commit. It is `586ca79`. The run
  record is in `docs/validation_log.md`.
- **For future study scripts:**
  - print the registered consequence for every claimed group;
  - print the widened interval on every decision path;
  - flag cells under 20;
  - print zero-count rules;
  - name the script commit in the output.
