# Copyright (c) 2026 Top Arena contributors

"""Public API for running a model against the Top Arena benchmark."""

from importlib.metadata import version

from top_arena import benchmark
from top_arena._models import BenchmarkResult, PipelineOptions, PositionMatrix, ReportFormat
from top_arena._pipeline import BenchmarkRun

__version__ = version("top-arena")

__all__ = [
    "BenchmarkResult",
    "BenchmarkRun",
    "PipelineOptions",
    "PositionMatrix",
    "ReportFormat",
    "__version__",
    "benchmark",
]
