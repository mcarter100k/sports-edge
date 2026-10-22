# Spec: fee model (`sports_edge/fees.py`)

Status: Phase 1. Protected file.

## Sources (read 2026-10-21) and what each one pins

| Fact | Source | Status |
|---|---|---|
| Each series carries `fee_type` (`quadratic`, `quadratic_with_maker_fees`, `quadratic_with_combo_maker_fees`, `flat`) and `fee_multiplier` | OpenAPI `Series` schema | VERIFIED in the spec and in live data |
| An event's `fee_type_override` / `fee_multiplier_override` takes precedence over the series | OpenAPI `EventData` schema | from the spec |
| Taker model fee for `quadratic` = **0.07 × multiplier × C × P × (1 − P)** | Docs "Fee Rounding" worked example: a buy at $0.055 has a model fee of $0.00363825 = 0.07 × 0.055 × 0.945 | coefficient pinned by an official worked example |
| Maker fee = 0.25 × the taker coefficient (0.0175); 0.5 × (0.035) for `quadratic_with_combo_maker_fees` | Series schema ("0.5 maker multiplier instead of 0.25"); changelog 2026-08-20 ("uses a fee multiplier of 0.5, rather than the standard 0.25") | from the docs |
| Rounding: trade fee = model fee rounded **up** to $0.000001; the balance change is then floored to the member's precision ($0.01 non-direct, $0.0001 direct) | Docs "Fee Rounding" | from the docs |
| `flat` fee amounts | the Specific Trading Fees Table in the fee schedule PDF | **NOT READ** — see open items |

Live on 2026-10-21, all 4,029 sports series were `quadratic` or
`quadratic_with_maker_fees`, with multiplier 1 or 0.5. None was `flat`.

## Behaviour

`fee_for_order(price, contracts, fee_type, multiplier, role="taker",
precision=Decimal("0.01")) -> Decimal` returns the total fee charged for
one order filled at one price.

1. `model = coeff(role, fee_type) × multiplier × C × P × (1 − P)`
2. `trade_fee = ceil(model, 0.000001)`
3. For a buyer, the cost is `C × P`. `charged_total = ceil(C × P + trade_fee, precision)`,
   so `fee = charged_total − C × P`. This is the same as flooring the signed
   balance change, as in the docs' example: $0.055 + $0.003639 becomes a
   $0.06 debit, a fee of $0.005.
4. `flat`, or any unknown fee type, raises `FeeScheduleUnknown`. Callers
   skip that market and log it. They never assume a fee.
5. A price outside (0, 1), or contracts ≤ 0, raises `ValueError`.
6. `quadratic` with role `maker` gives a fee of 0 (maker fees apply only
   to the `*_maker_fees` types).

`fee_per_contract(...) = fee_for_order(...) / C`.

**Shadow convention:** the shadow ledger assumes a **taker** buy of
`SHADOW_CONTRACTS` contracts (default 100) at the best ask, for a
non-direct member ($0.01 precision). Settlement is assumed fee-free.

**Fee waivers:** a market's `fee_waiver_expiration_time` is recorded in
the ledger but **not** applied. Charging the fee during a waiver can only
understate a gap, never invent one.

## Open items (owner to confirm from the PDF in a browser)

`kalshi.com/docs/kalshi-fee-schedule.pdf` and `kalshi.com/fee-schedule`
both returned HTTP 429 to every automated fetch on 2026-10-21. Confirm, and
record in `docs/validation_log.md`:

1. that the general taker coefficient is 0.07, and maker 0.0175;
2. that there is no fee at settlement;
3. how the Specific Trading Fees Table defines `flat`, if it is ever needed.
