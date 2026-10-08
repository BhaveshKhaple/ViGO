"""Pydantic v2 models for the ViGo IoT device contract.

These schemas are the frozen contract between firmware (device) and the
backend. Extras are allowed so the firmware can send extra fields without
breaking the server. Keep this file and db.py in sync.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TelemetryIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    device_id: str = Field(..., examples=["VIGO001"])
    message_id: str = Field(..., examples=["msg-0001"])
    timestamp: str = Field(
        ...,
        description="ISO-8601 timestamp generated on the device",
        examples=["2026-10-08T12:34:56Z"],
    )
    latitude: float = Field(..., examples=[18.5204])
    longitude: float = Field(..., examples=[73.8567])
    battery: int = Field(..., ge=0, le=100, examples=[87])
    sos_status: Literal["NORMAL", "WARNING", "TRIGGERED"] = Field(
        default="NORMAL", examples=["NORMAL"]
    )
    speed: Optional[float] = Field(default=None, examples=[12.4])
    heading: Optional[float] = Field(default=None, examples=[180.0])
    network: Optional[str] = Field(default=None, examples=["4G"])


class EventIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(..., examples=["evt-0001"])
    device_id: str = Field(..., examples=["VIGO001"])
    type: Literal["SOS", "WARNING"] = Field(..., examples=["SOS"])
    sos_status: Literal["TRIGGERED", "WARNING"] = Field(..., examples=["TRIGGERED"])
    trigger: Optional[Literal["BUTTON", "LOUD_SOUND"]] = Field(
        default=None,
        description="Required when type == 'SOS'",
        examples=["BUTTON"],
    )
    warning_type: Optional[str] = Field(default=None, examples=["LOW_BATTERY"])
    timestamp: str = Field(..., examples=["2026-10-08T12:34:56Z"])
    latitude: Optional[float] = Field(default=None, examples=[18.5204])
    longitude: Optional[float] = Field(default=None, examples=[73.8567])
    battery: Optional[int] = Field(default=None, ge=0, le=100, examples=[42])
    metadata: Optional[dict[str, Any]] = Field(default=None, examples=[{"note": "test"}])
