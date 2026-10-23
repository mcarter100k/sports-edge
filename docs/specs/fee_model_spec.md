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

---

## Amendment 2026-10-22: fee table from the official schedule (owner-verified)

The owner read the fee schedule (effective 2026-07-07) and confirmed:
taker = 0.07 × M_taker × C × P × (1−P); maker = 0.0175 × M_maker × C × P × (1−P);
round up per the schedule; **no settlement fee**. Default multipliers are
taker 1 and maker 0. Series listed under "Non-Standard Fees" override them.

**Source of truth: `config/fee_schedule.json`.** It is built only from the
PDF the owner saves in `docs/sources/`, and never by an automated fetch
(owner's terms). Every value carries a `cite` (page and section). The file
records the PDF's sha256, and a test fails if the PDF is missing or its hash
differs.

**Fail closed.** `fees.py` refuses to price a fee (it raises
`FeeScheduleUnknown`, and the shadow check counts the market as
`fee_unknown`) when:

1. `config/fee_schedule.json` does not exist yet;
2. the series is in the table's `unconfirmed` list;
3. the event carries a `fee_type_override` or `fee_multiplier_override`;
4. the API disagrees with the table. The API's `fee_multiplier` must equal
   the table's taker multiplier, and the API's `fee_type` must have maker
   fees exactly when the table's maker multiplier is above 0. A `flat` type
   always fails.

**Unconfirmed until the owner checks them** (live API, 2026-10-22): the 19
sports series with API multiplier 0.5, all Pro Baseball. One of them,
`KXMLBGAME`, conflicts with the schedule as quoted ("1/1") because the API
shows `quadratic_with_maker_fees` at 0.5. No `KXMVE*` (combo) series appears
under category Sports, and `/events` excludes combos, so combo rows are
never used here.

The earlier `fee_type`-based coefficients remain only as the cross-check in
point 4.

---

## Amendment 2026-10-22: centicent rounding (owner review finding 5)

The July 7 schedule rounds to a centicent ($0.0001): the owner quotes it as
"rounded to a centicent". This agrees with the Kalshi API docs' Fee
Rounding page, which aligns **direct** members (retail Kalshi accounts) to
$0.0001.

The precision is a fee-table value: `rounding.balance_precision = "0.0001"`,
with a required `cite` giving the PDF page and line. The table fails
validation without it. A test checks the value and requires the cite to
name a page. The shadow check takes the precision from the table and
records it on every row. The cite is filled in from the saved PDF when the
table is built. Until then there is no table, and every fee fails closed.

---

## Amendment 2026-10-22: the schedule changed after July 7 (owner note)

**The July 7 schedule is no longer the current one.** Per Kalshi's online
fee table, as read by the owner, a **combo maker fee takes effect
2026-08-20**. `config/fee_schedule.json` must therefore be built from the
schedule in force for the dates it prices, cited to that document. A table
whose `source.effective` is 2026-07-07 does not describe combo maker fees
after 2026-08-20.

The source of truth is unchanged: a PDF the owner saves in `docs/sources/`,
never an automated fetch. The Aug 20 date was **not** read by any automated
step; it is recorded here as the owner reported it.

**The API cross-check stays fail-closed (point 4 above), unchanged.**
- A market whose API `fee_type` is `quadratic_with_combo_maker_fees` is
  priced only if the table gives that series a maker multiplier above 0
  **and** the API multiplier equals the table's taker multiplier.
- Against the default entry (maker 0) it raises `FeeScheduleUnknown` and
  is counted as `fee_unknown`.
- A test pins this case (`test_combo_maker_fee_type_fails_closed_against_default_table`).

Combo (`KXMVE*`) markets are still outside Phase 1 (see the 2026-10-22
amendment above, and the PARKED `phase3_combo_logger_spec.md`).

---

## Correction 2026-10-22: the source of the August 20 date

The amendment above attributes the combo maker fee date (2026-08-20) to
"Kalshi's online fee table, as read by the owner". **That was wrong: the
source is news reporting.**
- **Status: UNCONFIRMED.** The owner will confirm it on
  kalshi.com/fee-schedule. Until then no fee table entry, cite or rule may
  rest on it.
- The fail-closed cross-check is unchanged and does not depend on the date.
- The amendment above is left as written; this correction supersedes its
  attribution.

---

## Amendment 2026-10-22: combo taker fee key (alerts only; never a ledger row)

- **The key.** The fee table may carry
  `"combo": {"taker": "<multiplier>", "cite": "<page and section>"}`. If it
  does, the cite is required, as for every other value.
- **`fees.combo_taker_multiplier(table)`** returns the multiplier. It raises
  `FeeScheduleUnknown` when:
  - there is no table;
  - there is no `combo` entry;
  - `combo` is listed under `unconfirmed`.
- **Use.** The only user is the combo line in the alerts, which then reads
  "fee unconfirmed — no price".
- **Status.** The combo fee is still UNCONFIRMED (see the correction above),
  so no table entry is made.

---

## Amendment 2026-10-22: combo taker fee key parked (owner cut B)

`fees.combo_taker_multiplier` and the optional `combo` table key, both added
above, are removed with the combo alert line, which is parked (see
`slate_alerts_spec.md`). The fail-closed cross-check on
`quadratic_with_combo_maker_fees` is unchanged.

---

## Amendment 2026-10-23: the fee table's source and values (owner-authorized; PROPOSED, owner review)

**Owner authorization (2026-10-23).** The owner authorized building the
table, including an automated fetch of Kalshi's fee pages. That lifts the
"never an automated fetch" term above, for this table only.

**What was reachable:**
- `kalshi.com/fee-schedule` and `kalshi.com/docs/kalshi-fee-schedule.pdf`
  still answer automated requests with HTTP 429 and a bot-check page. They
  were not retried with a disguised request.
- Kalshi's developer documentation, `docs.kalshi.com`, answered normally.

**The sources:**
1. **Eight screenshots of `kalshi.com/fee-schedule`, taken by the owner on
   2026-10-23:**
   - the Standard fees table with the top of the Non-standard table;
   - one search result each for KXLALIGAGAME, KXEPLGAME, KXNHLGAME,
     KXNBAGAME, KXNCAAFGAME, KXNFLGAME and KXMLBGAME.
2. **The Fee Rounding page,** `https://docs.kalshi.com/getting_started/fee_rounding.md`,
   fetched 2026-10-23.

**The source file.** The validator wants one document with page numbers, so
the sources are bundled into `docs/sources/kalshi-fees-2026-10-23.pdf`:
- page 1 is the rounding page's text;
- pages 2 to 9 are the screenshots, in the order above;
- each page carries its original file's name and sha256.

The originals are committed beside it, in
`docs/sources/kalshi-fees-2026-10-23/`. The table records the bundle's
sha256, and `source.effective` is `2026-10-23`: the page as displayed that
day, which shows "No upcoming fee changes scheduled".

**The values, and what each cites:**

| Key | Value | Cite |
|---|---|---|
| default | taker 1, maker 0 | p.2, Standard fees, "Most markets: multiplier 1, $0.07–$1.75 per 100 contracts" (= 0.07 × 100 × p(1−p) at p = 0.01 and 0.50) |
| KXNFLGAME, KXNCAAFGAME, KXNBAGAME, KXNHLGAME, KXEPLGAME, KXLALIGAGAME | taker 1, maker 1 | the series' page: Non-standard fees, "maker fees, multiplier 1, taker $0.07–$1.75, maker $0.02–$0.44" (maker = 0.0175 × 100 × 0.25) |
| KXMLBGAME | taker 0.5, maker 0.5 | p.9, Non-standard fees, "maker fees, 0.5 prelive, 1 live; taker $0.04–$0.88, maker $0.01–$0.22" |
| rounding.balance_precision | 0.0001 | p.1, Fee Rounding, Overview: "Direct member balances are aligned to $0.0001 (0.01c)" |
| settlement_fee | 0 | No settlement fee appears in the schedule pages (p.2–9, which list only taker and maker fees) or in the rounding mechanics (p.1). The owner confirmed no settlement fee from the 2026-07-07 schedule (amendment of 2026-10-22). |

**KXMLBGAME before and after the start.**
- The page gives 0.5 before the game and 1 once it is live. The table has
  one multiplier per series and records the pre-game 0.5.
- That is correct only because every price the system checks is before the
  start: `shadow.evaluate` refuses at or after `commence_utc`
  (`not_pre_game`), and the slip rater refuses started games.
- **Any future use of a live price must not use this table's KXMLBGAME
  value.** The `cite` says so.

**Fail closed for everything not checked:**
- **The 18 other Pro Baseball series** with API multiplier 0.5 (KXMLBF5,
  KXMLBTOTAL and the others) are listed under `unconfirmed`. They were not
  checked on the fee page, and they are not game moneylines.
- **Every other non-standard series** (the 106 with maker fees in the API,
  outside the seven above) has no row. Against the default (maker 0) the
  existing cross-check rejects them as `fee_unknown`. No row is guessed.

**Tests:**
- The repository table loads and validates against the committed bundle,
  with its hash checked.
- Each of the seven series resolves to its cited multipliers against its
  API fee type.
- An unconfirmed series raises.
- A series with API maker fees but no row raises.
- MLB's taker fee at p = 0.5 for 100 contracts is half the standard fee.

---

## Amendment 2026-10-23b: the combo taker fee key, unparked (PROPOSED, owner review)

**The key.** The fee table carries
`"combo": {"taker": "1", "cite": "<page and section>"}`, cited to p.2 of
`docs/sources/kalshi-fees-2026-10-23.pdf`:
- "Combos (excluding uncorrelated NFL): maker fees - 50% of taker, fee
  multiplier 1, $0.07 - $1.75 taker fees";
- uncorrelated NFL combos fall under "Most markets: fee multiplier 1".

So the taker multiplier is 1 for every combo.

**The rest is restored as before the park:**
- `fees.combo_taker_multiplier(table)` returns the multiplier. It raises
  `FeeScheduleUnknown` when there is no table, no `combo` entry, or `combo`
  is under `unconfirmed`.
- `validate_table` requires a cite on the entry.
- Its only user is the slate's combo line (`slate_alerts_spec.md`,
  amendment 2026-10-23). Combos are never ledger rows.

**Not added:** a combo maker multiplier. The system never rests orders.
Kalshi's "50% of taker" maker rate is recorded here for completeness only.

