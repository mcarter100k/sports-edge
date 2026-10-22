from decimal import Decimal as D

import pytest

from sports_edge.fees import FeeScheduleUnknown, fee_for_order, fee_per_contract, model_fee


def test_docs_worked_example_pins_the_coefficient():
    # Kalshi "Fee Rounding" docs: a buy at $0.055 has a model fee of $0.00363825,
    # a trade fee of $0.003639, and a $0.06 debit for a non-direct member.
    assert model_fee("0.055", 1, "quadratic", 1) == D("0.00363825")
    assert fee_for_order("0.055", 1, "quadratic", 1) == D("0.005")


def test_direct_member_precision():
    # cost 0.055 + trade fee 0.003639 = 0.058639 -> ceil to 0.0001 = 0.0587
    assert fee_for_order("0.055", 1, "quadratic", 1, precision="0.0001") == D("0.0037")


def test_hundred_contracts_at_fifty_cents():
    assert fee_for_order("0.50", 100, "quadratic", 1) == D("1.75")
    assert fee_per_contract("0.50", 100, "quadratic", 1) == D("0.0175")


def test_multiplier_scales_then_rounds_up():
    # 0.07 * 0.5 * 100 * 0.25 = 0.875 -> 0.88
    assert fee_for_order("0.50", 100, "quadratic", "0.5") == D("0.88")


def test_symmetric_in_price():
    assert fee_for_order("0.30", 100, "quadratic", 1) == fee_for_order("0.70", 100, "quadratic", 1)


def test_maker_fees_by_type():
    assert fee_for_order("0.50", 100, "quadratic", 1, role="maker") == D("0")
    assert fee_for_order("0.50", 100, "quadratic_with_maker_fees", 1, role="maker") == D("0.44")  # 0.4375
    assert fee_for_order("0.50", 100, "quadratic_with_combo_maker_fees", 1, role="maker") == D("0.88")


@pytest.mark.parametrize("fee_type", ["flat", "something_new", ""])
def test_unknown_fee_type_is_never_guessed(fee_type):
    with pytest.raises(FeeScheduleUnknown):
        fee_for_order("0.50", 100, fee_type, 1)


@pytest.mark.parametrize("price,contracts", [("0", 1), ("1", 1), ("1.2", 1), ("0.5", 0), ("0.5", -3)])
def test_invalid_inputs(price, contracts):
    with pytest.raises(ValueError):
        fee_for_order(price, contracts, "quadratic", 1)


def test_one_contract_rounds_to_a_full_cent():
    # 0.07 * 0.25 = 0.0175 -> the debit 0.5175 rounds to 0.52
    assert fee_for_order("0.50", 1, "quadratic", 1) == D("0.02")
