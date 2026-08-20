from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .process import run, s3_download

LOGGER = logging.getLogger(__name__)

DATABASE = "guitar_db"
OUTPUT = "s3://cinematic-audio-guitar-db-088543363904/athena-results/top-bench-corpus/"
QUERY = """WITH unique_recording AS (
  SELECT source_name, source_id, recording_id, dry_sha256, dry_s3_uri,
         start_sample, end_sample, valid_start_sample, valid_end_sample,
         duration_seconds, sample_rate, dry_channels, created_at,
         row_number() OVER (
           PARTITION BY source_name, recording_id
           ORDER BY duration_seconds DESC, created_at DESC
         ) AS recording_rank
  FROM guitar_db.wet_blackface63_examples_v2_clean
  WHERE source_category = 'electric_guitar'
    AND source_kind = 'real'
    AND split = 'test'
), source_ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY source_name ORDER BY duration_seconds DESC, recording_id
  ) AS source_rank
  FROM unique_recording WHERE recording_rank = 1
)
SELECT source_name, source_id, recording_id, dry_sha256, dry_s3_uri,
       start_sample, end_sample, valid_start_sample, valid_end_sample,
       duration_seconds, sample_rate, dry_channels
FROM source_ranked
WHERE source_rank <= 20
ORDER BY source_name, source_rank
"""


def query_candidates(destination: Path) -> Path:
    query_id = run(
        [
            "aws",
            "athena",
            "start-query-execution",
            "--region",
            "us-east-1",
            "--work-group",
            "primary",
            "--query-string",
            QUERY,
            "--query-execution-context",
            f"Database={DATABASE}",
            "--result-configuration",
            f"OutputLocation={OUTPUT}",
            "--query",
            "QueryExecutionId",
            "--output",
            "text",
        ],
        capture=True,
    ).strip()
    while True:
        status = json.loads(
            run(
                [
                    "aws",
                    "athena",
                    "get-query-execution",
                    "--region",
                    "us-east-1",
                    "--query-execution-id",
                    query_id,
                    "--query",
                    "QueryExecution.Status",
                    "--output",
                    "json",
                ],
                capture=True,
            )
        )
        state = status["State"]
        if state == "SUCCEEDED":
            break
        if state in {"FAILED", "CANCELLED"}:
            reason = status.get("StateChangeReason", state)
            raise RuntimeError(reason)
        time.sleep(1)
    s3_download(f"{OUTPUT}{query_id}.csv", destination)
    LOGGER.info("Athena candidates: %s", destination)
    return destination
