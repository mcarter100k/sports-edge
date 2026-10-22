"""Paths and settings, read from the environment at call time.

Nothing here caches a value, so tests can point every path at a temporary
directory with environment variables alone.
"""
import json
import os
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

# The frozen shadow rule (docs/specs/shadow_ledger_spec.md). Changing any of
# these is a new rule_id, never an edit in place.
RULE_ID = "GAP_v1"
RULE_DEFAULTS = {
    "SHADOW_EDGE_THRESHOLD": "0.03",
    "SHADOW_CONTRACTS": "100",
    "FEE_PRECISION": "0.01",
    "ODDS_MAX_AGE_MIN": "75",
}


def _path(var, default_name):
    value = os.environ.get(var)
    return Path(value) if value else REPO_ROOT / default_name


def data_dir() -> Path:
    return _path("SPORTS_EDGE_DATA_DIR", "data")


def logs_dir() -> Path:
    return _path("SPORTS_EDGE_LOG_DIR", "logs")


def state_dir() -> Path:
    return _path("SPORTS_EDGE_STATE_DIR", "state")


def db_path() -> Path:
    return data_dir() / "sports_edge.duckdb"


def shadow_ledger_path() -> Path:
    return data_dir() / "shadow_ledger.csv"


def calibration_grades_path() -> Path:
    return data_dir() / "calibration_grades.csv"


def shadow_grades_path() -> Path:
    return data_dir() / "shadow_grades.csv"


def kalshi_base_url() -> str:
    return os.environ.get("KALSHI_BASE_URL") or KALSHI_BASE_URL


def kalshi_max_rps() -> float:
    return float(os.environ.get("KALSHI_MAX_RPS", "5"))


def odds_api_key():
    return os.environ.get("ODDS_API_KEY") or None


def odds_daily_credits() -> int:
    return int(os.environ.get("ODDS_DAILY_CREDITS", "15"))


def odds_interval_min() -> int:
    return int(os.environ.get("ODDS_INTERVAL_MIN", "60"))


def odds_horizon_h() -> int:
    return int(os.environ.get("ODDS_HORIZON_H", "24"))


def _rule_value(name) -> str:
    return os.environ.get(name) or RULE_DEFAULTS[name]


def shadow_threshold() -> Decimal:
    return Decimal(_rule_value("SHADOW_EDGE_THRESHOLD"))


def shadow_contracts() -> int:
    return int(_rule_value("SHADOW_CONTRACTS"))


def fee_precision() -> Decimal:
    return Decimal(_rule_value("FEE_PRECISION"))


def odds_max_age_min() -> int:
    return int(_rule_value("ODDS_MAX_AGE_MIN"))


def rule_id() -> str:
    """GAP_v1, or GAP_v1+custom(...) when any rule parameter is overridden."""
    custom = {k: os.environ[k] for k in RULE_DEFAULTS
              if os.environ.get(k) and os.environ[k] != RULE_DEFAULTS[k]}
    if not custom:
        return RULE_ID
    parts = ",".join(f"{k}={v}" for k, v in sorted(custom.items()))
    return f"{RULE_ID}+custom({parts})"


def load_json_config(name):
    with open(REPO_ROOT / "config" / name) as fh:
        return json.load(fh)
