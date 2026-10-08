"""ViGo Backend — FastAPI entrypoint.

Run with:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Env is loaded from a `.env` file in the project root (see .env.example).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# IMPORTANT: load .env BEFORE anything reads os.environ.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from fastapi import (  # noqa: E402  — must come after load_dotenv
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from backend import db  # noqa: E402
from backend.schemas import EventIn, TelemetryIn  # noqa: E402


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="ViGo Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Module-level broadcast registry
_ws_clients: set[WebSocket] = set()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expected_token_hash() -> str:
    token = os.environ.get("DEVICE_TOKEN", "")
    return hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def verify_device(
    authorization: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None, alias="X-Device-ID"),
) -> str:
    if not x_device_id:
        raise HTTPException(status_code=400, detail="Missing X-Device-ID header")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    presented_token = authorization.split(" ", 1)[1].strip()
    presented_hash = hashlib.sha256(presented_token.encode("utf-8")).hexdigest()
    expected = _expected_token_hash()

    if not expected or presented_hash != expected:
        raise HTTPException(status_code=401, detail="Invalid device token")

    return x_device_id


# ---------------------------------------------------------------------------
# Broadcast helper
# ---------------------------------------------------------------------------
async def broadcast(message: dict[str, Any]) -> None:
    if not _ws_clients:
        return
    payload = json.dumps(message, default=str)
    dead: list[WebSocket] = []
    for client in list(_ws_clients):
        try:
            await client.send_text(payload)
        except (WebSocketDisconnect, RuntimeError, Exception):
            dead.append(client)
    for client in dead:
        _ws_clients.discard(client)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _on_startup() -> None:
    info = await db.init_db()

    host = os.environ.get("HOST", "0.0.0.0")
    port = os.environ.get("PORT", "8000")
    seed_device_id = info["seed_device_id"]
    seeded = info["seeded_now"]
    token = os.environ.get("DEVICE_TOKEN", "")
    token_display = (token[:6] + "..." + token[-4:]) if len(token) > 12 else "(not set)"

    banner = "=" * 72
    print(banner, flush=True)
    print("=== ViGo Backend STARTED", flush=True)
    print(f"=== URL:            http://{host}:{port}", flush=True)
    print(f"=== Local URL:      http://127.0.0.1:{port}", flush=True)
    print(f"=== Health:         http://127.0.0.1:{port}/health", flush=True)
    print(f"=== WebSocket:      ws://127.0.0.1:{port}/ws", flush=True)
    print(f"=== Seed device:    {seed_device_id}  ({'created now' if seeded else 'already present'})", flush=True)
    print(f"=== DEVICE_TOKEN:   {token_display}", flush=True)
    print(f"=== DB_PATH:        {os.environ.get('DB_PATH', './vigo.db')}", flush=True)
    print(banner, flush=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "time": _utcnow_iso()}


@app.post("/api/v1/telemetry")
async def post_telemetry(
    body: TelemetryIn,
    _device_id: str = Depends(verify_device),
) -> dict[str, Any]:
    payload = body.model_dump()
    try:
        row = await db.insert_telemetry(payload)
    except db.DuplicateError:
        return {
            "success": True,
            "duplicate": True,
            "message_id": payload["message_id"],
        }

    await db.update_device_last_seen(
        device_id=payload["device_id"],
        lat=payload["latitude"],
        lng=payload["longitude"],
        battery=payload["battery"],
        sos_status=None,  # telemetry alone doesn't mutate device.sos_status
    )

    await broadcast({"kind": "telemetry", "data": row})

    return {
        "success": True,
        "message_id": payload["message_id"],
        "server_received_at": row.get("server_received_at"),
    }


@app.post("/api/v1/events")
async def post_event(
    body: EventIn,
    _device_id: str = Depends(verify_device),
) -> dict[str, Any]:
    payload = body.model_dump()

    # Contract: trigger is required when type == 'SOS'
    if payload["type"] == "SOS" and not payload.get("trigger"):
        raise HTTPException(status_code=422, detail="`trigger` is required when type=SOS")

    row = await db.insert_event(payload)
    duplicate = bool(row.pop("_duplicate", False))

    # Only mutate device sos_status on SOS events (per contract).
    if not duplicate and payload["type"] == "SOS":
        await db.update_device_last_seen(
            device_id=payload["device_id"],
            lat=payload.get("latitude"),
            lng=payload.get("longitude"),
            battery=payload.get("battery"),
            sos_status=payload["sos_status"],
        )

    await broadcast({"kind": "event", "data": row})

    return {
        "success": True,
        "duplicate": duplicate,
        "event_id": payload["event_id"],
        "server_received_at": row.get("server_received_at"),
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.add(ws)
    try:
        seed_device_id = os.environ.get("SEED_DEVICE_ID", "VIGO001")
        snapshot = await db.get_snapshot(seed_device_id)
        await ws.send_text(json.dumps({"kind": "snapshot", "data": snapshot}, default=str))

        # Dashboard is read-only — just keep the connection alive and drain any inbound.
        while True:
            try:
                await ws.receive_text()
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Static / dashboard serving
# ---------------------------------------------------------------------------
_DASHBOARD_DIR = _PROJECT_ROOT / "dashboard"
_DASHBOARD_INDEX = _DASHBOARD_DIR / "index.html"

# Mount static only if the directory exists; otherwise skip to avoid startup error.
if _DASHBOARD_DIR.exists() and _DASHBOARD_DIR.is_dir():
    app.mount(
        "/dashboard-static",
        StaticFiles(directory=str(_DASHBOARD_DIR), html=False),
        name="dashboard-static",
    )


@app.get("/", response_class=HTMLResponse)
async def root(_request: Request) -> Any:
    if _DASHBOARD_INDEX.exists():
        return FileResponse(str(_DASHBOARD_INDEX))
    stub = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>ViGo Dashboard loading</title></head>
<body style="font-family: system-ui, sans-serif; padding: 2rem;">
  <h1>ViGo Dashboard loading</h1>
  <p>The dashboard files are not in place yet. Refresh in a moment.</p>
  <p>Backend health: <a href="/health">/health</a></p>
</body>
</html>"""
    return HTMLResponse(stub)
