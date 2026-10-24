# Pre-registration: <study name>

Copy this file to `research/registry/<YYYY-MM>-<short-name>/prereg.md`, fill
in every section, and commit it **before** the study script reads any
outcome. Then have it reviewed (`docs/reviews/`). Nothing here is edited
after the study has run. A change is a new pre-registration with its own
name.

## 1. Question and hypothesis

- **Question:** one sentence.
- **H0:** the null, stated as a number (for example, mean(y − p) = 0).
- **H1:** the alternative, two-sided unless a one-sided test is justified
  here, in advance.
- **Why it matters for trading:** what would change if H0 is rejected or
  not.

## 2. Data

- **Dataset:** name, id (16 hex), and the sha256 of `dataset_manifest.json`
  (a copy is committed next to this file).
- **Tables and fields used.**
- **Inclusion rules,** applied in this order, each with its count from the
  dataset (counts only, never outcomes).
- **Exclusion rules.**

## 3. Decision time and leakage

- **Decision time per forecast,** and how it is derived. Only fields known
  before the event may set it.
- **The price or probability used** at decision time, and its maximum age.
- **The leakage rule:** nothing timestamped after decision time is read,
  except the outcome used for scoring.

## 4. Unit of N and independence

- **The unit:** for example, one game, one city-day.
- **How dependent rows collapse to one unit:** for example, both sides of
  a game become one observation.
- **Clustering for intervals:** for example, by Los Angeles calendar date.

## 5. Primary metric and test

- **Metric:** a formula.
- **Test:** for example, a z test on the mean, with a cluster-bootstrap 95%
  interval.
- **Significance level α.**

## 6. Power

- **The assumed sd** of the per-unit metric, and where it comes from (a
  formula or prior data, not the outcomes of this dataset).
- **The smallest effect worth detecting (δ)**, and why.
- **The N needed:** `research.stats.n_for_effect(sd, δ, α, 0.8)`.
- **The N available**, and the effect detectable at that N
  (`detectable_effect`).
- **If the N available is below the N needed,** say what can still be
  concluded.

## 7. Secondary analyses

- A list. Each is labelled exploratory unless it is in the
  multiple-testing family below.

## 8. Multiple testing

- **The family of tests,** and the correction (Benjamini–Hochberg,
  q = 0.05, unless stated otherwise).

## 9. Decision rules

- **Reject H0 if:** …
- **Do not reject if:** …
- **What each outcome leads to:** next study, kill, or promote to
  prospective testing.
- **INSUFFICIENT if** N < 20 units in a group (AGENTS.md rule 9).

## 10. Disclosure

- **What was looked at before registering:** counts, field names,
  timestamps, any outcome seen by accident, and when.
- **Code version:** the commit of the study script.
- **Determinism:** the same dataset id gives byte-identical output.
