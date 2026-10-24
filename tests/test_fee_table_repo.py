"""The repository fee table (docs/specs/fee_model_spec.md, amendment 2026-10-23)."""
import hashlib
from decimal import Decimal as D
from pathlib import Path

import pytest

from sports_edge import fees

REPO = Path(__file__).resolve().parent.parent
TABLE = REPO / "config" / "fee_schedule.json"
BUNDLE = REPO / "docs" / "sources" / "kalshi-fees-2026-10-23.pdf"
ORIGINALS = REPO / "docs" / "sources" / "kalshi-fees-2026-10-23"
GAME_SERIES = ["KXNFLGAME", "KXNCAAFGAME", "KXNBAGAME", "KXNHLGAME", "KXEPLGAME", "KXLALIGAGAME"]


@pytest.fixture(scope="module")
def table():
    return fees.load_table(TABLE)


def test_table_validates_against_the_committed_bundle(table):
    assert table["source"]["file"] == "docs/sources/kalshi-fees-2026-10-23.pdf"
    assert table["source"]["sha256"] == hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    assert fees.table_precision(table) == D("0.0001")


def test_every_original_is_named_with_its_hash_in_the_bundle():
    pdf = BUNDLE.read_bytes()
    originals = sorted(ORIGINALS.iterdir())
    assert len(originals) == 9
    for f in originals:
        assert f"{f.name}, sha256 {hashlib.sha256(f.read_bytes()).hexdigest()}".encode() in pdf, f.name


@pytest.mark.parametrize("series", GAME_SERIES)
def test_game_series_resolve_to_taker_1_maker_1(table, series):
    assert fees.resolve_multipliers(table, series, "quadratic_with_maker_fees", D("1"), False) == (D("1"), D("1"))
    assert table["series"][series]["cite"].startswith("p.")


def test_mlb_game_is_half_before_the_start(table):
    assert fees.resolve_multipliers(table, "KXMLBGAME", "quadratic_with_maker_fees", D("0.5"), False) == \
        (D("0.5"), D("0.5"))
    assert "PRE-GAME ONLY" in table["series"]["KXMLBGAME"]["cite"]
    # the page's taker range for 100 contracts: $0.04 (p = 0.01) to $0.88 (p = 0.50, shown rounded)
    assert fees.model_fee("0.50", 100, D("0.5")) == D("0.875")
    assert round(fees.model_fee("0.01", 100, D("0.5")), 2) == D("0.03")


def test_standard_and_maker_ranges_match_the_page():
    # Most markets: taker $0.07 - $1.75 per 100; maker series: maker $0.02 - $0.44 per 100
    assert round(fees.model_fee("0.01", 100, 1), 2) == D("0.07")
    assert fees.model_fee("0.50", 100, 1) == D("1.75")
    assert round(fees.model_fee("0.50", 100, 1, "maker"), 2) == D("0.44")
    assert round(fees.model_fee("0.01", 100, 1, "maker"), 2) == D("0.02")


@pytest.mark.parametrize("series", ["KXMLBF5", "KXMLBTOTAL", "KXMLBSPREAD"])
def test_unchecked_baseball_series_fail_closed(table, series):
    with pytest.raises(fees.FeeScheduleUnknown, match="unconfirmed"):
        fees.resolve_multipliers(table, series, "quadratic", D("0.5"), False)


def test_a_maker_fee_series_without_a_row_fails_closed(table):
    with pytest.raises(fees.FeeScheduleUnknown, match="disagrees"):
        fees.resolve_multipliers(table, "KXATPMATCH", "quadratic_with_maker_fees", D("1"), False)


def test_an_api_multiplier_that_disagrees_fails_closed(table):
    with pytest.raises(fees.FeeScheduleUnknown, match="multiplier"):
        fees.resolve_multipliers(table, "KXNBAGAME", "quadratic_with_maker_fees", D("0.5"), False)


def test_bundle_rebuilds_byte_identical(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bundle", REPO / "scripts" / "bundle_fee_sources.py")
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    originals = sorted(ORIGINALS.iterdir(), key=lambda p: int(p.name.split("-")[0]))
    out = tmp_path / "rebuilt.pdf"
    bundle.build(out, originals[0], originals[1:])
    assert out.read_bytes() == BUNDLE.read_bytes()
