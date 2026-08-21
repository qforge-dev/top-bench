from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AmpResponse(ApiModel):
    id: str
    name: str
    amp_type: str
    control_names: list[str]


class ManifestCase(ApiModel):
    id: str
    positions: list[list[float]]
    dry_key: str
    dry_sha256: str
    download_url: str
    duration_seconds: float


class ManifestResponse(ApiModel):
    amp: AmpResponse
    cases: list[ManifestCase]


class CreateRunRequest(ApiModel):
    amp_id: str
    name: str
    creator: str = "anonymous"
    unique_positions_used: int
    audio_duration_sum: float
    turns: int
    training_time: float
    description: str
    parameter_count: int


class CreateRunResponse(ApiModel):
    id: str
    status: str
    total_cases: int


class EventRequest(ApiModel):
    kind: str
    case_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventResponse(ApiModel):
    id: int
    kind: str
    case_id: str | None
    payload: dict[str, Any]
    occurred_at: datetime


class EventsResponse(ApiModel):
    events: list[EventResponse]
    next_after_id: int | None


class CaseResultResponse(ApiModel):
    case_id: str
    status: str
    realtime_x: float | None
    esr: float | None
    human_weighted_esr: float | None
    mrstft: float | None
    level_db: float | None
    peak_db: float | None
    correlation: float | None
    nam_esr: float | None
    nam_human_weighted_esr: float | None
    nam_mrstft: float | None
    nam_level_db: float | None
    nam_peak_db: float | None
    nam_correlation: float | None


class RunResponse(ApiModel):
    id: str
    name: str
    creator: str
    amp_id: str
    amp_name: str
    amp_type: str
    unique_positions_used: int
    audio_duration_sum: float
    turns: int
    training_time: float
    description: str
    parameter_count: int
    status: str
    total_cases: int
    completed_cases: int
    metrics: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None
    cases: list[CaseResultResponse] = Field(default_factory=list)


class LeaderboardResponse(ApiModel):
    runs: list[RunResponse]
    amps: list[AmpResponse]
    creators: list[str]


class RunCaseIndexItem(ApiModel):
    case_id: str
    index: int
    chunk_index: int
    position_index: int
    status: str
    url: str


class RunCaseIndexResponse(ApiModel):
    run: RunResponse
    cases: list[RunCaseIndexItem]


class RunCaseMetricsResponse(ApiModel):
    realtime_x: float | None
    esr: float | None
    human_weighted_esr: float | None
    mrstft: float | None
    level_db: float | None
    peak_db: float | None
    correlation: float | None
    nam_esr: float | None
    nam_human_weighted_esr: float | None
    nam_mrstft: float | None
    nam_level_db: float | None
    nam_peak_db: float | None
    nam_correlation: float | None


class RunCaseAudioResponse(ApiModel):
    dry: str
    reference: str
    candidate: str | None


class RunCaseAnalysisPoint(ApiModel):
    time_seconds: float
    esr: float
    reference_level_db: float
    candidate_level_db: float
    level_delta_db: float
    reference_peak_db: float
    candidate_peak_db: float
    peak_delta_db: float
    correlation: float


class RunCaseAnalysisResponse(ApiModel):
    version: str = "top-arena-case-analysis-v1"
    window_seconds: float = 0.1
    hop_seconds: float = 0.1
    points: list[RunCaseAnalysisPoint] = Field(default_factory=list)


class RunCaseDetailResponse(ApiModel):
    run: RunResponse
    case_id: str
    index: int
    total: int
    chunk_index: int
    position_index: int
    status: str
    positions: list[list[float]]
    control_names: list[str]
    duration_seconds: float
    sample_rate: int
    metrics: RunCaseMetricsResponse
    analysis: RunCaseAnalysisResponse
    audio: RunCaseAudioResponse
    url: str
    previous_url: str | None
    next_url: str | None
