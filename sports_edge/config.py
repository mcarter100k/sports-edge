"""Paths and settings, read from the environment at call time.

Nothing here caches a value, so tests can point every path at a temporary
directory with environment variables alone.
"""
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


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


def fee_table_path() -> Path:
    value = os.environ.get("SPORTS_EDGE_FEE_TABLE")
    return Path(value) if value else REPO_ROOT / "config" / "fee_schedule.json"


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


def kalshi_ticker_batch() -> int:
    return int(os.environ.get("KALSHI_TICKER_BATCH", "50"))


def odds_horizon_h() -> int:
    return int(os.environ.get("ODDS_HORIZON_H", "24"))


def load_json_config(name):
    with open(REPO_ROOT / "config" / name) as fh:
        return json.load(fh)
