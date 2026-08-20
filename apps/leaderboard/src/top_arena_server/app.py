from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
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
    RunResponse,
)
from .scoring import ScoringService
from .storage import ObjectStorage, create_storage


@dataclass(frozen=True, slots=True)
class Services:
    settings: Settings
    database: Database
    storage: ObjectStorage
    scoring: ScoringService


def _case_response(run_case: RunCase) -> CaseResultResponse:
    return CaseResultResponse(
        case_id=run_case.benchmark_case_id,
        status=run_case.status,
        realtime_x=run_case.realtime_x,
        esr=run_case.esr,
        human_weighted_esr=run_case.human_weighted_esr,
        mrstft=run_case.mrstft,
    )


def _run_response(run: BenchmarkRun, *, include_cases: bool = True) -> RunResponse:
    return RunResponse(
        id=run.id,
        name=run.name,
        creator=run.creator,
        amp_id=run.amp_id,
        amp_name=run.amp.name,
        amp_type=run.amp.amp_type,
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
                .options(joinedload(BenchmarkRun.amp), selectinload(BenchmarkRun.cases))
                .where(BenchmarkRun.id == run_id)
            ),
        )


async def _leaderboard_runs(
    services: Services,
    *,
    amp_type: str | None = None,
    creator: str | None = None,
    sort_key: str = "esr",
    direction: str = "asc",
) -> list[RunResponse]:
    statement = select(BenchmarkRun).join(BenchmarkRun.amp).options(joinedload(BenchmarkRun.amp))
    if amp_type:
        statement = statement.where(Amp.amp_type == amp_type)
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
        candidate_key = f"runs/{run_id}/candidates/{case_id}.wav"
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
        await storage.put(candidate_key, value)
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
                    payload={"realtime_x": realtime_x, "bytes": len(value)},
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
        amp_type: str | None = None,
        creator: str | None = None,
        sort: str = "esr",
        direction: str = "asc",
    ) -> LeaderboardResponse:
        runs = await _leaderboard_runs(
            services,
            amp_type=amp_type,
            creator=creator,
            sort_key=sort,
            direction=direction,
        )
        return LeaderboardResponse(
            runs=runs,
            amp_types=sorted({run.amp_type for run in runs}),
            creators=sorted({run.creator for run in runs}),
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request) -> Response:
        runs = await _leaderboard_runs(services)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "runs": [run.model_dump(mode="json") for run in runs],
                "amp_types": sorted({run.amp_type for run in runs}),
                "creators": sorted({run.creator for run in runs}),
            },
        )

    return app
