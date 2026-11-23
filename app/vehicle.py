"""Vehicle telemetry models."""

from pydantic import BaseModel, Field


class Telemetry(BaseModel):
    """Current vehicle telemetry supplied with every driver question."""

    vehicle_id: str = Field(..., min_length=1)
    speed_kmh: float = Field(..., ge=0)
    battery_percent: float = Field(..., ge=0, le=100)
    battery_temp_c: float
    outside_temp_c: float
    nearest_charger_km: float = Field(..., ge=0)
