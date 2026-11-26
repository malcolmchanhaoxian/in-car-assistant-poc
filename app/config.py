"""Configuration values, settable by environment variables."""

import os

DEBUG_MODE = False


def get_env_float(key: str, default: float) -> float:
    """Get a float from the environment, with a default."""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


LOW_BATTERY_THRESHOLD_PERCENT = get_env_float("LOW_BATTERY_THRESHOLD_PERCENT", 20.0)
RESERVE_BATTERY_THRESHOLD_PERCENT = get_env_float("RESERVE_BATTERY_THRESHOLD_PERCENT", 30.0)
CHARGER_DISTANCE_THRESHOLD_KM = get_env_float("CHARGER_DISTANCE_THRESHOLD_KM", 40.0)
BATTERY_TEMP_THRESHOLD_C = get_env_float("BATTERY_TEMP_THRESHOLD_C", 50.0)
