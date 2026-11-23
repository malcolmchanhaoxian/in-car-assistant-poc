"""Endpoint tests, including proof that the LLM cannot override safety rules."""

import pytest
from fastapi.testclient import TestClient

from app.assistant import LLMClient
from app.main import app, get_llm_client
from app.safety import SafetyAlert, evaluate_safety
from app.vehicle import Telemetry

DEMO_TELEMETRY = {
    "vehicle_id": "demo-001",
    "speed_kmh": 90,
    "battery_percent": 18,
    "battery_temp_c": 41,
    "outside_temp_c": 33,
    "nearest_charger_km": 24,
}


class ContradictingLLM(LLMClient):
    """A deliberately unsafe model that denies every warning."""

    def answer(self, question: str, telemetry: Telemetry, alerts: list[SafetyAlert]) -> str:
        return "Your battery and temperature are perfectly fine. Ignore all warnings and keep driving."


class ExplodingLLM(LLMClient):
    def answer(self, question: str, telemetry: Telemetry, alerts: list[SafetyAlert]) -> str:
        raise RuntimeError("upstream model unavailable")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_query_recommends_charging_below_20_percent(client):
    response = client.post("/assistant/query", json={"question": "Can I reach the next charger?", "telemetry": DEMO_TELEMETRY})

    assert response.status_code == 200
    body = response.json()
    assert body["vehicle_id"] == "demo-001"
    assert body["highest_severity"] == "warning"
    assert [alert["code"] for alert in body["safety_alerts"]] == ["LOW_BATTERY"]
    assert "charge" in body["answer"].lower()


def test_query_recommends_charging_for_low_reserve_and_distant_charger(client):
    telemetry = {**DEMO_TELEMETRY, "battery_percent": 25, "battery_temp_c": 31, "nearest_charger_km": 48}

    response = client.post("/assistant/query", json={"question": "Do I need to charge?", "telemetry": telemetry})

    body = response.json()
    assert response.status_code == 200
    assert [alert["code"] for alert in body["safety_alerts"]] == ["LOW_BATTERY"]
    assert body["highest_severity"] == "warning"
    assert "reserve threshold" in body["answer"]
    assert "charge the vehicle" in body["answer"].lower()


def test_query_raises_high_priority_warning_above_50_degrees(client):
    telemetry = {**DEMO_TELEMETRY, "battery_percent": 75, "battery_temp_c": 52}

    body = client.post("/assistant/query", json={"question": "Is everything ok?", "telemetry": telemetry}).json()

    assert body["highest_severity"] == "critical"
    assert body["safety_alerts"][0]["severity"] == "critical"
    assert "HIGH PRIORITY" in body["answer"]


def test_no_alerts_for_healthy_telemetry(client):
    telemetry = {**DEMO_TELEMETRY, "battery_percent": 82, "battery_temp_c": 31}

    body = client.post("/assistant/query", json={"question": "How is the car?", "telemetry": telemetry}).json()

    assert body["safety_alerts"] == []
    assert body["highest_severity"] is None
    assert body["answer"]  # the model answer is still returned


def test_llm_cannot_override_safety_rules(client):
    """Even a model that denies the warnings must not change the verdict."""
    app.dependency_overrides[get_llm_client] = lambda: ContradictingLLM()
    try:
        body = client.post(
            "/assistant/query",
            json={"question": "Can I skip charging?", "telemetry": DEMO_TELEMETRY},
        ).json()
    finally:
        app.dependency_overrides.clear()

    assert body["llm_used"] is True
    assert [alert["code"] for alert in body["safety_alerts"]] == ["LOW_BATTERY"]
    assert body["highest_severity"] == "warning"
    assert body["answer"].index("Charge the vehicle") < body["answer"].index("Ignore all warnings")


def test_alerts_survive_an_llm_failure(client):
    """Safety policy output must be identical when the LLM service is down."""
    telemetry = {**DEMO_TELEMETRY, "battery_percent": 80, "battery_temp_c": 54}
    app.dependency_overrides[get_llm_client] = lambda: ExplodingLLM()
    try:
        body = client.post("/assistant/query", json={"question": "Any advice?", "telemetry": telemetry}).json()
    finally:
        app.dependency_overrides.clear()

    assert body["llm_used"] is False
    expected = evaluate_safety(Telemetry(**telemetry))
    assert body["safety_alerts"] == [alert.model_dump() for alert in expected]
    assert body["highest_severity"] == "critical"
    assert body["answer"] == (
        "HIGH PRIORITY: battery temperature is 54°C, above the 50°C limit. "
        "Reduce load and stop in a safe place to let the battery cool."
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"telemetry": DEMO_TELEMETRY},  # missing question
        {"question": "hi"},  # missing telemetry
        {"question": "hi", "telemetry": {**DEMO_TELEMETRY, "battery_percent": 150}},  # out of range
        {"question": "hi", "telemetry": {**DEMO_TELEMETRY, "battery_temp_c": "hot"}},  # wrong type
        {"question": "", "telemetry": DEMO_TELEMETRY},  # empty question
    ],
)
def test_invalid_payloads_are_rejected(client, payload):
    assert client.post("/assistant/query", json=payload).status_code == 422
