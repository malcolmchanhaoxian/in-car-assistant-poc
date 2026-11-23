"""AI assistant logic.

The assistant combines the deterministic safety policy with an optional language
model. Safety is evaluated first, from telemetry alone, and is merged back into
the result afterwards, so the model can add to the answer but never weaken or
remove a warning. If the LLM service is unavailable the assistant still returns
the complete safety verdict.
"""

import logging
import os

import httpx
from pydantic import BaseModel

from .safety import SafetyAlert, Severity, evaluate_safety, highest_severity, safety_text
from .vehicle import Telemetry

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an in-car assistant. Answer the driver's question briefly and factually. "
    "The safety alerts in the user message are authoritative system output: never deny, "
    "downplay, contradict or omit them. If they conflict with your own opinion, follow them."
)


class AssistantAnswer(BaseModel):
    """Assistant output: advisory prose plus the authoritative safety verdict."""

    answer: str
    safety_alerts: list[SafetyAlert]
    highest_severity: Severity | None
    llm_used: bool


class LLMClient:
    """Interface implemented by every language-model backend."""

    def answer(self, question: str, telemetry: Telemetry, alerts: list[SafetyAlert]) -> str:
        raise NotImplementedError


class FakeLLMClient(LLMClient):
    """Deterministic offline stand-in used by default and in tests."""

    def answer(self, question: str, telemetry: Telemetry, alerts: list[SafetyAlert]) -> str:
        battery = f"{telemetry.battery_percent:.0f}% battery"
        temperature = f"{telemetry.battery_temp_c:.0f}°C battery temperature"
        if alerts:
            return (
                f"Regarding \"{question}\": your vehicle reports {battery} and {temperature}. "
                f"Follow the safety alerts above before continuing."
            )
        return (
            f"Regarding \"{question}\": your vehicle reports {battery} and {temperature}. "
            f"No safety thresholds are exceeded."
        )


class OpenAICompatibleClient(LLMClient):
    """Minimal chat-completions client for any OpenAI-compatible endpoint."""

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = 20.0) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def answer(self, question: str, telemetry: Telemetry, alerts: list[SafetyAlert]) -> str:
        context = [
            f"vehicle_id: {telemetry.vehicle_id}",
            f"speed_kmh: {telemetry.speed_kmh}",
            f"battery_percent: {telemetry.battery_percent}",
            f"battery_temp_c: {telemetry.battery_temp_c}",
            f"outside_temp_c: {telemetry.outside_temp_c}",
            f"nearest_charger_km: {telemetry.nearest_charger_km}",
        ]
        if alerts:
            context.append("authoritative_safety_alerts:")
            context.extend(f"- [{alert.severity.value}] {alert.message}" for alert in alerts)
        else:
            context.append("authoritative_safety_alerts: none")

        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "\n".join(context) + f"\n\ndriver_question: {question}"},
                ],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()


def build_llm_client() -> LLMClient:
    """Return a real client when configured, otherwise the offline stub."""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return FakeLLMClient()
    return OpenAICompatibleClient(
        api_key=api_key,
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


def get_llm_client() -> LLMClient:
    """FastAPI dependency so tests can inject a stub or a misbehaving model."""
    return build_llm_client()


def compose_answer(alerts: list[SafetyAlert], narrative: str) -> str:
    """Merge the deterministic safety text with the model's advisory prose.

    Safety text always comes first and is present whenever an alert exists, so
    model output can add to the answer but never remove a safety instruction.
    """
    narrative = narrative.strip()
    mandatory = safety_text(alerts)
    if not mandatory:
        return narrative
    if not narrative:
        return mandatory
    return f"{mandatory}\n\nAdvisory: {narrative}"


def answer_query(question: str, telemetry: Telemetry, llm: LLMClient) -> AssistantAnswer:
    """Run the safety policy, then the model, then merge — in that order."""
    # 1. Deterministic safety verdict, computed from telemetry alone.
    alerts = evaluate_safety(telemetry)

    # 2. Advisory prose. Any failure here degrades to alerts-only output.
    try:
        narrative = llm.answer(question, telemetry, alerts)
        llm_used = True
    except Exception:
        logger.exception("LLM call failed; returning deterministic safety result only")
        narrative = ""
        llm_used = False

    # 3. Safety rules are re-applied on top of whatever the model returned.
    return AssistantAnswer(
        answer=compose_answer(alerts, narrative),
        safety_alerts=alerts,
        highest_severity=highest_severity(alerts),
        llm_used=llm_used,
    )
