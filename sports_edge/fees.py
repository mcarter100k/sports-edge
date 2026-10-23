"""Kalshi trading fees (docs/specs/fee_model_spec.md).

taker = 0.07   x M_taker x C x P x (1 - P)
maker = 0.0175 x M_maker x C x P x (1 - P)
Fee schedule effective 2026-07-07, as verified by the owner. Default
multipliers are taker 1 and maker 0; per-series overrides come only from
config/fee_schedule.json, which is built from the PDF in docs/sources/.

Fail closed: any doubt about a series' multipliers raises FeeScheduleUnknown.
A fee is never guessed.
"""
import hashlib
import json
import re
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from . import config

COEFF = {"taker": Decimal("0.07"), "maker": Decimal("0.0175")}
MICRO = Decimal("0.000001")
MAKER_FEE_TYPES = {"quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"}
KNOWN_FEE_TYPES = {"quadratic"} | MAKER_FEE_TYPES


class FeeScheduleUnknown(Exception):
    """No trustworthy multiplier for this series. Never guess a fee."""


class FeeTableInvalid(Exception):
    pass


def _ceil(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def model_fee(price, contracts, multiplier, role="taker") -> Decimal:
    price = Decimal(str(price))
    contracts = Decimal(str(contracts))
    if not (Decimal(0) < price < Decimal(1)):
        raise ValueError(f"price must be strictly between 0 and 1, got {price}")
    if contracts <= 0:
        raise ValueError(f"contracts must be positive, got {contracts}")
    if role not in COEFF:
        raise ValueError(f"role must be taker or maker, got {role!r}")
    return COEFF[role] * Decimal(str(multiplier)) * contracts * price * (Decimal(1) - price)


def fee_for_order(price, contracts, multiplier, role="taker", precision=Decimal("0.01")) -> Decimal:
    """Total fee for one buy order filled at one price, after rounding.

    trade fee = model fee rounded up to $0.000001; the buyer's debit
    (cost + trade fee) is then rounded up to the balance precision.
    """
    price = Decimal(str(price))
    contracts = Decimal(str(contracts))
    precision = Decimal(str(precision))
    trade_fee = _ceil(model_fee(price, contracts, multiplier, role), MICRO)
    cost = price * contracts
    return _ceil(cost + trade_fee, precision) - cost


def fee_per_contract(price, contracts, multiplier, role="taker", precision=Decimal("0.01")) -> Decimal:
    return fee_for_order(price, contracts, multiplier, role, precision) / Decimal(str(contracts))


# The fee table ---------------------------------------------------------------

def _need_cite(entry, where):
    if not isinstance(entry, dict) or not str(entry.get("cite", "")).strip():
        raise FeeTableInvalid(f"{where}: every value needs a non-empty 'cite'")


def validate_table(table, repo_root=None):
    """Raise FeeTableInvalid unless every value is cited and the source PDF matches its hash."""
    repo_root = repo_root or config.REPO_ROOT
    src = table.get("source") or {}
    for key in ("file", "sha256", "effective"):
        if not src.get(key):
            raise FeeTableInvalid(f"source.{key} is required")
    pdf = Path(src["file"]) if Path(src["file"]).is_absolute() else repo_root / src["file"]
    if not pdf.exists():
        raise FeeTableInvalid(f"source file {src['file']} is missing")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if digest != src["sha256"]:
        raise FeeTableInvalid(f"source file {src['file']} sha256 {digest} != recorded {src['sha256']}")
    for name in ("default", "settlement_fee", "rounding"):
        _need_cite(table.get(name), name)
    if not re.search(r"\b(p\.|page)\s*\d+", table["rounding"]["cite"], re.IGNORECASE):
        raise FeeTableInvalid("rounding: the cite must name a page of the source")
    if Decimal(str(table["rounding"]["balance_precision"])) not in (Decimal("0.01"), Decimal("0.0001")):
        raise FeeTableInvalid("rounding.balance_precision must be 0.01 or 0.0001")
    for ticker, entry in (table.get("series") or {}).items():
        _need_cite(entry, f"series.{ticker}")
        for k in ("taker", "maker"):
            Decimal(str(entry[k]))
    for k in ("taker", "maker"):
        Decimal(str(table["default"][k]))
    if Decimal(str(table["settlement_fee"]["value"])) != 0:
        raise FeeTableInvalid("a nonzero settlement fee is not implemented")
    for ticker, reason in (table.get("unconfirmed") or {}).items():
        if not str(reason).strip():
            raise FeeTableInvalid(f"unconfirmed.{ticker} needs a reason")
    if "combo" in table:
        _need_cite(table["combo"], "combo")
        Decimal(str(table["combo"]["taker"]))
    return table


def load_table(path=None):
    """The validated table, or None if config/fee_schedule.json does not exist yet."""
    path = path or config.fee_table_path()
    if not path.exists():
        return None
    with open(path) as fh:
        return validate_table(json.load(fh))


def table_precision(table):
    """Balance precision from the cited fee table (the July 7 schedule: a centicent)."""
    if table is None:
        raise FeeScheduleUnknown("no fee table yet (config/fee_schedule.json)")
    return Decimal(str(table["rounding"]["balance_precision"]))


def combo_taker_multiplier(table):
    """Taker multiplier for combos (alerts only), or FeeScheduleUnknown. Never guessed."""
    if table is None:
        raise FeeScheduleUnknown("no fee table yet (config/fee_schedule.json)")
    if "combo" in (table.get("unconfirmed") or {}):
        raise FeeScheduleUnknown(f"combo: unconfirmed ({table['unconfirmed']['combo']})")
    if "combo" not in table:
        raise FeeScheduleUnknown("combo: no confirmed entry in the fee table")
    return Decimal(str(table["combo"]["taker"]))


def resolve_multipliers(table, series_ticker, api_fee_type, api_multiplier, has_override):
    """(M_taker, M_maker) for a series, or FeeScheduleUnknown with the reason."""
    if table is None:
        raise FeeScheduleUnknown("no fee table yet (config/fee_schedule.json)")
    if has_override:
        raise FeeScheduleUnknown(f"{series_ticker}: event-level fee override present")
    if series_ticker in (table.get("unconfirmed") or {}):
        raise FeeScheduleUnknown(f"{series_ticker}: unconfirmed ({table['unconfirmed'][series_ticker]})")
    if api_fee_type not in KNOWN_FEE_TYPES:
        raise FeeScheduleUnknown(f"{series_ticker}: API fee type {api_fee_type!r} not implemented")
    entry = (table.get("series") or {}).get(series_ticker, table["default"])
    m_taker, m_maker = Decimal(str(entry["taker"])), Decimal(str(entry["maker"]))
    if api_multiplier is None or Decimal(str(api_multiplier)) != m_taker:
        raise FeeScheduleUnknown(
            f"{series_ticker}: API multiplier {api_multiplier} != table taker multiplier {m_taker}")
    if (m_maker > 0) != (api_fee_type in MAKER_FEE_TYPES):
        raise FeeScheduleUnknown(
            f"{series_ticker}: API fee type {api_fee_type} disagrees with table maker multiplier {m_maker}")
    return m_taker, m_maker


def table_state():
    """'ok', 'missing' (no config/fee_schedule.json yet) or 'invalid'."""
    try:
        return "ok" if load_table() is not None else "missing"
    except FeeTableInvalid:
        return "invalid"
