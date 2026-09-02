from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer, joinedload, selectinload
from starlette.middleware.base import RequestResponseEndpoint

from .config import Settings
from .database import Database
from .models import Amp, BenchmarkCase, BenchmarkRun, RunCase, RunEvent, now_utc
from .schemas import (
    AmpResponse,
    CaseResultResponse,
    CreateRunRequest,
    CreateRunResponse,
    EventBatchRequest,
    EventBatchResponse,
    EventRequest,
    EventResponse,
    EventsResponse,
    LeaderboardChartRunResponse,
    LeaderboardResponse,
    ManifestCase,
    ManifestResponse,
    NamA2CalibrationAssetsResponse,
    RunCaseAnalysisResponse,
    RunCaseAudioResponse,
    RunCaseDetailResponse,
    RunCaseIndexItem,
    RunCaseIndexResponse,
    RunCaseMetricsResponse,
    RunResponse,
    UpdateRunMetadataRequest,
    WaveformResponse,
    WaveformSeriesResponse,
)
from .scoring import ScoringService, baseline_cache_signature
from .storage import ObjectStorage, create_storage
from .waveform import waveform_envelope

LOGGER = logging.getLogger(__name__)
SIMPLE_AMP_IDS = frozenset(
    {
        "blackface63-simple",
        "blackface63-simple-quiet",
        "genome-artisan-100-ch1-simple",
        "genome-brit1959-ch1-simple",
        "genome-calibro-normal-simple",
        "genome-eldorado-syn-simple",
        "genome-flatback-dual-ch3-simple",
        "genome-fried-r50-dirty-simple",
        "genome-hektor-lead-simple",
        "genome-lyndon-lion-clean-simple",
        "genome-petaluma-rockrider-clean-simple",
        "genome-revelation-120-ch4-simple",
    }
)
LEADERBOARD_PAGE_SIZE = 25
NAM_A2_CALIBRATION_VERSION = "top-arena-native-nam-a2-speed-v1"
NAM_A2_CALIBRATION_AUDIO_SECONDS = 2.0
NAM_A2_CALIBRATION_MODEL_KEY = (
    "reference-corpus/v1/nam-a2-full/v1/models/blackface63-simple/position-01/training/model.nam"
)
NAM_A2_CALIBRATION_MODEL_SHA256 = "385d082afb6519b918bf965c70801e9f4a1598701b929ef42e8ac8cc263a7ebf"
NAM_A2_CALIBRATION_RELEASE_URL = (
    "https://github.com/qforge-dev/top-bench/releases/download/native-nam-calibration-v1"
)
NAM_A2_CALIBRATION_RUNNERS = {
    "darwin-arm64": (
        "benchmodel-darwin-arm64",
        "d2bc233a05af90ac4a00b90ea543987b53ad057f97e90055cfae635f8f0582a2",
    ),
    "darwin-x86_64": (
        "benchmodel-darwin-x86_64",
        "9dd3e76b3a1a0b0bc16368ea8526f75d68f72313c6e471d6ecea9f7888348c76",
    ),
    "linux-arm64": (
        "benchmodel-linux-arm64",
        "380051e00ed5abae1a76daa6f255376a27dbc9a276be7b4ac09a7eaad15507a6",
    ),
    "linux-x86_64": (
        "benchmodel-linux-x86_64",
        "695df5423cdc4c33d2a1d3ae61be2e0a5b84a6569dbbf7c6a35500b3d658b741",
    ),
    "windows-x86_64": (
        "benchmodel-windows-x86_64.exe",
        "79c031fd5cddc70fe2fe1af72e6ebf1ebef68513749fbeceb78f4e1424c0d25c",
    ),
}


@dataclass(frozen=True, slots=True)
class Services:
    settings: Settings
    database: Database
    storage: ObjectStorage
    scoring: ScoringService


@dataclass(frozen=True, slots=True)
class CaseLocation:
    case_id: str
    status: str
    chunk_index: int
    position_index: int


def _case_response(run_case: RunCase) -> CaseResultResponse:
    return CaseResultResponse(
        case_id=run_case.benchmark_case_id,
        status=run_case.status,
        realtime_x=run_case.realtime_x,
        esr=run_case.esr,
        human_weighted_esr=run_case.human_weighted_esr,
        mrstft=run_case.mrstft,
        level_db=run_case.level_db,
        peak_db=run_case.peak_db,
        correlation=run_case.correlation,
        nam_esr=run_case.nam_esr,
        nam_human_weighted_esr=run_case.nam_human_weighted_esr,
        nam_mrstft=run_case.nam_mrstft,
        nam_level_db=run_case.nam_level_db,
        nam_peak_db=run_case.nam_peak_db,
        nam_correlation=run_case.nam_correlation,
    )


def _case_page_url(run_id: str, case_id: str) -> str:
    return f"/runs/{run_id}/cases/{case_id}"


def _case_audio_url(run_id: str, case_id: str, kind: str) -> str:
    return f"/api/v1/runs/{run_id}/cases/{case_id}/audio/{kind}"


def _audio_media_type(object_key: str) -> str:
    return "audio/flac" if Path(object_key).suffix.lower() == ".flac" else "audio/wav"


def _parse_byte_range(range_header: str, total: int) -> tuple[int, int]:
    if not range_header.startswith("bytes=") or "," in range_header or total == 0:
        raise ValueError
    bounds = range_header.removeprefix("bytes=").strip().split("-", maxsplit=1)
    if len(bounds) != 2:
        raise ValueError
    start_text, end_text = bounds
    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else total - 1
    else:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError
        start = max(total - suffix_length, 0)
        end = total - 1
    if start < 0 or start >= total or end < start:
        raise ValueError
    return start, min(end, total - 1)


def _audio_response(value: bytes, media_type: str, range_header: str | None) -> Response:
    total = len(value)
    headers = {"Accept-Ranges": "bytes"}
    if range_header is None:
        return Response(content=value, media_type=media_type, headers=headers)
    try:
        start, end = _parse_byte_range(range_header, total)
    except ValueError:
        return Response(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={**headers, "Content-Range": f"bytes */{total}"},
        )
    content = value[start : end + 1]
    return Response(
        content=content,
        media_type=media_type,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(len(content)),
        },
    )


def _run_response(
    run: BenchmarkRun,
    *,
    include_cases: bool = True,
    metrics: dict[str, Any] | None = None,
) -> RunResponse:
    return RunResponse(
        id=run.id,
        name=run.name,
        creator=run.creator,
        amp_id=run.amp_id,
        amp_name=run.amp.name,
        amp_type=run.amp.amp_type,
        amp_control_count=run.amp_control_count_override or len(run.amp.control_names),
        unique_positions_used=run.unique_positions_used,
        audio_duration_sum=run.audio_duration_sum,
        turns=run.turns,
        training_time=run.training_time,
        description=run.description,
        parameter_count=run.parameter_count,
        nam_a2_realtime_x=run.nam_a2_realtime_x,
        speed_calibration=run.speed_calibration,
        status=run.status,
        total_cases=run.total_cases,
        completed_cases=run.completed_cases,
        metrics=run.metrics if metrics is None else metrics,
        created_at=run.created_at,
        completed_at=run.completed_at,
        cases=[_case_response(run_case) for run_case in run.cases] if include_cases else [],
    )


async def _load_run(services: Services, run_id: str) -> BenchmarkRun | None:
    async with services.database.session() as session:
        return cast(
            "BenchmarkRun | None",
            await session.scalar(
                select(BenchmarkRun)
                .options(
                    joinedload(BenchmarkRun.amp),
                    selectinload(BenchmarkRun.cases).defer(RunCase.analysis),
                )
                .where(BenchmarkRun.id == run_id)
            ),
        )


async def _update_run_metadata(
    services: Services,
    run_id: str,
    request: UpdateRunMetadataRequest,
) -> RunResponse:
    try:
        async with services.database.session() as session:
            run = await session.scalar(
                select(BenchmarkRun)
                .options(joinedload(BenchmarkRun.amp))
                .where(BenchmarkRun.id == run_id)
            )
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")

            updates = request.model_dump(exclude_unset=True)
            changes: dict[str, dict[str, Any]] = {}
            if "amp_control_count" in updates:
                old_count = run.amp_control_count_override or len(run.amp.control_names)
                new_count = updates.pop("amp_control_count")
                run.amp_control_count_override = cast("int | None", new_count)
                effective_count = new_count or len(run.amp.control_names)
                if old_count != effective_count:
                    changes["amp_control_count"] = {
                        "from": old_count,
                        "to": effective_count,
                    }

            for field, value in updates.items():
                previous = getattr(run, field)
                if previous != value:
                    setattr(run, field, value)
                    changes[field] = {"from": previous, "to": value}

            if changes:
                run.updated_at = now_utc()
                session.add(
                    RunEvent(
                        run_id=run_id,
                        kind="run.metadata_updated",
                        payload={"changes": changes},
                    )
                )
            await session.flush()
            response = _run_response(run, include_cases=False)
    except IntegrityError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "run name already exists") from error
    return response


async def _load_case_locations(
    services: Services, run_id: str
) -> tuple[BenchmarkRun | None, list[CaseLocation]]:
    async with services.database.session() as session:
        run = cast(
            "BenchmarkRun | None",
            await session.scalar(
                select(BenchmarkRun)
                .options(joinedload(BenchmarkRun.amp))
                .where(BenchmarkRun.id == run_id)
            ),
        )
        if run is None:
            return None, []
        rows = (
            await session.execute(
                select(
                    RunCase.benchmark_case_id,
                    RunCase.status,
                    BenchmarkCase.chunk_index,
                    BenchmarkCase.position_index,
                )
                .join(BenchmarkCase, BenchmarkCase.id == RunCase.benchmark_case_id)
                .where(RunCase.run_id == run_id)
                .order_by(BenchmarkCase.chunk_index, BenchmarkCase.position_index)
            )
        ).all()
    return run, [
        CaseLocation(
            case_id=case_id,
            status=case_status,
            chunk_index=chunk_index,
            position_index=position_index,
        )
        for case_id, case_status, chunk_index, position_index in rows
    ]


async def _leaderboard_runs(
    services: Services,
    *,
    amp_scope: Literal["normal", "simple", "all"] = "all",
    amp_id: str | None = None,
    creator: str | None = None,
    sort_key: str = "esr",
    direction: str = "asc",
) -> list[RunResponse]:
    statement = (
        select(BenchmarkRun)
        .join(BenchmarkRun.amp)
        .options(
            joinedload(BenchmarkRun.amp),
            defer(BenchmarkRun.metrics, raiseload=True),
        )
    )
    if amp_scope == "simple":
        statement = statement.where(BenchmarkRun.amp_id.in_(SIMPLE_AMP_IDS))
    elif amp_scope == "normal":
        statement = statement.where(BenchmarkRun.amp_id.not_in(SIMPLE_AMP_IDS))
    if amp_id:
        statement = statement.where(BenchmarkRun.amp_id == amp_id)
    if creator:
        statement = statement.where(BenchmarkRun.creator == creator)
    async with services.database.session() as session:
        runs = (await session.scalars(statement)).unique().all()
    responses = [
        _run_response(
            run,
            include_cases=False,
            metrics=run.leaderboard_metrics,
        )
        for run in runs
    ]

    def metric_value(run: RunResponse, metric: str, summary: str = "mean") -> float:
        value = run.metrics.get(metric, {}).get(summary)
        return float(value) if value is not None else float("inf")

    keys: dict[str, Any] = {
        "name": lambda run: run.name.casefold(),
        "creator": lambda run: run.creator.casefold(),
        "positions": lambda run: run.unique_positions_used,
        "esr": lambda run: metric_value(run, "esr"),
        "mrstft": lambda run: metric_value(run, "mrstft"),
        "speed": lambda run: metric_value(run, "nam_a2_speed_ratio"),
        "created": lambda run: run.created_at,
    }
    key = keys.get(sort_key, keys["esr"])
    responses.sort(key=key, reverse=direction == "desc")
    return responses


def _metric_mean(run: RunResponse, metric: str) -> float | None:
    value = run.metrics.get(metric, {}).get("mean")
    return float(value) if value is not None else None


def _sort_leaderboard_runs(
    runs: list[RunResponse],
    *,
    sort_key: str,
    direction: str,
) -> list[RunResponse]:
    def status_progress(run: RunResponse) -> float:
        return run.completed_cases / run.total_cases if run.total_cases else 0.0

    keys: dict[str, Any] = {
        "rank": lambda run: _metric_mean(run, "esr"),
        "name": lambda run: run.name.casefold(),
        "amp": lambda run: (run.amp_name.casefold(), run.amp_id.casefold()),
        "status": status_progress,
        "positions": lambda run: run.unique_positions_used,
        "ampParameters": lambda run: run.amp_control_count,
        "positionsPerControl": lambda run: (
            run.unique_positions_used / run.amp_control_count if run.amp_control_count else None
        ),
        "started": lambda run: run.created_at,
        "created": lambda run: run.created_at,
        "realtime": lambda run: _metric_mean(run, "nam_a2_speed_ratio"),
        "speed": lambda run: _metric_mean(run, "nam_a2_speed_ratio"),
        "esr": lambda run: _metric_mean(run, "esr"),
        "humanWeightedEsr": lambda run: _metric_mean(run, "human_weighted_esr"),
        "mrstft": lambda run: _metric_mean(run, "mrstft"),
    }
    key = keys.get(sort_key, keys["esr"])
    named = sorted(runs, key=lambda run: (run.name.casefold(), run.id))
    available = [run for run in named if key(run) is not None]
    missing = [run for run in named if key(run) is None]
    available.sort(key=key, reverse=direction == "desc")
    return [*available, *missing]


async def _leaderboard_page(
    services: Services,
    *,
    amp_scope: Literal["normal", "simple", "all"] = "normal",
    amp_id: str | None = None,
    creator: str | None = None,
    search: str | None = None,
    sort_key: str = "esr",
    direction: str = "asc",
    page: int = 1,
    page_size: int | None = LEADERBOARD_PAGE_SIZE,
) -> LeaderboardResponse:
    scope_runs = await _leaderboard_runs(services, amp_scope=amp_scope)
    ranked = [run for run in scope_runs if _metric_mean(run, "esr") is not None]
    ranked.sort(key=lambda run: (_metric_mean(run, "esr"), run.name.casefold(), run.id))
    ranks = {run.id: index for index, run in enumerate(ranked, start=1)}
    query = (search or "").strip().casefold()
    filtered = [
        run
        for run in scope_runs
        if (not amp_id or run.amp_id == amp_id)
        and (not creator or run.creator == creator)
        and (not query or query in f"{run.name} {run.description} {run.creator}".casefold())
    ]
    filtered = _sort_leaderboard_runs(
        filtered,
        sort_key=sort_key,
        direction=direction,
    )
    total_runs = len(filtered)
    selected_page_size = page_size or max(1, total_runs)
    total_pages = max(1, ceil(total_runs / selected_page_size))
    selected_page = min(page, total_pages)
    start = (selected_page - 1) * selected_page_size
    page_runs = filtered[start : start + selected_page_size]
    return LeaderboardResponse(
        runs=page_runs,
        chart_runs=(
            [
                LeaderboardChartRunResponse(
                    id=run.id,
                    name=run.name,
                    amp_id=run.amp_id,
                    amp_name=run.amp_name,
                    amp_control_count=run.amp_control_count,
                    unique_positions_used=run.unique_positions_used,
                    esr=_metric_mean(run, "esr"),
                )
                for run in filtered
            ]
            if page_size is not None
            else []
        ),
        amps=await _leaderboard_amps(services),
        creators=sorted({run.creator for run in scope_runs}),
        run_ranks={run.id: ranks[run.id] for run in page_runs if run.id in ranks},
        page=selected_page,
        page_size=selected_page_size,
        total_runs=total_runs,
        total_pages=total_pages,
    )


async def _leaderboard_amps(services: Services) -> list[AmpResponse]:
    async with services.database.session() as session:
        amps = (await session.scalars(select(Amp).order_by(Amp.name, Amp.id))).all()
    return [AmpResponse.model_validate(amp) for amp in amps]


async def _leaderboard_etag(services: Services, query: str) -> str:
    async with services.database.session() as session:
        run_revision = (
            await session.execute(
                select(
                    func.count(BenchmarkRun.id),
                    func.max(BenchmarkRun.updated_at),
                )
            )
        ).one()
        amp_revision = (
            await session.execute(
                select(
                    Amp.id,
                    Amp.name,
                    Amp.amp_type,
                    Amp.control_names,
                ).order_by(Amp.id)
            )
        ).all()
    revision = repr((query, *run_revision, amp_revision)).encode()
    return f'"{hashlib.sha256(revision).hexdigest()}"'


def _etag_matches(header: str, etag: str) -> bool:
    expected = etag.strip('"')
    for raw_value in header.split(","):
        candidate = raw_value.strip()
        if candidate == "*":
            return True
        candidate = candidate.removeprefix("W/")
        candidate = candidate.strip('"')
        for encoding in ("br", "gzip", "zstd"):
            if candidate.endswith(f"-{encoding}"):
                candidate = candidate[: -(len(encoding) + 1)]
                break
        if candidate == expected:
            return True
    return False


def create_app(settings: Settings | None = None) -> FastAPI:
    selected_settings = settings or Settings()
    database = Database(selected_settings.database_url)
    storage = create_storage(selected_settings)
    services = Services(
        settings=selected_settings,
        database=database,
        storage=storage,
        scoring=ScoringService(database, storage, selected_settings),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.services = services
        await database.initialize()
        await services.scoring.start()
        try:
            yield
        finally:
            await services.scoring.stop()
            await database.close()

    app = FastAPI(
        title="Top Arena",
        version="0.1.0",
        summary="Open audio-model benchmark and leaderboard",
        lifespan=lifespan,
    )
    upload_slots = asyncio.Semaphore(max(1, selected_settings.upload_concurrency_limit))

    @app.middleware("http")
    async def log_slow_requests(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started_at) * 1_000
            LOGGER.exception(
                "request.failed method=%s path=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                elapsed_ms,
            )
            raise
        elapsed_seconds = time.perf_counter() - started_at
        if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            LOGGER.error(
                "request.server_error method=%s path=%s status=%d duration_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_seconds * 1_000,
            )
        elif elapsed_seconds >= selected_settings.slow_request_log_seconds:
            LOGGER.warning(
                "request.slow method=%s path=%s status=%d duration_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_seconds * 1_000,
            )
        return response

    package_root = Path(__file__).parent
    static_root = package_root / "static"
    template_root = package_root / "templates"
    static_root.mkdir(exist_ok=True)
    template_root.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_root), name="static")
    templates = Jinja2Templates(directory=template_root)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str | int]:
        return {
            "status": "ok",
            "score_queue_depth": services.scoring.queue_depth,
            "score_workers": services.scoring.worker_count,
        }

    @app.get("/api/v1/amps", response_model=list[AmpResponse], tags=["benchmark"])
    async def amps() -> list[AmpResponse]:
        async with database.session() as session:
            values = (await session.scalars(select(Amp).order_by(Amp.name))).all()
        return [AmpResponse.model_validate(value) for value in values]

    @app.get(
        "/api/v1/amps/{amp_id}/manifest",
        response_model=ManifestResponse,
        tags=["benchmark"],
    )
    async def manifest(amp_id: str) -> ManifestResponse:
        async with database.session() as session:
            amp = await session.get(Amp, amp_id)
            cases = (
                await session.scalars(
                    select(BenchmarkCase)
                    .where(BenchmarkCase.amp_id == amp_id)
                    .order_by(BenchmarkCase.chunk_index, BenchmarkCase.position_index)
                )
            ).all()
        if amp is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "amp not found")
        return ManifestResponse(
            amp=AmpResponse.model_validate(amp),
            cases=[
                ManifestCase(
                    id=case.id,
                    positions=case.position_matrix,
                    dry_key=case.dry_key,
                    dry_sha256=case.dry_sha256,
                    download_url=f"/api/v1/cases/{case.id}/dry",
                    duration_seconds=case.duration_seconds,
                )
                for case in cases
            ],
        )

    @app.get("/api/v1/cases/{case_id}/dry", tags=["benchmark"])
    async def download_dry(case_id: str) -> Response:
        async with database.session() as session:
            benchmark_case = await session.get(BenchmarkCase, case_id)
        if benchmark_case is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "benchmark case not found")
        value = await storage.get(benchmark_case.dry_key)
        return Response(
            content=value,
            media_type="audio/wav",
            headers={"ETag": benchmark_case.dry_sha256},
        )

    @app.get(
        "/api/v1/calibration/nam-a2-full",
        response_model=NamA2CalibrationAssetsResponse,
        tags=["benchmark"],
    )
    async def nam_a2_calibration_assets(
        platform: Annotated[str, Query()],
    ) -> NamA2CalibrationAssetsResponse:
        runner = NAM_A2_CALIBRATION_RUNNERS.get(platform)
        if runner is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"native NAM-A2 calibration is not available for {platform}",
            )
        filename, sha256 = runner
        return NamA2CalibrationAssetsResponse(
            version=NAM_A2_CALIBRATION_VERSION,
            platform=platform,
            runner_url=f"{NAM_A2_CALIBRATION_RELEASE_URL}/{filename}",
            runner_sha256=sha256,
            model_url=(
                f"{selected_settings.public_base_url.rstrip('/')}"
                "/api/v1/calibration/nam-a2-full/model"
            ),
            model_sha256=NAM_A2_CALIBRATION_MODEL_SHA256,
            audio_seconds=NAM_A2_CALIBRATION_AUDIO_SECONDS,
        )

    @app.get("/api/v1/calibration/nam-a2-full/model", tags=["benchmark"])
    async def download_nam_a2_calibration_model() -> Response:
        value = await storage.get(NAM_A2_CALIBRATION_MODEL_KEY)
        return Response(
            content=value,
            media_type="application/json",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": NAM_A2_CALIBRATION_MODEL_SHA256,
            },
        )

    @app.post(
        "/api/v1/runs",
        response_model=CreateRunResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["runs"],
    )
    async def create_run(request: CreateRunRequest) -> CreateRunResponse:
        started_at = time.perf_counter()
        try:
            async with database.session() as session:
                amp = await session.get(Amp, request.amp_id)
                if amp is None:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "amp not found")
                benchmark_cases = (
                    await session.scalars(
                        select(BenchmarkCase).where(BenchmarkCase.amp_id == request.amp_id)
                    )
                ).all()
                run_values = request.model_dump(exclude={"amp_control_count"})
                run = BenchmarkRun(
                    **run_values,
                    amp_control_count_override=request.amp_control_count,
                    total_cases=len(benchmark_cases),
                    completed_cases=0,
                    status="running",
                    metrics={},
                )
                session.add(run)
                await session.flush()
                session.add_all(
                    [
                        RunCase(run_id=run.id, benchmark_case_id=case.id, status="pending")
                        for case in benchmark_cases
                    ]
                )
                session.add(
                    RunEvent(
                        run_id=run.id,
                        kind="run.created",
                        payload={"amp_id": request.amp_id, "total_cases": len(benchmark_cases)},
                    )
                )
                response = CreateRunResponse(
                    id=run.id,
                    status=run.status,
                    total_cases=run.total_cases,
                )
        except IntegrityError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "run name already exists") from error
        LOGGER.info(
            "run.created run_id=%s amp_id=%s cases=%d duration_ms=%.1f",
            response.id,
            request.amp_id,
            response.total_cases,
            (time.perf_counter() - started_at) * 1_000,
        )
        return response

    @app.post(
        "/api/v1/runs/{run_id}/events",
        response_model=EventResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["events"],
    )
    async def add_event(run_id: str, request: EventRequest) -> EventResponse:
        async with database.session() as session:
            run = await session.get(BenchmarkRun, run_id)
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
            if request.kind == "run.client_failed" and run.status not in {"completed", "failed"}:
                run.status = "failed"
            event = RunEvent(
                run_id=run_id,
                benchmark_case_id=request.case_id,
                kind=request.kind,
                payload=request.payload,
            )
            session.add(event)
            await session.flush()
            if request.kind == "run.client_failed":
                LOGGER.error(
                    "run.client_failed run_id=%s error=%s details=%r",
                    run_id,
                    request.payload.get("error"),
                    request.payload.get("details"),
                )
            return EventResponse(
                id=event.id,
                kind=event.kind,
                case_id=event.benchmark_case_id,
                payload=event.payload,
                occurred_at=event.occurred_at,
            )

    @app.post(
        "/api/v1/runs/{run_id}/events/batch",
        response_model=EventBatchResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["events"],
    )
    async def add_event_batch(
        run_id: str,
        request: EventBatchRequest,
    ) -> EventBatchResponse:
        client_failure = next(
            (event for event in request.events if event.kind == "run.client_failed"),
            None,
        )
        async with database.session() as session:
            run = await session.get(BenchmarkRun, run_id)
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
            if client_failure is not None and run.status not in {
                "completed",
                "failed",
            }:
                run.status = "failed"
            session.add_all(
                [
                    RunEvent(
                        run_id=run_id,
                        benchmark_case_id=event.case_id,
                        kind=event.kind,
                        payload=event.payload,
                    )
                    for event in request.events
                ]
            )
        if client_failure is not None:
            LOGGER.error(
                "run.client_failed run_id=%s error=%s details=%r",
                run_id,
                client_failure.payload.get("error"),
                client_failure.payload.get("details"),
            )
        return EventBatchResponse(accepted=len(request.events))

    @app.put(
        "/api/v1/runs/{run_id}/cases/{case_id}/audio",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["runs"],
    )
    async def upload_audio(
        run_id: str,
        case_id: str,
        request: Request,
        realtime_x: Annotated[float, Query()],
    ) -> dict[str, str]:
        started_at = time.perf_counter()
        request_media_type = request.headers.get("content-type", "").partition(";")[0].lower()
        is_flac = request_media_type in {"application/flac", "audio/flac", "audio/x-flac"}
        extension = ".flac" if is_flac else ".wav"
        media_type = "audio/flac" if is_flac else "audio/wav"
        candidate_key = f"runs/{run_id}/candidates/{case_id}{extension}"
        async with database.session() as session:
            row = (
                await session.execute(
                    select(RunCase, BenchmarkRun.client_finished, BenchmarkRun.status)
                    .join(BenchmarkRun, BenchmarkRun.id == RunCase.run_id)
                    .where(
                        RunCase.run_id == run_id,
                        RunCase.benchmark_case_id == case_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run case not found")
            run_case, client_finished, run_status = row
            if (
                run_case.candidate_wet_key == candidate_key
                and run_case.status
                in {
                    "uploaded",
                    "scoring",
                    "completed",
                }
                and run_case.realtime_x == realtime_x
            ):
                return {"status": "accepted"}
            if client_finished or run_status in {"completed", "failed"}:
                raise HTTPException(status.HTTP_409_CONFLICT, "run no longer accepts uploads")
            if run_case.status in {"scoring", "completed", "failed"}:
                raise HTTPException(status.HTTP_409_CONFLICT, "run case no longer accepts uploads")
            run_case_id = run_case.id
        validated_at = time.perf_counter()
        slot_requested_at = validated_at
        async with upload_slots:
            slot_acquired_at = time.perf_counter()
            value = await request.body()
            body_read_at = time.perf_counter()
            await storage.put(candidate_key, value, content_type=media_type)
            stored_at = time.perf_counter()
        async with database.session() as session:
            run_case = await session.get(RunCase, run_case_id)
            if run_case is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run case not found")
            run_case.candidate_wet_key = candidate_key
            run_case.realtime_x = realtime_x
            run_case.status = "uploaded"
            run_case.uploaded_at = now_utc()
            session.add(
                RunEvent(
                    run_id=run_id,
                    benchmark_case_id=case_id,
                    kind="upload.received",
                    payload={
                        "realtime_x": realtime_x,
                        "bytes": len(value),
                        "media_type": media_type,
                    },
                )
            )
        await services.scoring.enqueue(run_case_id, run_id=run_id)
        finished_at = time.perf_counter()
        LOGGER.info(
            "upload.accepted run_id=%s case_id=%s bytes=%d validate_ms=%.1f "
            "slot_wait_ms=%.1f read_ms=%.1f storage_ms=%.1f db_queue_ms=%.1f "
            "total_ms=%.1f score_queue_depth=%d",
            run_id,
            case_id,
            len(value),
            (validated_at - started_at) * 1_000,
            (slot_acquired_at - slot_requested_at) * 1_000,
            (body_read_at - slot_acquired_at) * 1_000,
            (stored_at - body_read_at) * 1_000,
            (finished_at - stored_at) * 1_000,
            (finished_at - started_at) * 1_000,
            services.scoring.queue_depth,
        )
        return {"status": "accepted"}

    @app.post("/api/v1/runs/{run_id}/finish", tags=["runs"])
    async def finish_run(run_id: str) -> RunResponse:
        async with database.session() as session:
            run = await session.get(BenchmarkRun, run_id)
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
            run.client_finished = True
            if run.status not in {"completed", "failed"}:
                run.status = "finalizing"
            session.add(RunEvent(run_id=run_id, kind="run.client_finished", payload={}))
        await services.scoring.finalize_if_ready(run_id)
        value = await _load_run(services, run_id)
        if value is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        return _run_response(value)

    @app.get("/api/v1/runs/{run_id}", response_model=RunResponse, tags=["runs"])
    async def get_run(run_id: str) -> RunResponse:
        run = await _load_run(services, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        return _run_response(run)

    @app.patch(
        "/api/v1/runs/{run_id}",
        response_model=RunResponse,
        tags=["runs"],
    )
    async def update_run_metadata(
        run_id: str,
        request: UpdateRunMetadataRequest,
    ) -> RunResponse:
        return await _update_run_metadata(services, run_id, request)

    @app.delete(
        "/api/v1/runs/{run_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["runs"],
    )
    async def delete_run(run_id: str) -> Response:
        async with database.session() as session:
            run = await session.scalar(
                select(BenchmarkRun)
                .options(selectinload(BenchmarkRun.cases), selectinload(BenchmarkRun.events))
                .where(BenchmarkRun.id == run_id)
            )
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
            if run.status not in {"completed", "failed"}:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "only completed or failed runs can be deleted",
                )
            candidate_keys = {
                run_case.candidate_wet_key
                for run_case in run.cases
                if run_case.candidate_wet_key is not None
            }
            await session.delete(run)

        cleanup_results = await asyncio.gather(
            *(storage.delete(key) for key in candidate_keys),
            return_exceptions=True,
        )
        for key, result in zip(candidate_keys, cleanup_results, strict=True):
            if isinstance(result, BaseException):
                LOGGER.warning("could not delete candidate object %s: %s", key, result)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/api/v1/runs/{run_id}/case-index",
        response_model=RunCaseIndexResponse,
        tags=["runs"],
    )
    async def case_index(run_id: str) -> RunCaseIndexResponse:
        run, locations = await _load_case_locations(services, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        return RunCaseIndexResponse(
            run=_run_response(run, include_cases=False),
            cases=[
                RunCaseIndexItem(
                    case_id=location.case_id,
                    index=index,
                    chunk_index=location.chunk_index,
                    position_index=location.position_index,
                    status=location.status,
                    url=_case_page_url(run_id, location.case_id),
                )
                for index, location in enumerate(locations, start=1)
            ],
        )

    @app.get(
        "/api/v1/runs/{run_id}/cases/{case_id}/detail",
        response_model=RunCaseDetailResponse,
        tags=["runs"],
    )
    async def case_detail(run_id: str, case_id: str) -> RunCaseDetailResponse:
        run, locations = await _load_case_locations(services, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        zero_based_index = next(
            (index for index, location in enumerate(locations) if location.case_id == case_id),
            None,
        )
        if zero_based_index is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run case not found")

        async with database.session() as session:
            row = (
                await session.execute(
                    select(RunCase, BenchmarkCase)
                    .join(BenchmarkCase, BenchmarkCase.id == RunCase.benchmark_case_id)
                    .where(
                        RunCase.run_id == run_id,
                        RunCase.benchmark_case_id == case_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run case not found")
        run_case, benchmark_case = row
        previous_location = locations[zero_based_index - 1] if zero_based_index > 0 else None
        next_location = (
            locations[zero_based_index + 1] if zero_based_index + 1 < len(locations) else None
        )
        candidate_url = (
            _case_audio_url(run_id, case_id, "candidate")
            if run_case.candidate_wet_key is not None
            else None
        )
        nam_url = (
            _case_audio_url(run_id, case_id, "nam")
            if benchmark_case.nam_reference_wet_key is not None
            else None
        )
        analysis = dict(run_case.analysis or {})
        cached_nam_analysis = benchmark_case.nam_metrics_cache.get("analysis")
        if (
            "nam_points" not in analysis
            and benchmark_case.nam_cache_signature == baseline_cache_signature(benchmark_case)
            and isinstance(cached_nam_analysis, dict)
            and isinstance((nam_points := cached_nam_analysis.get("points")), list)
        ):
            analysis["nam_points"] = nam_points
        return RunCaseDetailResponse(
            run=_run_response(run, include_cases=False),
            case_id=case_id,
            index=zero_based_index + 1,
            total=len(locations),
            chunk_index=benchmark_case.chunk_index,
            position_index=benchmark_case.position_index,
            status=run_case.status,
            positions=benchmark_case.position_matrix,
            control_names=run.amp.control_names,
            duration_seconds=benchmark_case.duration_seconds,
            sample_rate=benchmark_case.sample_rate,
            metrics=RunCaseMetricsResponse(
                realtime_x=run_case.realtime_x,
                esr=run_case.esr,
                human_weighted_esr=run_case.human_weighted_esr,
                mrstft=run_case.mrstft,
                level_db=run_case.level_db,
                peak_db=run_case.peak_db,
                correlation=run_case.correlation,
                nam_esr=run_case.nam_esr,
                nam_human_weighted_esr=run_case.nam_human_weighted_esr,
                nam_mrstft=run_case.nam_mrstft,
                nam_level_db=run_case.nam_level_db,
                nam_peak_db=run_case.nam_peak_db,
                nam_correlation=run_case.nam_correlation,
            ),
            analysis=RunCaseAnalysisResponse.model_validate(analysis),
            audio=RunCaseAudioResponse(
                dry=_case_audio_url(run_id, case_id, "dry"),
                reference=_case_audio_url(run_id, case_id, "reference"),
                candidate=candidate_url,
                nam=nam_url,
            ),
            waveform_url=f"/api/v1/runs/{run_id}/cases/{case_id}/waveform",
            url=_case_page_url(run_id, case_id),
            previous_url=(
                _case_page_url(run_id, previous_location.case_id)
                if previous_location is not None
                else None
            ),
            next_url=(
                _case_page_url(run_id, next_location.case_id) if next_location is not None else None
            ),
        )

    @app.get(
        "/api/v1/runs/{run_id}/cases/{case_id}/waveform",
        response_model=WaveformResponse,
        tags=["runs"],
    )
    async def case_waveform(run_id: str, case_id: str) -> WaveformResponse:
        async with database.session() as session:
            row = (
                await session.execute(
                    select(
                        BenchmarkCase.reference_wet_key,
                        BenchmarkCase.nam_reference_wet_key,
                        RunCase.candidate_wet_key,
                        BenchmarkCase.duration_seconds,
                    )
                    .select_from(RunCase)
                    .join(BenchmarkCase, BenchmarkCase.id == RunCase.benchmark_case_id)
                    .where(
                        RunCase.run_id == run_id,
                        RunCase.benchmark_case_id == case_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run case not found")
        sources: list[tuple[Literal["reference", "nam", "model"], str, str | None]] = [
            ("reference", "BIAS X wet", row.reference_wet_key),
            ("nam", "NAM-A2-FULL", row.nam_reference_wet_key),
            ("model", "Model", row.candidate_wet_key),
        ]
        available = [(key, label, object_key) for key, label, object_key in sources if object_key]
        encoded = await asyncio.gather(*(storage.get(object_key) for _, _, object_key in available))
        envelopes = await asyncio.gather(
            *(asyncio.to_thread(waveform_envelope, value) for value in encoded)
        )
        return WaveformResponse(
            duration_seconds=row.duration_seconds,
            series=[
                WaveformSeriesResponse(key=key, label=label, values=values)
                for (key, label, _object_key), values in zip(available, envelopes, strict=True)
            ],
        )

    @app.get(
        "/api/v1/runs/{run_id}/cases/{case_id}/audio/{kind}",
        tags=["runs"],
        response_class=Response,
    )
    async def case_audio(
        request: Request,
        run_id: str,
        case_id: str,
        kind: Literal["dry", "reference", "candidate", "nam"],
    ) -> Response:
        key_column = {
            "dry": BenchmarkCase.dry_key,
            "reference": BenchmarkCase.reference_wet_key,
            "candidate": RunCase.candidate_wet_key,
            "nam": BenchmarkCase.nam_reference_wet_key,
        }[kind]
        async with database.session() as session:
            object_key = await session.scalar(
                select(key_column)
                .select_from(RunCase)
                .join(BenchmarkCase, BenchmarkCase.id == RunCase.benchmark_case_id)
                .where(
                    RunCase.run_id == run_id,
                    RunCase.benchmark_case_id == case_id,
                )
            )
        if object_key is None or not await storage.exists(object_key):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} audio not found")
        value = await storage.get(object_key)
        return _audio_response(value, _audio_media_type(object_key), request.headers.get("range"))

    @app.get(
        "/api/v1/runs/{run_id}/events",
        response_model=EventsResponse,
        tags=["events"],
    )
    async def events(run_id: str, after_id: int = 0) -> EventsResponse:
        async with database.session() as session:
            run = await session.get(BenchmarkRun, run_id)
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
            values = (
                await session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.id > after_id)
                    .order_by(RunEvent.id)
                    .limit(1_000)
                )
            ).all()
        responses = [
            EventResponse(
                id=event.id,
                kind=event.kind,
                case_id=event.benchmark_case_id,
                payload=event.payload,
                occurred_at=event.occurred_at,
            )
            for event in values
        ]
        return EventsResponse(
            events=responses,
            next_after_id=responses[-1].id if responses else None,
        )

    @app.get("/api/v1/leaderboard", response_model=LeaderboardResponse, tags=["leaderboard"])
    async def leaderboard(  # noqa: PLR0917
        request: Request,
        response: Response,
        amp_id: str | None = None,
        creator: str | None = None,
        search: str | None = None,
        sort: str = "esr",
        direction: str = "asc",
        amp_scope: Literal["normal", "simple", "all"] = "all",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int | None, Query(ge=1, le=100)] = None,
    ) -> LeaderboardResponse | Response:
        etag = await _leaderboard_etag(services, request.url.query)
        cache_headers = {
            "Cache-Control": "public, no-cache",
            "ETag": etag,
        }
        if _etag_matches(request.headers.get("if-none-match", ""), etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)
        for key, value in cache_headers.items():
            response.headers[key] = value
        return await _leaderboard_page(
            services,
            amp_scope=amp_scope,
            amp_id=amp_id,
            creator=creator,
            search=search,
            sort_key=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )

    @app.get("/runs/{run_id}", include_in_schema=False)
    async def run_detail_redirect(run_id: str) -> RedirectResponse:
        run, locations = await _load_case_locations(services, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        if not locations:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run has no cases")
        return RedirectResponse(_case_page_url(run_id, locations[0].case_id))

    @app.get("/amps/{amp_id}", response_class=HTMLResponse, include_in_schema=False)
    async def amp_detail(request: Request, amp_id: str) -> Response:
        async with database.session() as session:
            amp = await session.get(Amp, amp_id)
            benchmark_cases = (
                await session.scalars(
                    select(BenchmarkCase)
                    .where(BenchmarkCase.amp_id == amp_id)
                    .order_by(BenchmarkCase.position_index, BenchmarkCase.chunk_index)
                )
            ).all()
        if amp is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "amp not found")
        position_matrices: dict[int, list[list[float]]] = {}
        for benchmark_case in benchmark_cases:
            position_matrices.setdefault(
                benchmark_case.position_index,
                benchmark_case.position_matrix,
            )
        amp_positions = [
            {"number": position_index + 1, "steps": matrix}
            for position_index, matrix in position_matrices.items()
        ]
        runs = await _leaderboard_runs(services, amp_id=amp_id)
        serialized_runs = [run.model_dump(mode="json") for run in runs]
        serialized_amp = AmpResponse.model_validate(amp).model_dump(mode="json")
        return templates.TemplateResponse(
            request=request,
            name="amp_detail.html",
            context={
                "amp": serialized_amp,
                "amp_positions": amp_positions,
                "runs": serialized_runs,
                "amp_page": {"amp": serialized_amp, "runs": serialized_runs},
            },
        )

    @app.get(
        "/runs/{run_id}/cases/{case_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def run_case_page(request: Request, run_id: str, case_id: str) -> Response:
        async with database.session() as session:
            run_info = (
                await session.execute(
                    select(
                        BenchmarkRun.name,
                        BenchmarkRun.created_at,
                        BenchmarkRun.description,
                    )
                    .join(RunCase, RunCase.run_id == BenchmarkRun.id)
                    .where(
                        BenchmarkRun.id == run_id,
                        RunCase.benchmark_case_id == case_id,
                    )
                )
            ).one_or_none()
        if run_info is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run case not found")
        run_name, run_started, run_description = run_info
        return templates.TemplateResponse(
            request=request,
            name="run_detail.html",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "run_name": run_name,
                "run_description": run_description,
                "run_started": run_started.isoformat(),
                "run_started_display": run_started.strftime("%d.%m.%Y %H:%M"),
                "page_title": f"{run_name} · Case detail",
            },
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(  # noqa: PLR0917
        request: Request,
        amp_id: str | None = None,
        creator: str | None = None,
        search: str | None = None,
        sort: str = "esr",
        direction: str = "asc",
        amp_scope: Literal["normal", "simple", "all"] = "normal",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = LEADERBOARD_PAGE_SIZE,
    ) -> Response:
        leaderboard_page = await _leaderboard_page(
            services,
            amp_scope=amp_scope,
            amp_id=amp_id,
            creator=creator,
            search=search,
            sort_key=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )
        serialized = leaderboard_page.model_dump(mode="json")
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "runs": serialized["runs"],
                "amps": serialized["amps"],
                "leaderboard": serialized,
                "creators": serialized["creators"],
                "simple_amp_ids": SIMPLE_AMP_IDS,
                "table_state": {
                    "amp_scope": amp_scope,
                    "amp_id": amp_id or "",
                    "creator": creator or "",
                    "search": search or "",
                },
            },
        )

    return app
