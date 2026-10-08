"""Async SQLite layer for ViGo backend.

Owns schema creation, seeding, and all read/write helpers used by main.py.
Uses aiosqlite so we don't block the FastAPI event loop.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite


class DuplicateError(Exception):
    """Raised when a unique constraint (e.g. telemetry.message_id) is violated."""


def _db_path() -> str:
    return os.environ.get("DB_PATH", "./vigo.db")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    device_id TEXT UNIQUE,
    token_hash TEXT,
    status TEXT DEFAULT 'ACTIVE',
    last_seen_at TEXT,
    last_latitude REAL,
    last_longitude REAL,
    last_battery INTEGER,
    sos_status TEXT DEFAULT 'NORMAL',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    message_id TEXT UNIQUE,
    device_timestamp TEXT,
    server_received_at TEXT,
    latitude REAL,
    longitude REAL,
    battery INTEGER,
    speed REAL,
    heading REAL,
    sos_status TEXT,
    raw_payload TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    event_id TEXT UNIQUE,
    type TEXT,
    sos_status TEXT,
    trigger TEXT,
    warning_type TEXT,
    event_timestamp TEXT,
    server_received_at TEXT,
    latitude REAL,
    longitude REAL,
    battery INTEGER,
    metadata TEXT,
    acknowledged INTEGER DEFAULT 0,
    resolved INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_telemetry_device_time
    ON telemetry(device_id, device_timestamp);

CREATE INDEX IF NOT EXISTS idx_events_device_time
    ON events(device_id, event_timestamp DESC);
"""


def _connect():
    """Return an aiosqlite connection context manager.

    Usage:
        async with _connect() as conn:
            await _prep(conn)
            ...
    """
    return aiosqlite.connect(_db_path())


async def _prep(conn: aiosqlite.Connection) -> None:
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")


def _row_to_dict(row: Optional[aiosqlite.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


async def init_db() -> dict[str, Any]:
    """Create tables + indexes if missing, and seed the demo device."""
    seed_device_id = os.environ.get("SEED_DEVICE_ID", "VIGO001")
    device_token = os.environ.get("DEVICE_TOKEN", "")
    token_hash = _hash_token(device_token) if device_token else ""

    async with _connect() as conn:
        await _prep(conn)
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()

        cursor = await conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (seed_device_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()

        if row is None:
            await conn.execute(
                """
                INSERT INTO devices
                    (device_id, token_hash, status, created_at, sos_status)
                VALUES (?, ?, 'ACTIVE', ?, 'NORMAL')
                """,
                (seed_device_id, token_hash, _utcnow_iso()),
            )
            await conn.commit()
            seeded = True
        else:
            # Keep token_hash in sync with current env in case the dev rotated it.
            if token_hash:
                await conn.execute(
                    "UPDATE devices SET token_hash = ? WHERE device_id = ?",
                    (token_hash, seed_device_id),
                )
                await conn.commit()
            seeded = False

    return {"seed_device_id": seed_device_id, "seeded_now": seeded}


async def insert_telemetry(payload: dict[str, Any]) -> dict[str, Any]:
    """Insert telemetry row, raise DuplicateError on duplicate message_id."""
    server_received_at = _utcnow_iso()
    raw = json.dumps(payload, default=str)
    new_id: Optional[int] = None

    try:
        async with _connect() as conn:
            await _prep(conn)
            cursor = await conn.execute(
                """
                INSERT INTO telemetry (
                    device_id, message_id, device_timestamp, server_received_at,
                    latitude, longitude, battery, speed, heading, sos_status,
                    raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["device_id"],
                    payload["message_id"],
                    payload["timestamp"],
                    server_received_at,
                    payload["latitude"],
                    payload["longitude"],
                    payload["battery"],
                    payload.get("speed"),
                    payload.get("heading"),
                    payload.get("sos_status", "NORMAL"),
                    raw,
                ),
            )
            new_id = cursor.lastrowid
            await cursor.close()
            await conn.commit()
    except aiosqlite.IntegrityError as exc:
        if "message_id" in str(exc).lower() or "unique" in str(exc).lower():
            raise DuplicateError(
                f"Telemetry message_id already exists: {payload['message_id']}"
            ) from exc
        raise

    async with _connect() as conn:
        await _prep(conn)
        cursor = await conn.execute("SELECT * FROM telemetry WHERE id = ?", (new_id,))
        row = await cursor.fetchone()
        await cursor.close()

    return _row_to_dict(row) or {}


async def insert_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Insert event row. Duplicate event_id returns the existing row (idempotent)."""
    server_received_at = _utcnow_iso()
    metadata_json = (
        json.dumps(payload["metadata"]) if payload.get("metadata") is not None else None
    )

    async with _connect() as conn:
        await _prep(conn)

        # Idempotency check first — firmware retries are expected.
        cursor = await conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (payload["event_id"],)
        )
        existing = await cursor.fetchone()
        await cursor.close()
        if existing is not None:
            result = _row_to_dict(existing) or {}
            result["_duplicate"] = True
            return result

        try:
            cursor = await conn.execute(
                """
                INSERT INTO events (
                    device_id, event_id, type, sos_status, trigger, warning_type,
                    event_timestamp, server_received_at, latitude, longitude,
                    battery, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["device_id"],
                    payload["event_id"],
                    payload["type"],
                    payload["sos_status"],
                    payload.get("trigger"),
                    payload.get("warning_type"),
                    payload["timestamp"],
                    server_received_at,
                    payload.get("latitude"),
                    payload.get("longitude"),
                    payload.get("battery"),
                    metadata_json,
                ),
            )
            new_id = cursor.lastrowid
            await cursor.close()
            await conn.commit()
        except aiosqlite.IntegrityError:
            # Race condition — someone inserted between SELECT and INSERT.
            cursor = await conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (payload["event_id"],)
            )
            row = await cursor.fetchone()
            await cursor.close()
            result = _row_to_dict(row) or {}
            result["_duplicate"] = True
            return result

        cursor = await conn.execute("SELECT * FROM events WHERE id = ?", (new_id,))
        row = await cursor.fetchone()
        await cursor.close()

    result = _row_to_dict(row) or {}
    result["_duplicate"] = False
    return result


async def update_device_last_seen(
    device_id: str,
    lat: Optional[float],
    lng: Optional[float],
    battery: Optional[int],
    sos_status: Optional[str],
) -> None:
    """Patch device.last_* columns. Only updates sos_status if a non-None value is given."""
    now = _utcnow_iso()
    async with _connect() as conn:
        await _prep(conn)
        if sos_status is None:
            await conn.execute(
                """
                UPDATE devices
                   SET last_seen_at = ?,
                       last_latitude = ?,
                       last_longitude = ?,
                       last_battery = ?
                 WHERE device_id = ?
                """,
                (now, lat, lng, battery, device_id),
            )
        else:
            await conn.execute(
                """
                UPDATE devices
                   SET last_seen_at = ?,
                       last_latitude = ?,
                       last_longitude = ?,
                       last_battery = ?,
                       sos_status = ?
                 WHERE device_id = ?
                """,
                (now, lat, lng, battery, sos_status, device_id),
            )
        await conn.commit()


async def get_snapshot(device_id: str) -> dict[str, Any]:
    """Dashboard-friendly bundle: device + latest telemetry + recent events."""
    async with _connect() as conn:
        await _prep(conn)

        cursor = await conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        )
        device_row = await cursor.fetchone()
        await cursor.close()

        cursor = await conn.execute(
            """
            SELECT * FROM telemetry
             WHERE device_id = ?
             ORDER BY id DESC
             LIMIT 1
            """,
            (device_id,),
        )
        latest_telemetry_row = await cursor.fetchone()
        await cursor.close()

        cursor = await conn.execute(
            """
            SELECT * FROM events
             WHERE device_id = ?
             ORDER BY id DESC
             LIMIT 20
            """,
            (device_id,),
        )
        event_rows = await cursor.fetchall()
        await cursor.close()

    device = _row_to_dict(device_row)
    latest_telemetry = _row_to_dict(latest_telemetry_row)
    recent_events = [_row_to_dict(r) or {} for r in event_rows]

    # sos_latched: the most recent event is TRIGGERED and not resolved
    sos_latched = False
    if recent_events:
        latest_event = recent_events[0]
        if (
            latest_event.get("sos_status") == "TRIGGERED"
            and not latest_event.get("resolved")
        ):
            sos_latched = True

    return {
        "device": device,
        "latest_telemetry": latest_telemetry,
        "recent_events": recent_events,
        "sos_latched": sos_latched,
    }
