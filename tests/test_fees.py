import json
from decimal import Decimal as D

import pytest

from helpers import fee_table
from sports_edge import fees
from sports_edge.fees import (FeeScheduleUnknown, FeeTableInvalid, fee_for_order, fee_per_contract,
                              load_table, model_fee, resolve_multipliers)


# Formula and rounding ---------------------------------------------------------

def test_docs_worked_example_pins_the_taker_coefficient():
    # Kalshi "Fee Rounding" docs: a buy at $0.055 has a model fee of $0.00363825,
    # a trade fee of $0.003639, and a $0.06 debit for a non-direct member.
    assert model_fee("0.055", 1, 1) == D("0.00363825")
    assert fee_for_order("0.055", 1, 1) == D("0.005")


def test_direct_member_precision():
    assert fee_for_order("0.055", 1, 1, precision="0.0001") == D("0.0037")


def test_hundred_contracts_at_fifty_cents():
    assert fee_for_order("0.50", 100, 1) == D("1.75")
    assert fee_per_contract("0.50", 100, 1) == D("0.0175")


def test_multiplier_scales_then_rounds_up():
    assert fee_for_order("0.50", 100, "0.5") == D("0.88")      # 0.875 -> 0.88


def test_maker_coefficient_is_0_0175():
    assert model_fee("0.50", 100, 1, role="maker") == D("0.4375")
    assert fee_for_order("0.50", 100, 2, role="maker") == D("0.88")   # combo-style maker 2
    assert fee_for_order("0.50", 100, 0, role="maker") == D("0")


def test_symmetric_in_price():
    assert fee_for_order("0.30", 100, 1) == fee_for_order("0.70", 100, 1)


@pytest.mark.parametrize("price,contracts", [("0", 1), ("1", 1), ("1.2", 1), ("0.5", 0), ("0.5", -3)])
def test_invalid_inputs(price, contracts):
    with pytest.raises(ValueError):
        fee_for_order(price, contracts, 1)


def test_one_contract_rounds_to_a_full_cent():
    assert fee_for_order("0.50", 1, 1) == D("0.02")


# The table: fail closed -----------------------------------------------------------

def test_no_table_fails_closed():
    assert load_table() is None
    with pytest.raises(FeeScheduleUnknown, match="no fee table"):
        resolve_multipliers(None, "KXNBAGAME", "quadratic", 1, False)


def test_default_series(tmp_path):
    t = load_table(fee_table(tmp_path))
    assert resolve_multipliers(t, "KXNBAGAME", "quadratic", 1, False) == (D("1"), D("0"))


def test_listed_series_with_maker_fees(tmp_path):
    t = load_table(fee_table(tmp_path, series={"KXNFLGAME": ("1", "1")}))
    assert resolve_multipliers(t, "KXNFLGAME", "quadratic_with_maker_fees", 1, False) == (D("1"), D("1"))


def test_api_says_maker_fees_but_table_says_none(tmp_path):
    t = load_table(fee_table(tmp_path))
    with pytest.raises(FeeScheduleUnknown, match="disagrees"):
        resolve_multipliers(t, "KXNEW", "quadratic_with_maker_fees", 1, False)


def test_api_multiplier_disagrees_with_table(tmp_path):
    # The live case: API shows KXMLBGAME at 0.5 while the schedule was quoted as 1/1.
    t = load_table(fee_table(tmp_path, series={"KXMLBGAME": ("1", "1")}))
    with pytest.raises(FeeScheduleUnknown, match="API multiplier 0.5"):
        resolve_multipliers(t, "KXMLBGAME", "quadratic_with_maker_fees", 0.5, False)


def test_unconfirmed_series_fails_closed(tmp_path):
    t = load_table(fee_table(tmp_path, unconfirmed={"KXMLBHR": "API 0.5, awaiting owner check"}))
    with pytest.raises(FeeScheduleUnknown, match="unconfirmed"):
        resolve_multipliers(t, "KXMLBHR", "quadratic", 0.5, False)


def test_event_override_fails_closed(tmp_path):
    t = load_table(fee_table(tmp_path))
    with pytest.raises(FeeScheduleUnknown, match="override"):
        resolve_multipliers(t, "KXNBAGAME", "quadratic", 1, True)


@pytest.mark.parametrize("fee_type", ["flat", "something_new", None])
def test_unknown_api_fee_type_fails_closed(tmp_path, fee_type):
    t = load_table(fee_table(tmp_path))
    with pytest.raises(FeeScheduleUnknown):
        resolve_multipliers(t, "KXNBAGAME", fee_type, 1, False)


# The table: validation ----------------------------------------------------------

def test_missing_cite_is_invalid(tmp_path):
    p = fee_table(tmp_path, series={"KXNFLGAME": ("1", "1")})
    t = json.loads(p.read_text()); t["series"]["KXNFLGAME"]["cite"] = " "; p.write_text(json.dumps(t))
    with pytest.raises(FeeTableInvalid, match="cite"):
        load_table(p)


def test_source_hash_mismatch_is_invalid(tmp_path):
    p = fee_table(tmp_path)
    (tmp_path / "fee_source.pdf").write_bytes(b"changed")
    with pytest.raises(FeeTableInvalid, match="sha256"):
        load_table(p)


def test_missing_source_file_is_invalid(tmp_path):
    p = fee_table(tmp_path)
    (tmp_path / "fee_source.pdf").unlink()
    with pytest.raises(FeeTableInvalid, match="missing"):
        load_table(p)


def test_nonzero_settlement_fee_is_rejected(tmp_path):
    p = fee_table(tmp_path)
    t = json.loads(p.read_text()); t["settlement_fee"]["value"] = "0.01"; p.write_text(json.dumps(t))
    with pytest.raises(FeeTableInvalid, match="settlement"):
        load_table(p)


def test_repo_table_if_present_is_valid_and_cited():
    path = fees.config.REPO_ROOT / "config" / "fee_schedule.json"
    if not path.exists():
        pytest.skip("config/fee_schedule.json not built yet (waiting on the PDF in docs/sources/)")
    table = load_table(path)
    assert table["source"]["file"].startswith("docs/sources/")
    assert table["rounding"]["balance_precision"] == "0.0001"


# Centicent precision from the cited table ------------------------------------------

def test_precision_comes_from_the_table(tmp_path):
    from sports_edge.fees import table_precision
    assert table_precision(load_table(fee_table(tmp_path))) == D("0.0001")
    with pytest.raises(FeeScheduleUnknown):
        table_precision(None)


def test_centicent_changes_the_fee():
    # 100 @ 0.42: model 1.7052 -> centicent keeps 1.7052, cent rounds to 1.71
    assert fee_for_order("0.42", 100, 1, precision="0.0001") == D("1.7052")
    assert fee_for_order("0.42", 100, 1, precision="0.01") == D("1.71")


@pytest.mark.parametrize("change,match", [
    ({"cite": ""}, "cite"),
    ({"cite": "Rounding section"}, "page"),
    ({"balance_precision": "0.001"}, "0.01 or 0.0001"),
])
def test_rounding_entry_must_be_cited_and_sane(tmp_path, change, match):
    p = fee_table(tmp_path)
    t = json.loads(p.read_text()); t["rounding"].update(change); p.write_text(json.dumps(t))
    with pytest.raises(FeeTableInvalid, match=match):
        load_table(p)


def test_missing_rounding_entry_is_invalid(tmp_path):
    p = fee_table(tmp_path)
    t = json.loads(p.read_text()); del t["rounding"]; p.write_text(json.dumps(t))
    with pytest.raises(FeeTableInvalid):
        load_table(p)


def test_combo_maker_fee_type_fails_closed_against_default_table(tmp_path):
    # fee_model_spec.md amendment 2026-10-22: a combo maker fee is in force from
    # 2026-08-20; a series the table does not give a maker fee must not be priced.
    t = load_table(fee_table(tmp_path))
    with pytest.raises(FeeScheduleUnknown, match="disagrees with table maker multiplier"):
        resolve_multipliers(t, "KXMVESPORTSMULTIGAMEEXTENDED", "quadratic_with_combo_maker_fees", 1, False)
    t2 = load_table(fee_table(tmp_path, series={"KXMVECOMBO": ("1", "1")}))
    assert resolve_multipliers(t2, "KXMVECOMBO", "quadratic_with_combo_maker_fees", 1, False) == (D("1"), D("1"))
    with pytest.raises(FeeScheduleUnknown, match="API multiplier"):
        resolve_multipliers(t2, "KXMVECOMBO", "quadratic_with_combo_maker_fees", 2, False)


def test_combo_fee_fails_closed_until_the_table_confirms_it(tmp_path):
    with pytest.raises(FeeScheduleUnknown, match="no fee table"):
        fees.combo_taker_multiplier(None)
    fee_table(tmp_path)
    with pytest.raises(FeeScheduleUnknown, match="no confirmed entry"):
        fees.combo_taker_multiplier(fees.load_table())
    path = fee_table(tmp_path, unconfirmed={"combo": "news reporting only"})
    t = json.loads(path.read_text())
    t["combo"] = {"taker": "1", "cite": "p.4 Combos"}
    path.write_text(json.dumps(t))
    with pytest.raises(FeeScheduleUnknown, match="unconfirmed"):
        fees.combo_taker_multiplier(fees.load_table())
    t["unconfirmed"] = {}
    path.write_text(json.dumps(t))
    assert fees.combo_taker_multiplier(fees.load_table()) == D("1")


def test_combo_entry_needs_a_cite(tmp_path):
    path = fee_table(tmp_path)
    t = json.loads(path.read_text())
    t["combo"] = {"taker": "1"}
    path.write_text(json.dumps(t))
    with pytest.raises(FeeTableInvalid, match="combo"):
        fees.load_table()
