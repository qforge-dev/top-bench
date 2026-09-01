from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    amp_control_count: int | None = Field(default=None, gt=0)
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


class UpdateRunMetadataRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    creator: str | None = Field(default=None, min_length=1, max_length=255)
    amp_control_count: int | None = Field(default=None, gt=0)
    unique_positions_used: int | None = Field(default=None, ge=0)
    audio_duration_sum: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    turns: int | None = Field(default=None, ge=0)
    training_time: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    description: str | None = None
    parameter_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_updates(self) -> Self:
        if not self.model_fields_set:
            msg = "at least one metadata field must be supplied"
            raise ValueError(msg)
        null_fields = {
            field
            for field in self.model_fields_set
            if field != "amp_control_count" and getattr(self, field) is None
        }
        if null_fields:
            msg = f"metadata fields cannot be null: {', '.join(sorted(null_fields))}"
            raise ValueError(msg)
        return self


class EventRequest(ApiModel):
    kind: str
    case_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBatchRequest(ApiModel):
    events: list[EventRequest] = Field(min_length=1, max_length=100)


class EventBatchResponse(ApiModel):
    accepted: int


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
    amp_control_count: int
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


class LeaderboardChartRunResponse(ApiModel):
    id: str
    name: str
    amp_id: str
    amp_name: str
    amp_control_count: int
    unique_positions_used: int
    esr: float | None


class LeaderboardResponse(ApiModel):
    runs: list[RunResponse]
    chart_runs: list[LeaderboardChartRunResponse] = Field(default_factory=list)
    amps: list[AmpResponse]
    creators: list[str]
    run_ranks: dict[str, int] = Field(default_factory=dict)
    page: int = 1
    page_size: int = 25
    total_runs: int = 0
    total_pages: int = 1


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
    nam: str | None


class WaveformSeriesResponse(ApiModel):
    key: Literal["reference", "nam", "model"]
    label: str
    values: list[float]


class WaveformResponse(ApiModel):
    duration_seconds: float
    series: list[WaveformSeriesResponse]


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
    nam_points: list[RunCaseAnalysisPoint] = Field(default_factory=list)


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
    waveform_url: str
    url: str
    previous_url: str | None
    next_url: str | None
