"""Pydantic request/response models for the v1 API.

Field names loosely follow CLAUDE.md Section 22's example payloads, kept
practical for the v1 demo scope (no scenarios/sharing yet).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class OriginModel(BaseModel):
    lat: float
    lon: float


class FuelInput(BaseModel):
    mode: Literal["full", "liters", "percent"] = "percent"
    value: float = 100.0


class RangeRequest(BaseModel):
    origin: OriginModel
    vesselProfileId: str = "axopar-28-2019-verado-300"
    fuel: FuelInput = Field(default_factory=lambda: FuelInput(mode="percent", value=100.0))
    reservePct: float = 20.0
    speedKn: Optional[float] = None
    rpm: Optional[float] = None
    seaState: Literal["calm", "moderate", "rough"] = "calm"
    loadState: Literal["light", "normal", "heavy"] = "normal"
    clearanceM: float = 50.0
    rangeMode: Literal["one_way", "round_trip"] = "one_way"
    depthPolicy: Literal["conservative", "permissive"] = "conservative"
    depthSafetyMarginM: float = Field(default=0.5, ge=0.0, le=5.0)
    developerMode: bool = False

    @model_validator(mode="after")
    def permissive_depth_requires_developer_mode(self):
        if self.depthPolicy == "permissive" and not self.developerMode:
            raise ValueError("permissive depth policy requires developerMode=true")
        return self


class RouteRequest(BaseModel):
    origin: OriginModel
    destination: OriginModel
    vesselProfileId: str = "axopar-28-2019-verado-300"
    fuel: FuelInput = Field(default_factory=lambda: FuelInput(mode="percent", value=100.0))
    reservePct: float = 20.0
    speedKn: Optional[float] = None
    rpm: Optional[float] = None
    seaState: Literal["calm", "moderate", "rough"] = "calm"
    loadState: Literal["light", "normal", "heavy"] = "normal"
    clearanceM: float = 50.0
    depthPolicy: Literal["conservative", "permissive"] = "conservative"
    depthSafetyMarginM: float = Field(default=0.5, ge=0.0, le=5.0)
    developerMode: bool = False

    @model_validator(mode="after")
    def permissive_depth_requires_developer_mode(self):
        if self.depthPolicy == "permissive" and not self.developerMode:
            raise ValueError("permissive depth policy requires developerMode=true")
        return self


class VerifyRequest(RangeRequest):
    """Developer-only re-verification of supplied WGS84 polygon GeoJSON."""

    geometry: dict[str, Any]

    @model_validator(mode="after")
    def verification_requires_developer_mode(self):
        if not self.developerMode:
            raise ValueError("verification requires developerMode=true")
        return self


class PointDiagnosticRequest(RangeRequest):
    """Developer-only explanation of a point's navigability decision."""

    point: OriginModel

    @model_validator(mode="after")
    def diagnostics_require_developer_mode(self):
        if not self.developerMode:
            raise ValueError("point diagnostics require developerMode=true")
        return self


class SegmentDiagnosticRequest(RangeRequest):
    """Developer-only explanation of a candidate route segment decision."""

    end: OriginModel

    @model_validator(mode="after")
    def diagnostics_require_developer_mode(self):
        if not self.developerMode:
            raise ValueError("segment diagnostics require developerMode=true")
        return self
