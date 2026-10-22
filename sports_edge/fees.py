"""Kalshi trading fees (docs/specs/fee_model_spec.md).

Taker model fee for the quadratic types is 0.07 x multiplier x C x P x (1 - P).
The 0.07 coefficient is pinned by the worked example in Kalshi's
"Fee Rounding" docs: a buy at $0.055 has a model fee of $0.00363825.
"""
from decimal import ROUND_CEILING, Decimal

TAKER_COEFF = Decimal("0.07")
MAKER_SHARE = {
    "quadratic": Decimal("0"),
    "quadratic_with_maker_fees": Decimal("0.25"),
    "quadratic_with_combo_maker_fees": Decimal("0.5"),
}
MICRO = Decimal("0.000001")


class FeeScheduleUnknown(Exception):
    """The fee type has no implemented formula. Never guess a fee."""


def _ceil(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def coefficient(fee_type: str, role: str) -> Decimal:
    if fee_type not in MAKER_SHARE:
        raise FeeScheduleUnknown(f"fee type {fee_type!r} is not implemented")
    if role == "taker":
        return TAKER_COEFF
    if role == "maker":
        return TAKER_COEFF * MAKER_SHARE[fee_type]
    raise ValueError(f"role must be taker or maker, got {role!r}")


def model_fee(price, contracts, fee_type, multiplier, role="taker") -> Decimal:
    price = Decimal(str(price))
    contracts = Decimal(str(contracts))
    if not (Decimal(0) < price < Decimal(1)):
        raise ValueError(f"price must be strictly between 0 and 1, got {price}")
    if contracts <= 0:
        raise ValueError(f"contracts must be positive, got {contracts}")
    mult = Decimal(str(multiplier))
    return coefficient(fee_type, role) * mult * contracts * price * (Decimal(1) - price)


def fee_for_order(price, contracts, fee_type, multiplier, role="taker",
                  precision=Decimal("0.01")) -> Decimal:
    """Total fee for one buy order filled at one price, after rounding.

    trade fee = model fee rounded up to $0.000001; the buyer's debit
    (cost + trade fee) is then rounded up to the balance precision.
    """
    price = Decimal(str(price))
    contracts = Decimal(str(contracts))
    precision = Decimal(str(precision))
    trade_fee = _ceil(model_fee(price, contracts, fee_type, multiplier, role), MICRO)
    cost = price * contracts
    return _ceil(cost + trade_fee, precision) - cost


def fee_per_contract(price, contracts, fee_type, multiplier, role="taker",
                     precision=Decimal("0.01")) -> Decimal:
    contracts = Decimal(str(contracts))
    return fee_for_order(price, contracts, fee_type, multiplier, role, precision) / contracts
