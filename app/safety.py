"""Deterministic vehicle safety policy.

This module deliberately has no LLM, network or I/O dependency. It only reads
telemetry numbers and returns alerts, so its outcome is fully reproducible, is
never influenced by the AI layer, and keeps working when the LLM service is
unavailable.
"""

from enum import Enum

from pydantic import BaseModel

from .vehicle import Telemetry

# Thresholds are named constants so they are easy to audit and to test.
LOW_BATTERY_THRESHOLD_PERCENT = 20.0
RESERVE_BATTERY_THRESHOLD_PERCENT = 30.0
CHARGER_DISTANCE_THRESHOLD_KM = 40.0
BATTERY_TEMP_THRESHOLD_C = 50.0

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


class Severity(str, Enum):
    """Ordered severity of a safety alert (higher value = higher priority)."""

    info = "info"
    warning = "warning"
    critical = "critical"


class SafetyAlert(BaseModel):
    """Outcome of a single deterministic safety rule."""

    rule_id: str
    code: str
    severity: Severity
    message: str


def evaluate_safety(telemetry: Telemetry) -> list[SafetyAlert]:
    """Return every safety alert triggered by ``telemetry``.

    Charging is recommended when the charge is below the low-battery limit, or when
    it is below the reserve limit and the nearest charger is out of reach. Alerts are
    ordered highest severity first, then by ``rule_id`` for stability.
    """
    alerts: list[SafetyAlert] = []

    battery_percent = telemetry.battery_percent
    charger_km = telemetry.nearest_charger_km

    low_charge = battery_percent < LOW_BATTERY_THRESHOLD_PERCENT
    low_reserve_out_of_reach = (
        battery_percent < RESERVE_BATTERY_THRESHOLD_PERCENT
        and charger_km > CHARGER_DISTANCE_THRESHOLD_KM
    )

    if low_charge or low_reserve_out_of_reach:
        # ``:g`` keeps values from rounding onto the threshold itself
        # (19.6% must not be reported as "20%, below the 20% limit").
        if low_charge:
            message = (
                f"Battery charge is {battery_percent:g}%, below the "
                f"{LOW_BATTERY_THRESHOLD_PERCENT:g}% limit. "
                f"Charge the vehicle soon; nearest charger is {charger_km:g} km away."
            )
        else:
            message = (
                f"Battery charge is {battery_percent:g}%, below the "
                f"{RESERVE_BATTERY_THRESHOLD_PERCENT:g}% reserve threshold, and the nearest "
                f"charger is {charger_km:g} km away, beyond the "
                f"{CHARGER_DISTANCE_THRESHOLD_KM:g} km range. Charge the vehicle soon."
            )
        alerts.append(
            SafetyAlert(
                rule_id="SAFETY-001",
                code="LOW_BATTERY",
                severity=Severity.warning,
                message=message,
            )
        )

    # Strictly greater than: exactly 50°C is within spec and must not warn.
    if telemetry.battery_temp_c > BATTERY_TEMP_THRESHOLD_C:
        alerts.append(
            SafetyAlert(
                rule_id="SAFETY-002",
                code="BATTERY_OVERHEAT",
                severity=Severity.critical,
                message=(
                    f"HIGH PRIORITY: battery temperature is {telemetry.battery_temp_c:g}°C, "
                    f"above the {BATTERY_TEMP_THRESHOLD_C:g}°C limit. Reduce load and "
                    f"stop in a safe place to let the battery cool."
                ),
            )
        )

    return sorted(alerts, key=lambda alert: (-_SEVERITY_RANK[alert.severity.value], alert.rule_id))


def highest_severity(alerts: list[SafetyAlert]) -> Severity | None:
    """Return the most severe alert level, or ``None`` when there are no alerts."""
    if not alerts:
        return None
    return max((alert.severity for alert in alerts), key=lambda severity: _SEVERITY_RANK[severity.value])


def safety_text(alerts: list[SafetyAlert]) -> str:
    """Render alerts as plain text that must always reach the driver."""
    return " ".join(alert.message for alert in alerts)
