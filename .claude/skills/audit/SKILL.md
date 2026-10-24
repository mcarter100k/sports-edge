---
name: audit
description: Independent audit of a research study against its pre-registered criteria. Use when the owner types /audit with a registry id.
argument-hint: [registry id]
disable-model-invocation: true
context: fork
agent: auditor
background: false
---
Audit registry entry $ARGUMENTS.

1. Read the registry entry, its git history, the frozen dataset version, and the code in research/studies/$ARGUMENTS/. Do NOT read the Researcher's written report or summary.
2. Re-run the harness yourself and save the output to research/audits/$ARGUMENTS/runs/. Compare it with the Researcher's saved runs. Any mismatch means REJECTED until it is explained.
3. Check each of these and mark it PASS or FAIL with the file path behind it:
   - Leakage: does anything use information from after snapshot time? Walk-forward with a 1-day embargo.
   - Pre-registration: were the threshold, metric, or dataset changed after results came in? Use the registry git log.
   - Multiple testing: count all variants and apply the correction from research/stats.py.
   - Sample: independent games, not rows, against the registered minimum.
   - Costs: is the result after the taker fee from fees.py, with depth respected?
   - Holdout: was it untouched (optimizer's curse)?
   - Robustness: split by season, sport, and month. Does the edge survive the worst split?
   - Baseline: is the baseline actually beaten?
4. Give a verdict against the pre-registered criteria only: ACCEPTED, REJECTED, or NEEDS WORK. Write research/audits/$ARGUMENTS/audit.md with a 1-minute plain-English summary first, then each check. Label claims VERIFIED / OBSERVED / INFERRED / PENDING / NOT CHECKED.
5. Update only that entry's status line. ACCEPTED is not promotion: promotion to the live path needs the owner's approval in the registry.
