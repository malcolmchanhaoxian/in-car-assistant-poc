"""Unit tests for the deterministic safety rules (no API, no LLM involved)."""

import pytest

from app.safety import (
    BATTERY_TEMP_THRESHOLD_C,
    CHARGER_DISTANCE_THRESHOLD_KM,
    LOW_BATTERY_THRESHOLD_PERCENT,
    RESERVE_BATTERY_THRESHOLD_PERCENT,
    Severity,
    evaluate_safety,
    highest_severity,
    safety_text,
)
from app.vehicle import Telemetry


def make_telemetry(**overrides) -> Telemetry:
    data = {
        "vehicle_id": "test-001",
        "speed_kmh": 90,
        "battery_percent": 80,
        "battery_temp_c": 30,
        "outside_temp_c": 20,
        "nearest_charger_km": 10,
    }
    data.update(overrides)
    return Telemetry(**data)


@pytest.mark.parametrize(
    "battery_percent, expected",
    [
        (0, True),
        (19, True),
        (19.9, True),
        (20, False),  # threshold is exclusive: "below 20%" (the default 10 km charger is reachable)
        (20.1, False),
        (80, False),
    ],
)
def test_low_battery_rule(battery_percent, expected):
    alerts = evaluate_safety(make_telemetry(battery_percent=battery_percent))

    assert (len(alerts) == 1) is expected
    if expected:
        alert = alerts[0]
        assert alert.rule_id == "SAFETY-001"
        assert alert.code == "LOW_BATTERY"
        assert alert.severity is Severity.warning
        assert "charge" in alert.message.lower()


@pytest.mark.parametrize(
    "battery_percent, nearest_charger_km, expected",
    [
        (29.9, 40.1, True),  # below the reserve limit and the charger is out of reach
        (25, 48, True),
        (20, 50, True),  # low-charge boundary is exclusive, the reserve rule still applies
        (15, 5, True),  # low charge alone is enough, however close the charger is
        (10, 60, True),  # both conditions true -> still a single alert
        (29.9, 40, False),  # a charger at exactly 40 km is not "> 40"
        (30, 50, False),  # charge at exactly 30% is not "< 30"
        (29.9, 39.9, False),
        (35, 60, False),  # distance alone never triggers charging
    ],
)
def test_charging_recommended_for_low_charge_or_unreachable_charger(
    battery_percent, nearest_charger_km, expected
):
    alerts = evaluate_safety(
        make_telemetry(battery_percent=battery_percent, nearest_charger_km=nearest_charger_km)
    )

    assert (len(alerts) == 1) is expected
    if expected:
        assert [alert.code for alert in alerts] == ["LOW_BATTERY"]
        assert alerts[0].severity is Severity.warning
        assert "charge the vehicle" in alerts[0].message.lower()


def test_charging_message_reflects_the_triggering_condition():
    low_charge = evaluate_safety(make_telemetry(battery_percent=15))[0].message

    assert "below the 20% limit" in low_charge

    reserve = evaluate_safety(make_telemetry(battery_percent=29.9, nearest_charger_km=40.1))[0].message

    assert "29.9%," in reserve
    assert "30% reserve threshold" in reserve
    assert "40.1 km" in reserve
    assert "beyond the 40 km range" in reserve
    assert "charge is 30%," not in reserve.lower()


@pytest.mark.parametrize(
    "battery_temp_c, expected",
    [
        (30, False),
        (50, False),  # threshold is exclusive: "exceeds 50C"
        (50.1, True),
        (51, True),
        (70, True),
    ],
)
def test_battery_overheat_rule(battery_temp_c, expected):
    alerts = evaluate_safety(make_telemetry(battery_temp_c=battery_temp_c))

    assert (len(alerts) == 1) is expected
    if expected:
        alert = alerts[0]
        assert alert.rule_id == "SAFETY-002"
        assert alert.code == "BATTERY_OVERHEAT"
        assert alert.severity is Severity.critical
        assert "high priority" in alert.message.lower()


def test_temperature_exactly_at_the_limit_is_not_flagged():
    """Regression: the spec warns only when the temperature EXCEEDS 50°C.

    Exactly 50°C is within spec, so no alert may be raised for it.
    """
    assert evaluate_safety(make_telemetry(battery_temp_c=50)) == []
    assert evaluate_safety(make_telemetry(battery_temp_c=50.0001)) != []


@pytest.mark.parametrize("battery_temp_c", [50.1, 50.4, 50.0001, 54])
def test_overheat_message_never_rounds_onto_the_limit(battery_temp_c):
    """Regression: a car at 50.1°C used to be reported as "50°C, above the 50°C limit".

    The message formatted the reading with ``:.0f``, so a value just over the limit
    was displayed as the limit itself and looked like a wrong classification.
    """
    message = evaluate_safety(make_telemetry(battery_temp_c=battery_temp_c))[0].message

    assert f"{battery_temp_c:g}°C" in message
    assert "temperature is 50°C," not in message


@pytest.mark.parametrize("battery_percent", [0, 19.6, 19.999])
def test_low_battery_message_never_rounds_onto_the_limit(battery_percent):
    """Same rounding defect for the low-battery rule: 19.6% must not read as "20%"."""
    message = evaluate_safety(make_telemetry(battery_percent=battery_percent))[0].message

    assert f"{battery_percent:g}%" in message
    assert "charge is 20%," not in message.lower()


def test_no_alerts_when_all_values_healthy():
    assert evaluate_safety(make_telemetry()) == []
    assert highest_severity([]) is None
    assert safety_text([]) == ""


def test_both_rules_trigger_and_critical_is_ranked_first():
    alerts = evaluate_safety(make_telemetry(battery_percent=15, battery_temp_c=55))

    assert [alert.rule_id for alert in alerts] == ["SAFETY-002", "SAFETY-001"]
    assert highest_severity(alerts) is Severity.critical


def test_rule_result_is_deterministic():
    telemetry = make_telemetry(battery_percent=15, battery_temp_c=55)

    first = evaluate_safety(telemetry)
    second = evaluate_safety(telemetry)

    assert first == second


def test_thresholds_match_the_documented_values():
    assert LOW_BATTERY_THRESHOLD_PERCENT == 20.0
    assert RESERVE_BATTERY_THRESHOLD_PERCENT == 30.0
    assert CHARGER_DISTANCE_THRESHOLD_KM == 40.0
    assert BATTERY_TEMP_THRESHOLD_C == 50.0
