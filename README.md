# In-Car AI Assistant POC (FastAPI)

A proof-of-concept backend for an automotive in-car assistant. It answers a driver's
natural-language question using the current vehicle telemetry, and **always** returns a
deterministic safety verdict that the language model cannot override.

## Features

- `POST /assistant/query` — driver question + telemetry snapshot → answer + safety alerts.
- `GET /health` — liveness probe.
- Deterministic safety rules evaluated **before** and **independently of** any AI call.
- Pluggable LLM layer: runs fully offline by default, real OpenAI-compatible endpoint via env vars.
- 45 unit/API tests, including proof that a contradictory or failing model cannot suppress warnings.

## Requirements

- Python 3.11+ (developed against 3.12)

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows PowerShell
# source .venv/bin/activate       # macOS / Linux
python -m pip install -r requirements.txt
```

## Run

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs: http://127.0.0.1:8000/docs

## Test

```powershell
.\.venv\Scripts\python -m pytest
```

## Example request

The repository ships [telemetry.json](telemetry.json), which already triggers the low-battery rule.

```powershell
$t = Get-Content telemetry.json -Raw | ConvertFrom-Json
$body = @{ question = "Can I reach the next charger?"; telemetry = $t } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri http://127.0.0.1:8000/assistant/query -Method Post -ContentType "application/json" -Body $body
```

<details>
<summary>curl / JSON body equivalent</summary>

```bash
curl -X POST http://127.0.0.1:8000/assistant/query \
  -H "Content-Type: application/json" \
  -d '{
        "question": "Can I reach the next charger?",
        "telemetry": {
          "vehicle_id": "demo-001",
          "speed_kmh": 90,
          "battery_percent": 18,
          "battery_temp_c": 41,
          "outside_temp_c": 33,
          "nearest_charger_km": 24
        }
      }'
```
</details>

Response:

```json
{
  "vehicle_id": "demo-001",
  "answer": "Battery charge is 18%, below the 20% limit. Charge the vehicle soon; nearest charger is 24 km away.\n\nAdvisory: Regarding \"Can I reach the next charger?\": ...",
  "safety_alerts": [
    {
      "rule_id": "SAFETY-001",
      "code": "LOW_BATTERY",
      "severity": "warning",
      "message": "Battery charge is 18%, below the 20% limit. Charge the vehicle soon; nearest charger is 24 km away."
    }
  ],
  "highest_severity": "warning",
  "llm_used": true
}
```

## API contract

### Request

| Field | Type | Notes |
| --- | --- | --- |
| `question` | string | Required, non-empty. Driver's natural-language question. |
| `telemetry.vehicle_id` | string | Required, non-empty. |
| `telemetry.speed_kmh` | float | Required, `>= 0`. |
| `telemetry.battery_percent` | float | Required, `0..100`. |
| `telemetry.battery_temp_c` | float | Required. Battery pack temperature. |
| `telemetry.outside_temp_c` | float | Required. Ambient temperature. |
| `telemetry.nearest_charger_km` | float | Required, `>= 0`. |
| `telemetry` | object | Required. |

Invalid payloads return `422`.

### Response

| Field | Type | Notes |
| --- | --- | --- |
| `vehicle_id` | string | Echoed from the request. |
| `answer` | string | Driver-facing text: deterministic safety text first, then advisory prose. |
| `safety_alerts` | array | Authoritative rule output; empty when nothing is triggered. |
| `highest_severity` | `info \| warning \| critical \| null` | Highest alert level, `null` if no alerts. |
| `llm_used` | boolean | `false` when the model call failed and only rules produced the answer. |

## Deterministic safety rules

Implemented in [app/safety.py](app/safety.py). Pure functions, no I/O, no LLM:

| Rule | Condition | Severity | Action |
| --- | --- | --- | --- |
| `SAFETY-001` (`LOW_BATTERY`) | `battery_percent < 20` **OR** (`battery_percent < 30` AND `nearest_charger_km > 40`) | `warning` | Recommend charging; the message states which condition fired. |
| `SAFETY-002` (`BATTERY_OVERHEAT`) | `battery_temp_c > 50` | `critical` | High-priority warning; reduce load and stop to cool down. |

Both charging conditions raise the same alert (`LOW_BATTERY`), so the API contract is unchanged.

Boundaries are exclusive: exactly `20 %` (with a reachable charger), exactly `30 %`, exactly
`40 km` and exactly `50 °C` do **not** trigger. Thresholds are named constants
(`LOW_BATTERY_THRESHOLD_PERCENT`, `RESERVE_BATTERY_THRESHOLD_PERCENT`,
`CHARGER_DISTANCE_THRESHOLD_KM`, `BATTERY_TEMP_THRESHOLD_C`) so they can be audited and changed
in one place. Alerts are ordered critical-first, then by `rule_id`.

## Why the LLM cannot override the safety rules

1. **Rules run first, on telemetry alone.** `evaluate_safety()` in [app/safety.py](app/safety.py)
   never sees the prompt or model output, so its verdict is reproducible for identical telemetry.
2. **Alerts are structurally separate.** The response always carries `safety_alerts` as its own
   field; model prose only fills `answer`.
3. **Safety text is prepended to the answer.** `compose_answer()` in [app/assistant.py](app/assistant.py)
   puts the deterministic messages first, so even a model that says "everything is fine" cannot
   remove or hide a warning from the driver-facing text.
4. **Model failure is non-fatal.** If the LLM errors or times out, the response degrades to
   rules-only (`llm_used: false`) instead of losing the warning.
5. **The prompt reinforces it.** When a real endpoint is configured, the alerts are passed as
   read-only `authoritative_safety_alerts` context with an explicit instruction not to contradict them.

## LLM configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `LLM_API_KEY` | *(unset)* | When unset, the deterministic offline `FakeLLMClient` is used. |
| `LLM_MODEL` | `gpt-4o-mini` | Model name for the chat-completions call. |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible base URL. |

## Project layout

```
app/
  main.py       FastAPI API: POST /assistant/query, GET /health, request/response schemas
  assistant.py  AI assistant logic: LLM clients, prompt, safety/LLM merge, orchestration
  safety.py     Deterministic safety policy (no LLM, no I/O)
  vehicle.py    Vehicle telemetry models
tests/
  test_safety.py  Rule unit tests incl. boundary values and determinism
  test_api.py     Endpoint tests incl. LLM-cannot-override and LLM-unavailable cases
```
