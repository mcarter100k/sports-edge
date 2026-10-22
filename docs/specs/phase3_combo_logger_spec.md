# Spec: Phase 3 — passive combo-price logger (PARKED)

**Status: PARKED 2026-10-22 by owner direction. No code exists or may be
written until the unpark gate below is met and the owner unparks it.**

## What it would be

A **record-only** logger for same-game combo trade prints (`KXMVE*`
multivariate markets). It never creates an RFQ, never quotes, never
places an order, and never authenticates to a trading endpoint, the same
as Phase 1. For each observed same-game combo print it would record:

1. **The fair joint probability**, built from the de-vigged single-leg
   reference probabilities with a **correlation adjustment** for legs of
   the same game. Legs of one game are not independent, so the plain
   product of leg probabilities is wrong.
2. **The residual after fees:** fair joint probability − print price − the
   combo fee, with fees from the cited fee table. That includes the combo
   maker fee effective 2026-08-20 (`fee_model_spec.md`), and the same
   fail-closed rules apply.
3. **A grade at settlement:** the combo's actual result against the
   recorded residual, under the same append-only and first-fire rules as
   the shadow ledger.

## Unpark gate (all required)

- **A few hundred graded single-leg first-fire rows** from Phase 1's shadow
  ledger, counted as independent events (rule 9).
- **Positive closing-line beat** on those rows: the grader's `clv` > 0 on
  balance.
- **After-fee P&L with a confidence interval that excludes zero.**

Until then, a combo logger would add a harder estimation problem
(correlation) on top of a single-leg signal that has not been shown to
exist.

## Open before any unparking

- **The correlation model:** how it is fitted, on which data, and how it is
  validated out of sample. It is not specified here.
- **Where combo prints come from:** read-only endpoints only. Phase 1's
  `/events` excludes combos.
- **A fresh spec,** committed before any code (AGENTS.md).
