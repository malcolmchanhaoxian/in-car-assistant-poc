"""FastAPI API for the in-car AI assistant POC."""

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from .assistant import LLMClient, answer_query, get_llm_client
from .safety import SafetyAlert, Severity
from .vehicle import Telemetry

app = FastAPI(
    title="In-Car AI Assistant POC",
    description=(
        "Answers driver questions using vehicle telemetry. Safety rules are evaluated "
        "deterministically and can never be overridden by the language model."
    ),
    version="0.1.0",
)


class QueryRequest(BaseModel):
    """Driver question plus the telemetry snapshot it was asked against."""

    question: str = Field(..., min_length=1)
    telemetry: Telemetry


class QueryResponse(BaseModel):
    """Assistant answer together with the authoritative safety verdict."""

    vehicle_id: str
    answer: str
    safety_alerts: list[SafetyAlert]
    highest_severity: Severity | None
    llm_used: bool


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/assistant/query", response_model=QueryResponse)
def assistant_query(
    request: QueryRequest,
    llm: LLMClient = Depends(get_llm_client),
) -> QueryResponse:
    result = answer_query(request.question, request.telemetry, llm)
    return QueryResponse(
        vehicle_id=request.telemetry.vehicle_id,
        answer=result.answer,
        safety_alerts=result.safety_alerts,
        highest_severity=result.highest_severity,
        llm_used=result.llm_used,
    )
