# ViGo ESP32 Firmware Spec

**Audience:** ESP32 firmware developer. One page. Dense. No fluff.

---

## Base URL

Demo laptop IP: `<PLACEHOLDER — Bhavesh fills with ipconfig IPv4 at venue>`
Full base: `http://<ip>:8000`
ESP32 and laptop **must be on the same Wi-Fi network**.

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | Boot-time reachability check |
| POST | `/api/v1/telemetry` | Periodic GPS + battery (every 5s) |
| POST | `/api/v1/events` | SOS / WARNING (fire immediately on trigger) |

---

## Required headers (every POST)

```
Authorization: Bearer <DEVICE_TOKEN>
X-Device-ID: VIGO001
Content-Type: application/json
```

`DEVICE_TOKEN` is in the laptop's `.env` — ask Bhavesh at the venue. Flash it into the build. Mismatches = `401`.

---

## curl examples (copy-paste ready)

**Telemetry (every 5s):**
```bash
curl -X POST http://<ip>:8000/api/v1/telemetry \
  -H "Authorization: Bearer REPLACE_WITH_DEVICE_TOKEN" \
  -H "X-Device-ID: VIGO001" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "VIGO001",
    "message_id": "MSG-VIGO001-137",
    "timestamp": "2026-10-09T04:00:00Z",
    "latitude": 18.5204,
    "longitude": 73.8567,
    "battery": 85,
    "sos_status": "NORMAL",
    "speed": 24.4,
    "heading": 180,
    "network": "WIFI"
  }'
```

**SOS BUTTON event:**
```bash
curl -X POST http://<ip>:8000/api/v1/events \
  -H "Authorization: Bearer REPLACE_WITH_DEVICE_TOKEN" \
  -H "X-Device-ID: VIGO001" \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "EVT-VIGO001-42",
    "device_id": "VIGO001",
    "type": "SOS",
    "sos_status": "TRIGGERED",
    "trigger": "BUTTON",
    "timestamp": "2026-10-09T04:00:12Z",
    "latitude": 18.5204,
    "longitude": 73.8567,
    "battery": 84
  }'
```

**SOS LOUD_SOUND event:**
```bash
curl -X POST http://<ip>:8000/api/v1/events \
  -H "Authorization: Bearer REPLACE_WITH_DEVICE_TOKEN" \
  -H "X-Device-ID: VIGO001" \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "EVT-VIGO001-43",
    "device_id": "VIGO001",
    "type": "SOS",
    "sos_status": "TRIGGERED",
    "trigger": "LOUD_SOUND",
    "timestamp": "2026-10-09T04:00:18Z",
    "latitude": 18.5204,
    "longitude": 73.8567,
    "battery": 84,
    "metadata": {"sound_level": 87}
  }'
```

---

## Field tables

### Telemetry
| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `device_id` | string | yes | Exactly `VIGO001` for this demo |
| `message_id` | string | yes | `MSG-VIGO001-<int>`, monotonically increasing per boot |
| `timestamp` | string (ISO-8601 UTC) | yes | Must end with `Z`, e.g. `2026-10-09T04:00:00Z` |
| `latitude` | float | yes | Decimal degrees, WGS84 |
| `longitude` | float | yes | Decimal degrees, WGS84 |
| `battery` | int (0–100) | yes | Percent |
| `sos_status` | string | yes | `NORMAL` \| `TRIGGERED` \| `WARNING` |
| `speed` | float | yes | km/h |
| `heading` | int (0–359) | yes | Degrees from true north |
| `network` | string | yes | `WIFI` \| `LTE` \| `OFFLINE_QUEUED` |

### Event
| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `event_id` | string | yes | `EVT-VIGO001-<int>`, unique per event |
| `device_id` | string | yes | `VIGO001` |
| `type` | string | yes | `SOS` or `WARNING` |
| `sos_status` | string | yes | `TRIGGERED` for SOS, `WARNING` for warnings |
| `trigger` | string | yes for SOS | `BUTTON` \| `LOUD_SOUND` |
| `warning_type` | string | yes for WARNING | e.g. `RASH_DRIVING` |
| `timestamp` | string (ISO-8601 UTC) | yes | `Z` suffix |
| `latitude` | float | yes for SOS | Decimal degrees |
| `longitude` | float | yes for SOS | Decimal degrees |
| `battery` | int | yes for SOS | Percent |
| `metadata` | object | optional | Trigger-specific, e.g. `{"sound_level": 87}` |

---

## Timing

- **Telemetry:** every **5 seconds**, continuous while powered.
- **Events:** fire **immediately** on trigger. Do not batch. Do not wait for the next telemetry tick.
- **Health check:** once at boot. If `/health` fails, retry every 10s with backoff; still emit telemetry attempts in the background so recovery is automatic once Wi-Fi returns.

---

## SOS latch behavior

Once the device sends an event with `sos_status: TRIGGERED`, **do not** downgrade subsequent telemetry packets back to `sos_status: NORMAL` automatically. The dashboard latches the alert and the server retains `device.sos_status: TRIGGERED` until an operator calls the resolve API (out of scope for this demo). Only revert to `NORMAL` after an explicit local reset (e.g. hardware reset button held 3s), not on a timer.

---

## Idempotency

- Resending the **same `message_id`** → server returns `200`, no duplicate stored.
- Resending the **same `event_id`** → server returns `200`, no duplicate alert.
- Safe to retry freely on network blips. **Preferred:** queue failed POSTs in flash and replay on reconnect with the original IDs intact. Do not mint new IDs on retry.

---

## Expected responses

| Code | Meaning | Device action |
|------|---------|---------------|
| `200` | Accepted (fresh or replay). Body: `{"status":"ok","id":"..."}` | Continue |
| `400` | Missing/invalid header (e.g. `X-Device-ID`) | Log + fix firmware, don't retry forever |
| `401` | Bad or missing bearer token | Stop; token mismatch, needs reflash |
| `422` | Schema validation failed (bad field type, missing required) | Log full response body, don't retry |
| `5xx` | Backend error | Retry with exponential backoff (1s → 2s → 4s, cap 30s) |

---

## Change control

**Any field changes — rename, type change, new required field, new trigger value — require updating `CONTRACT.md` and pinging Bhavesh first. Do not freelance on the shape.** The dashboard and simulator are coded against this exact schema; drifting the firmware alone will silently break the demo.
