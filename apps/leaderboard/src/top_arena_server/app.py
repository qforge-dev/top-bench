from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from .config import Settings
from .database import Database
from .models import Amp, BenchmarkCase, BenchmarkRun, RunCase, RunEvent, now_utc
from .schemas import (
    AmpResponse,
    CaseResultResponse,
    CreateRunRequest,
    CreateRunResponse,
    EventRequest,
    EventResponse,
    EventsResponse,
    LeaderboardResponse,
    ManifestCase,
    ManifestResponse,
    RunCaseAnalysisResponse,
    RunCaseAudioResponse,
    RunCaseDetailResponse,
    RunCaseIndexItem,
    RunCaseIndexResponse,
    RunCaseMetricsResponse,
    RunResponse,
    WaveformResponse,
    WaveformSeriesResponse,
)
from .scoring import ScoringService
from .storage import ObjectStorage, create_storage
from .waveform import waveform_envelope

LOGGER = logging.getLogger(__name__)


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


def _run_response(run: BenchmarkRun, *, include_cases: bool = True) -> RunResponse:
    return RunResponse(
        id=run.id,
        name=run.name,
        creator=run.creator,
        amp_id=run.amp_id,
        amp_name=run.amp.name,
        amp_type=run.amp.amp_type,
        amp_control_count=len(run.amp.control_names),
        unique_positions_used=run.unique_positions_used,
        audio_duration_sum=run.audio_duration_sum,
        turns=run.turns,
        training_time=run.training_time,
        description=run.description,
        parameter_count=run.parameter_count,
        status=run.status,
        total_cases=run.total_cases,
        completed_cases=run.completed_cases,
        metrics=run.metrics,
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
    amp_id: str | None = None,
    creator: str | None = None,
    sort_key: str = "esr",
    direction: str = "asc",
) -> list[RunResponse]:
    statement = select(BenchmarkRun).join(BenchmarkRun.amp).options(joinedload(BenchmarkRun.amp))
    if amp_id:
        statement = statement.where(BenchmarkRun.amp_id == amp_id)
    if creator:
        statement = statement.where(BenchmarkRun.creator == creator)
    async with services.database.session() as session:
        runs = (await session.scalars(statement)).unique().all()
    responses = [_run_response(run, include_cases=False) for run in runs]

    def metric_value(run: RunResponse, metric: str, summary: str = "mean") -> float:
        value = run.metrics.get(metric, {}).get(summary)
        return float(value) if value is not None else float("inf")

    keys: dict[str, Any] = {
        "name": lambda run: run.name.casefold(),
        "creator": lambda run: run.creator.casefold(),
        "positions": lambda run: run.unique_positions_used,
        "esr": lambda run: metric_value(run, "esr"),
        "mrstft": lambda run: metric_value(run, "mrstft"),
        "speed": lambda run: metric_value(run, "realtime_x"),
        "created": lambda run: run.created_at,
    }
    key = keys.get(sort_key, keys["esr"])
    responses.sort(key=key, reverse=direction == "desc")
    return responses


async def _leaderboard_amps(services: Services) -> list[AmpResponse]:
    async with services.database.session() as session:
        amps = (await session.scalars(select(Amp).order_by(Amp.name, Amp.id))).all()
    return [AmpResponse.model_validate(amp) for amp in amps]


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
    package_root = Path(__file__).parent
    static_root = package_root / "static"
    template_root = package_root / "templates"
    static_root.mkdir(exist_ok=True)
    template_root.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_root), name="static")
    templates = Jinja2Templates(directory=template_root)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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

    @app.post(
        "/api/v1/runs",
        response_model=CreateRunResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["runs"],
    )
    async def create_run(request: CreateRunRequest) -> CreateRunResponse:
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
                run = BenchmarkRun(
                    **request.model_dump(),
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
            return EventResponse(
                id=event.id,
                kind=event.kind,
                case_id=event.benchmark_case_id,
                payload=event.payload,
                occurred_at=event.occurred_at,
            )

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
        request_media_type = request.headers.get("content-type", "").partition(";")[0].lower()
        is_flac = request_media_type in {"application/flac", "audio/flac", "audio/x-flac"}
        extension = ".flac" if is_flac else ".wav"
        media_type = "audio/flac" if is_flac else "audio/wav"
        candidate_key = f"runs/{run_id}/candidates/{case_id}{extension}"
        async with database.session() as session:
            run = await session.get(BenchmarkRun, run_id)
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
            if run.client_finished or run.status in {"completed", "failed"}:
                raise HTTPException(status.HTTP_409_CONFLICT, "run no longer accepts uploads")
            run_case = await session.scalar(
                select(RunCase).where(
                    RunCase.run_id == run_id,
                    RunCase.benchmark_case_id == case_id,
                )
            )
            if run_case is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run case not found")
            if run_case.status in {"scoring", "completed", "failed"}:
                raise HTTPException(status.HTTP_409_CONFLICT, "run case no longer accepts uploads")
            run_case_id = run_case.id
        value = await request.body()
        await storage.put(candidate_key, value, content_type=media_type)
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
        await services.scoring.enqueue(run_case_id)
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
            analysis=RunCaseAnalysisResponse.model_validate(run_case.analysis or {}),
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
    async def leaderboard(
        amp_id: str | None = None,
        creator: str | None = None,
        sort: str = "esr",
        direction: str = "asc",
    ) -> LeaderboardResponse:
        runs = await _leaderboard_runs(
            services,
            amp_id=amp_id,
            creator=creator,
            sort_key=sort,
            direction=direction,
        )
        return LeaderboardResponse(
            runs=runs,
            amps=await _leaderboard_amps(services),
            creators=sorted({run.creator for run in runs}),
        )

    @app.get("/runs/{run_id}", include_in_schema=False)
    async def run_detail_redirect(run_id: str) -> RedirectResponse:
        run, locations = await _load_case_locations(services, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        if not locations:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run has no cases")
        return RedirectResponse(_case_page_url(run_id, locations[0].case_id))

    @app.get(
        "/runs/{run_id}/cases/{case_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def run_case_page(request: Request, run_id: str, case_id: str) -> Response:
        async with database.session() as session:
            run_name = await session.scalar(
                select(BenchmarkRun.name)
                .join(RunCase, RunCase.run_id == BenchmarkRun.id)
                .where(
                    BenchmarkRun.id == run_id,
                    RunCase.benchmark_case_id == case_id,
                )
            )
        if run_name is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run case not found")
        return templates.TemplateResponse(
            request=request,
            name="run_detail.html",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "run_name": run_name,
                "page_title": f"{run_name} · Case detail",
            },
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request) -> Response:
        runs = await _leaderboard_runs(services)
        amps = await _leaderboard_amps(services)
        serialized_runs = [run.model_dump(mode="json") for run in runs]
        serialized_amps = [amp.model_dump(mode="json") for amp in amps]
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "runs": serialized_runs,
                "amps": serialized_amps,
                "leaderboard": {"runs": serialized_runs, "amps": serialized_amps},
                "creators": sorted({run.creator for run in runs}),
            },
        )

    return app
