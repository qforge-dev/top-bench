from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import Select


def new_id() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Amp(Base):
    __tablename__ = "amps"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    amp_type: Mapped[str] = mapped_column(String(100), index=True)
    control_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    cases: Mapped[list[BenchmarkCase]] = relationship(back_populates="amp")
    runs: Mapped[list[BenchmarkRun]] = relationship(back_populates="amp")


class BenchmarkCase(Base):
    __tablename__ = "benchmark_cases"
    __table_args__ = (UniqueConstraint("amp_id", "chunk_index", "position_index"),)

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=new_id)
    amp_id: Mapped[str] = mapped_column(ForeignKey("amps.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    position_index: Mapped[int] = mapped_column(Integer)
    position_matrix: Mapped[list[list[float]]] = mapped_column(JSON)
    dry_key: Mapped[str] = mapped_column(String(1024))
    dry_sha256: Mapped[str] = mapped_column(String(64))
    reference_wet_key: Mapped[str] = mapped_column(String(1024))
    reference_latency_samples: Mapped[int] = mapped_column(Integer, default=0)
    nam_reference_wet_key: Mapped[str | None] = mapped_column(String(1024))
    duration_seconds: Mapped[float] = mapped_column(Float)
    sample_rate: Mapped[int] = mapped_column(Integer, default=48_000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    amp: Mapped[Amp] = relationship(back_populates="cases")
    run_cases: Mapped[list[RunCase]] = relationship(back_populates="benchmark_case")

    @classmethod
    def select_all(cls) -> Select[tuple[BenchmarkCase]]:
        return select(cls).order_by(cls.chunk_index, cls.position_index)


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    creator: Mapped[str] = mapped_column(String(255), index=True)
    amp_id: Mapped[str] = mapped_column(ForeignKey("amps.id"), index=True)
    unique_positions_used: Mapped[int] = mapped_column(Integer)
    audio_duration_sum: Mapped[float] = mapped_column(Float)
    turns: Mapped[int] = mapped_column(Integer)
    training_time: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    parameter_count: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    client_finished: Mapped[bool] = mapped_column(Boolean, default=False)
    total_cases: Mapped[int] = mapped_column(Integer)
    completed_cases: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    amp: Mapped[Amp] = relationship(back_populates="runs")
    cases: Mapped[list[RunCase]] = relationship(back_populates="run", cascade="all, delete-orphan")
    events: Mapped[list[RunEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunCase(Base):
    __tablename__ = "run_cases"
    __table_args__ = (UniqueConstraint("run_id", "benchmark_case_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), index=True
    )
    benchmark_case_id: Mapped[str] = mapped_column(ForeignKey("benchmark_cases.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    candidate_wet_key: Mapped[str | None] = mapped_column(String(1024))
    realtime_x: Mapped[float | None] = mapped_column(Float)
    esr: Mapped[float | None] = mapped_column(Float)
    human_weighted_esr: Mapped[float | None] = mapped_column(Float)
    mrstft: Mapped[float | None] = mapped_column(Float)
    level_db: Mapped[float | None] = mapped_column(Float)
    peak_db: Mapped[float | None] = mapped_column(Float)
    correlation: Mapped[float | None] = mapped_column(Float)
    nam_esr: Mapped[float | None] = mapped_column(Float)
    nam_human_weighted_esr: Mapped[float | None] = mapped_column(Float)
    nam_mrstft: Mapped[float | None] = mapped_column(Float)
    nam_level_db: Mapped[float | None] = mapped_column(Float)
    nam_peak_db: Mapped[float | None] = mapped_column(Float)
    nam_correlation: Mapped[float | None] = mapped_column(Float)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[BenchmarkRun] = relationship(back_populates="cases")
    benchmark_case: Mapped[BenchmarkCase] = relationship(back_populates="run_cases")


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), index=True
    )
    benchmark_case_id: Mapped[str | None] = mapped_column(
        ForeignKey("benchmark_cases.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, index=True
    )

    run: Mapped[BenchmarkRun] = relationship(back_populates="events")
