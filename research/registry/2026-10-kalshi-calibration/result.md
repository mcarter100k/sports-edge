# Result: Kalshi calibration by price bucket and sport

Study `2026-10-kalshi-calibration`. Written by `research/studies/kalshi_calibration.py`; never edited by hand.

- Dataset: `kalshi-backfill` id `34babdc762e232b0`
- Tables: candles 2684145 rows, markets 36034 rows, outcomes 36034 rows
- Leakage check (registered run): every tested price — 46071 decisions, including excluded and exploratory ones — was also predicted on a separate connection holding only quotes at or before its decision time, with no outcomes, and all were identical. This checks the quotes used at t, not the choice of t; t comes from the timing rules (pre-registration section 3).

## Inclusion

- Sports: excluded games: no two-sided quote within 6 h: 88
- Sports: excluded games: not settled yes or no: 11
- Sports: excluded games: spread over 10 cents: 16
- Sports: excluded: decision time outside the market's open period: 59
- Sports: excluded: market closed too soon after the decision (game may have started): 109
- Sports: games: 10331
- Sports: games with a decision time: 10163
- Weather: bracket markets with a decision time: 15342
- Weather: city-days: 2562
- Weather: excluded markets: no two-sided quote within 6 h: 810
- Weather: excluded markets: not settled yes or no: 2
- Weather: excluded markets: not yet listed at the decision time: 30
- Weather: excluded markets: spread over 10 cents: 214
- Sports games scored: 10048; weather city-days with a bracket under 0.50 scored: 2557 (14316 bracket markets priced)
- Rule-4 exclusions with a tested price (used for the exclusion bound): Football 76, Basketball 9, Hockey 4, Baseball 35

## Primary test: pooled sports, underdog excess Δ = mean(y − p) on the underdog side

Δ < 0 means underdogs win less often than their price says (favorite-longshot bias). Units are games; intervals resample Los Angeles dates of the decision.

| group | units | dates | Δ | SE | 95% interval | p | exclusion bound B |
|---|---|---|---|---|---|---|---|
| Sports (pooled) | 10048 | 542 | +0.0112 | 0.0047 | [+0.0024, +0.0205] | 0.0166 | 0.0089 |

Decision: **H0 rejected, not claimed: the interval widened by the exclusion bound B includes 0**.

Power (stated before the run): with sd 0.5, |Δ| = 0.02 needs 4906 games; 10048 were scored, so the smallest effect detectable at 80% power is 0.0140, before any design effect from clustering. Under the claim rule (registered before the run, sd 0.5, before any design effect): the smallest claimable |Δ̂| is 0.0187, the power to claim a true Δ of 0.02 is 0.60, and 80% claim power needs a true Δ of 0.0229. A non-claim is to be read with these numbers.

Follow-up threshold (registered before the run): |Δ| ≥ 0.0225 (fee at p = 0.5 with multiplier 1, 0.0175, plus the pre-run median half-spread, 0.0050). Not met. Run-time median half-spread of the scored games, for reference: 0.0050. The follow-up needs both a claim and |Δ̂| ≥ 0.0225, so it triggers with 80% probability only at a true Δ of about 0.0267.

## Secondary family (Benjamini–Hochberg, q = 0.05, always five tests)

| group | units | dates | Δ | SE | 95% interval | p | BH adjusted p | exclusion bound B | rejected and claimed |
|---|---|---|---|---|---|---|---|---|---|
| Football | 2152 | 178 | +0.0091 | 0.0104 | [-0.0107, +0.0313] | 0.3860 | 0.3860 | 0.0252 | not rejected |
| Basketball | 1520 | 279 | +0.0141 | 0.0111 | [-0.0071, +0.0377] | 0.2012 | 0.3354 | 0.0045 | not rejected |
| Hockey | 1742 | 294 | +0.0117 | 0.0118 | [-0.0113, +0.0345] | 0.3220 | 0.3860 | 0.0019 | not rejected |
| Baseball | 4634 | 385 | +0.0111 | 0.0074 | [-0.0034, +0.0256] | 0.1358 | 0.3354 | 0.0051 | not rejected |
| Weather | 2557 | 366 | -0.0124 | 0.0014 | [-0.0151, -0.0097] | 0.0000 | 0.0000 | 0.0000 | yes |

Power to claim per sport (registered before the run, sd 0.5, before any design effect): Football smallest claimable |Δ̂| 0.0476, power at Δ = 0.02 0.01, 80% at Δ = 0.0570; Basketball smallest claimable |Δ̂| 0.0297, power at Δ = 0.02 0.23, 80% at Δ = 0.0405; Hockey smallest claimable |Δ̂| 0.0254, power at Δ = 0.02 0.33, 80% at Δ = 0.0355; Baseball smallest claimable |Δ̂| 0.0195, power at Δ = 0.02 0.52, 80% at Δ = 0.0258.

Weather Δ is, per city-day, the mean of (y − p) over brackets priced under 0.50 at local midnight. It is driven mostly by whether the winning bracket is one of them: when it is, the value is (1 − Σp) / k. Weather clusters are measured dates. Weather power (stated before the run): sd 0.0709 from prices, so the smallest effect detectable at 80% power with 2557 city-days is 0.0039.

## Calibration tables (descriptive)

**Sports, underdog side (pooled)**

| bucket | games | mean price | observed | 95% interval (Wilson; unadjusted, not a test) |
|---|---|---|---|---|
| 0.00–0.05 | 243 | 0.0270 | 0.0123 | [0.0042, 0.0357] |
| 0.05–0.10 | 245 | 0.0741 | 0.0612 | [0.0375, 0.0985] |
| 0.10–0.15 | 289 | 0.1252 | 0.1107 | [0.0795, 0.1521] |
| 0.15–0.20 | 345 | 0.1755 | 0.2058 | [0.1665, 0.2516] |
| 0.20–0.25 | 402 | 0.2266 | 0.2537 | [0.2137, 0.2985] |
| 0.25–0.30 | 591 | 0.2760 | 0.2555 | [0.2220, 0.2922] |
| 0.30–0.35 | 916 | 0.3263 | 0.3406 | [0.3106, 0.3719] |
| 0.35–0.40 | 1585 | 0.3768 | 0.4057 | [0.3818, 0.4301] |
| 0.40–0.45 | 2555 | 0.4265 | 0.4513 | [0.4321, 0.4706] |
| 0.45–0.50 | 2877 | 0.4751 | 0.4724 | [0.4542, 0.4906] |

Brier 0.22176 = reliability 0.00041 − resolution 0.01440 + uncertainty 0.23614 + within-bin -0.00039

**Football, underdog side**

| bucket | games | mean price | observed | 95% interval (Wilson; unadjusted, not a test) |
|---|---|---|---|---|
| 0.00–0.05 | 234 | 0.0267 | 0.0128 | [0.0044, 0.0370] |
| 0.05–0.10 | 184 | 0.0728 | 0.0761 | [0.0459, 0.1237] |
| 0.10–0.15 | 165 | 0.1246 | 0.1030 | [0.0653, 0.1588] |
| 0.15–0.20 | 205 | 0.1747 | 0.1902 | [0.1424, 0.2495] |
| 0.20–0.25 | 212 | 0.2271 | 0.2830 | [0.2267, 0.3471] |
| 0.25–0.30 | 206 | 0.2749 | 0.2573 | [0.2024, 0.3210] |
| 0.30–0.35 | 230 | 0.3243 | 0.3739 | [0.3139, 0.4380] |
| 0.35–0.40 | 249 | 0.3740 | 0.3896 | [0.3311, 0.4514] |
| 0.40–0.45 | 266 | 0.4246 | 0.4286 | [0.3705, 0.4886] |
| 0.45–0.50 | 201 | 0.4730 | 0.4627 | [0.3951, 0.5317] |

Brier 0.17388 = reliability 0.00072 − resolution 0.02256 + uncertainty 0.19602 + within-bin -0.00030

**Basketball, underdog side**

| bucket | games | mean price | observed | 95% interval (Wilson; unadjusted, not a test) |
|---|---|---|---|---|
| 0.00–0.05 | 9 | 0.0344 | 0.0000 | [0.0000, 0.2991] |
| 0.05–0.10 | 61 | 0.0783 | 0.0164 | [0.0029, 0.0872] |
| 0.10–0.15 | 123 | 0.1261 | 0.1138 | [0.0690, 0.1820] |
| 0.15–0.20 | 140 | 0.1768 | 0.2286 | [0.1668, 0.3048] |
| 0.20–0.25 | 150 | 0.2240 | 0.2267 | [0.1670, 0.3000] |
| 0.25–0.30 | 162 | 0.2735 | 0.2654 | [0.2034, 0.3383] |
| 0.30–0.35 | 228 | 0.3238 | 0.3421 | [0.2836, 0.4058] |
| 0.35–0.40 | 223 | 0.3758 | 0.3991 | [0.3371, 0.4646] |
| 0.40–0.45 | 231 | 0.4247 | 0.4242 | [0.3623, 0.4887] |
| 0.45–0.50 | 193 | 0.4743 | 0.5337 | [0.4633, 0.6027] |

Brier 0.20136 = reliability 0.00101 − resolution 0.01812 + uncertainty 0.21891 + within-bin -0.00044

**Hockey, underdog side**

| bucket | games | mean price | observed | 95% interval (Wilson; unadjusted, not a test) |
|---|---|---|---|---|
| 0.20–0.25 | 15 | 0.2327 | 0.2000 | [0.0705, 0.4519] |
| 0.25–0.30 | 80 | 0.2784 | 0.2250 | [0.1473, 0.3279] |
| 0.30–0.35 | 141 | 0.3296 | 0.3333 | [0.2609, 0.4147] |
| 0.35–0.40 | 333 | 0.3766 | 0.3934 | [0.3424, 0.4468] |
| 0.40–0.45 | 557 | 0.4263 | 0.4560 | [0.4151, 0.4975] |
| 0.45–0.50 | 616 | 0.4749 | 0.4789 | [0.4397, 0.5184] |

Brier 0.24084 = reliability 0.00048 − resolution 0.00446 + uncertainty 0.24501 + within-bin -0.00019

**Baseball, underdog side**

| bucket | games | mean price | observed | 95% interval (Wilson; unadjusted, not a test) |
|---|---|---|---|---|
| 0.10–0.15 | 1 | 0.1200 | 1.0000 | [0.2065, 1.0000] |
| 0.20–0.25 | 25 | 0.2350 | 0.2000 | [0.0886, 0.3913] |
| 0.25–0.30 | 143 | 0.2792 | 0.2587 | [0.1939, 0.3362] |
| 0.30–0.35 | 317 | 0.3283 | 0.3186 | [0.2698, 0.3718] |
| 0.35–0.40 | 780 | 0.3780 | 0.4179 | [0.3838, 0.4529] |
| 0.40–0.45 | 1501 | 0.4271 | 0.4577 | [0.4326, 0.4830] |
| 0.45–0.50 | 1867 | 0.4755 | 0.4649 | [0.4424, 0.4876] |

Brier 0.24352 = reliability 0.00081 − resolution 0.00282 + uncertainty 0.24603 + within-bin -0.00049

**Weather, every bracket**

| bucket | brackets (not independent) | mean price | observed | 95% interval (Wilson; unadjusted, not a test) |
|---|---|---|---|---|
| 0.00–0.05 | 4770 | 0.0251 | 0.0111 | [0.0085, 0.0145] |
| 0.05–0.10 | 1934 | 0.0701 | 0.0450 | [0.0366, 0.0552] |
| 0.10–0.20 | 2183 | 0.1449 | 0.1177 | [0.1049, 0.1319] |
| 0.20–0.30 | 1785 | 0.2486 | 0.2314 | [0.2124, 0.2515] |
| 0.30–0.40 | 1598 | 0.3478 | 0.3586 | [0.3354, 0.3824] |
| 0.40–0.50 | 1201 | 0.4434 | 0.4763 | [0.4481, 0.5045] |
| 0.50–0.60 | 537 | 0.5425 | 0.5810 | [0.5388, 0.6220] |
| 0.60–0.70 | 192 | 0.6385 | 0.6615 | [0.5919, 0.7246] |
| 0.70–0.80 | 56 | 0.7412 | 0.8036 | [0.6816, 0.8866] |
| 0.80–0.90 | 44 | 0.8532 | 0.9545 | [0.8487, 0.9874] |
| 0.90–0.95 | 11 | 0.9173 | 0.9091 | [0.6226, 0.9838] |
| 0.95–1.00 | 5 | 0.9710 | 1.0000 | [0.5655, 1.0000] |

Brier 0.10682 = reliability 0.00051 − resolution 0.03693 + uncertainty 0.14395 + within-bin -0.00072

## Harness scores against the baselines

**Sports games** (harness score, registered run; 10048 rows; leakage check on all 10048 decisions passed)

- Kalshi mid (the source): 10048 units, Brier 0.22176, log loss 0.62902
- Kalshi mid baseline: 10048 units, Brier 0.22176, log loss 0.62902
- Pinnacle fair baseline: not available (no Pinnacle fair prices at decision time in this dataset)

**Weather brackets** (harness score, registered run; 14316 rows; leakage check on all 14316 decisions passed)

- Kalshi mid (the source): 2557 units, Brier 0.10647, log loss 0.33511
- Kalshi mid baseline: 2557 units, Brier 0.10647, log loss 0.33511
- Pinnacle fair baseline: not available (no Pinnacle fair prices at decision time in this dataset)

## Exploratory (not in any test family)

- Sports with the decision 5 h before expected expiration: Δ +0.0117, 95% interval [+0.0023, +0.0207], 10082 games
- Sports with the decision 8 h before expected expiration: Δ +0.0129, 95% interval [+0.0038, +0.0221], 9838 games

## What this does and does not show

- This measures calibration of Kalshi's mid price at one decision time. It is not a trading result: a taker pays half the spread and a fee of 0.07 × p × (1 − p) per contract (multiplier assumed 1), so a miscalibration smaller than those costs cannot be traded.
- No result here is an edge. A claimed rejection is a hypothesis for a prospective, fee-adjusted shadow test on data after this dataset.
