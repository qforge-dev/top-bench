from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

MODEL_CONFIG: dict[str, Any] = {
    "net": {
        "name": "WaveNet",
        "config": {
            "layers_configs": [
                {
                    "input_size": 1,
                    "condition_size": 1,
                    "channels": 8,
                    "kernel_sizes": [
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        15,
                        15,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                        6,
                    ],
                    "dilations": [
                        1,
                        3,
                        7,
                        17,
                        41,
                        101,
                        239,
                        1,
                        3,
                        7,
                        17,
                        41,
                        101,
                        239,
                        1,
                        13,
                        1,
                        3,
                        7,
                        17,
                        41,
                        101,
                        239,
                    ],
                    "activation": "LeakyReLU",
                    "gated": False,
                    "head": {"out_channels": 1, "kernel_size": 16, "bias": True},
                }
            ],
            "head_scale": 0.01,
        },
    },
    "loss": {"val_loss": "esr", "mrstft_weight": 0.0005},
    "optimizer": {"lr": 0.004, "weight_decay": 3.17e-7},
    "lr_scheduler": {"class": "ExponentialLR", "kwargs": {"gamma": 0.99976}},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


class S3:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self.aws = shutil.which("aws")
        if self.aws is None:
            msg = "AWS CLI is required"
            raise RuntimeError(msg)

    def list(self, prefix: str) -> list[str]:
        result = subprocess.run(  # noqa: S603
            [
                self.aws,
                "s3api",
                "list-objects-v2",
                "--bucket",
                self.bucket,
                "--prefix",
                prefix,
                "--query",
                "Contents[].Key",
                "--output",
                "json",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        return list(json.loads(result.stdout) or [])

    def download(self, key: str, destination: Path) -> None:
        if destination.exists():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        subprocess.run(  # noqa: S603
            [
                self.aws,
                "s3",
                "cp",
                f"s3://{self.bucket}/{key}",
                str(temporary),
                "--only-show-errors",
            ],
            check=True,
        )
        temporary.replace(destination)

    def upload(self, source: Path, key: str) -> None:
        subprocess.run(  # noqa: S603
            [
                self.aws,
                "s3",
                "cp",
                str(source),
                f"s3://{self.bucket}/{key}",
                "--only-show-errors",
            ],
            check=True,
        )


class QueueStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    job_key TEXT NOT NULL,
                    amp_id TEXT NOT NULL,
                    position_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    train_attempts INTEGER NOT NULL DEFAULT 0,
                    inference_attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("UPDATE jobs SET status = 'pending' WHERE status = 'training'")
            connection.execute("UPDATE jobs SET status = 'trained' WHERE status = 'inference'")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def add(self, job_key: str, job: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO jobs
                    (job_id, job_key, amp_id, position_id, status, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (
                    job["job_id"],
                    job_key,
                    job["amp_id"],
                    job["position_id"],
                    datetime.now(UTC).isoformat(),
                ),
            )

    def claim(self, source_status: str, claimed_status: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY updated_at, job_id LIMIT 1",
                (source_status,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (claimed_status, datetime.now(UTC).isoformat(), row["job_id"]),
            )
            connection.commit()
            return dict(row)
        finally:
            connection.close()

    def transition(self, job_id: str, status: str, *, error: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE job_id = ?",
                (status, error, datetime.now(UTC).isoformat(), job_id),
            )

    def fail(self, job_id: str, stage: str, error: BaseException) -> None:
        attempt_column = "train_attempts" if stage == "training" else "inference_attempts"
        retry_status = "pending" if stage == "training" else "trained"
        with self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {attempt_column} = {attempt_column} + 1 WHERE job_id = ?",  # noqa: S608
                (job_id,),
            )
            attempts = int(
                connection.execute(
                    f"SELECT {attempt_column} FROM jobs WHERE job_id = ?",  # noqa: S608
                    (job_id,),
                ).fetchone()[0]
            )
            status = retry_status if attempts < 3 else "failed"
            connection.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE job_id = ?",
                (
                    status,
                    f"{type(error).__name__}: {error}",
                    datetime.now(UTC).isoformat(),
                    job_id,
                ),
            )

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                str(row[0]): int(row[1])
                for row in connection.execute("SELECT status, count(*) FROM jobs GROUP BY status")
            }


class BestiaFactory:
    def __init__(
        self,
        *,
        root: Path,
        bucket: str,
        prefix: str,
        nam_bin: Path,
        nam_python: Path,
        repo: Path,
        gpus: list[int],
        epochs: int,
        inference_workers: int,
        poll_seconds: int,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.s3 = S3(bucket)
        self.prefix = prefix.rstrip("/")
        self.nam_bin = nam_bin
        self.nam_python = nam_python
        self.repo = repo
        self.gpus = gpus
        self.epochs = epochs
        self.inference_workers = inference_workers
        self.poll_seconds = poll_seconds
        self.store = QueueStore(root / "state" / "queue.sqlite3")
        self.dry_lock = threading.Lock()
        self.ffmpeg = shutil.which("ffmpeg")
        if self.ffmpeg is None:
            msg = "ffmpeg is required"
            raise RuntimeError(msg)
        self.nvidia_smi = shutil.which("nvidia-smi")
        if self.nvidia_smi is None:
            msg = "nvidia-smi is required"
            raise RuntimeError(msg)

    def _gpu_idle(self, gpu: int) -> bool:
        result = subprocess.run(  # noqa: S603
            [
                self.nvidia_smi,
                "-i",
                str(gpu),
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        utilization, memory = (int(float(value.strip())) for value in result.stdout.split(","))
        return utilization <= 10 and memory <= 1500

    def _reserve_gpu(self, gpu: int) -> None:
        directory = Path("/tmp/arena-gpu-locks")  # noqa: S108 - shared arena contract
        directory.mkdir(parents=True, exist_ok=True)
        lock = directory / f"gpu-{gpu}.lock"
        while True:
            if lock.exists():
                try:
                    holder = int(lock.read_text().strip())
                    os.kill(holder, 0)
                except (ValueError, ProcessLookupError):
                    lock.unlink(missing_ok=True)
                except PermissionError:
                    pass
                else:
                    LOGGER.info("GPU %d reserved by PID %d; waiting", gpu, holder)
                    time.sleep(30)
                    continue
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            with os.fdopen(descriptor, "w") as handle:
                handle.write(str(os.getpid()))
            return

    def _wait_gpu_idle(self, gpu: int) -> None:
        while True:
            try:
                if self._gpu_idle(gpu):
                    return
            except (OSError, subprocess.SubprocessError, ValueError):
                LOGGER.exception("could not inspect GPU %d", gpu)
            LOGGER.info("GPU %d is busy; waiting", gpu)
            time.sleep(30)

    def _job_json(self, row: dict[str, Any]) -> dict[str, Any]:
        path = self.root / "queue" / f"{row['job_id']}.json"
        self.s3.download(row["job_key"], path)
        return json.loads(path.read_text())

    def scan(self) -> None:
        ready_prefix = f"{self.prefix}/queue/ready/"
        for key in self.s3.list(ready_prefix):
            if not key.endswith(".json"):
                continue
            path = self.root / "queue" / Path(key).name
            self.s3.download(key, path)
            job = json.loads(path.read_text())
            if job.get("format") != "top-arena.nam-a2-full-job.v1":
                continue
            self.store.add(key, job)

    def _convert_to_float_wav(self, source: Path, destination: Path) -> None:
        if destination.exists():
            return
        subprocess.run(  # noqa: S603
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-c:a",
                "pcm_f32le",
                str(destination),
            ],
            check=True,
        )

    def train(self, row: dict[str, Any], gpu: int) -> None:
        job = self._job_json(row)
        if int(job["epochs"]) != self.epochs:
            msg = "job epoch contract differs from worker"
            raise ValueError(msg)
        directory = self.root / "jobs" / row["amp_id"] / row["position_id"]
        directory.mkdir(parents=True, exist_ok=True)
        dry_flac = self.root / "cache" / "dry-190s.flac"
        with self.dry_lock:
            self.s3.download(job["dry_key"], dry_flac)
        wet_flac = directory / "wet-190s.flac"
        self.s3.download(job["wet_key"], wet_flac)
        if sha256(dry_flac) != job["dry_sha256"] or sha256(wet_flac) != job["wet_sha256"]:
            msg = "training audio checksum mismatch"
            raise ValueError(msg)
        self._convert_to_float_wav(dry_flac, directory / "dry.wav")
        self._convert_to_float_wav(wet_flac, directory / "wet.wav")
        data_config = {
            "train": {"stop_seconds": job["train_stop_seconds"], "ny": 32768},
            "validation": {
                "start_seconds": job["train_stop_seconds"],
                "ny": None,
                "require_input_pre_silence": None,
            },
            "common": {
                "x_path": str(directory / "dry.wav"),
                "y_path": str(directory / "wet.wav"),
                "delay": int(job["latency_samples"]),
            },
        }
        learning_config = {
            "train_dataloader": {
                "batch_size": 16,
                "shuffle": True,
                "pin_memory": True,
                "drop_last": True,
                "num_workers": 0,
            },
            "val_dataloader": {},
            "trainer": {
                "accelerator": "gpu",
                "devices": 1,
                "max_epochs": self.epochs,
                "check_val_every_n_epoch": 10,
            },
        }
        write_json(directory / "data_config.json", data_config)
        write_json(directory / "model_config.json", MODEL_CONFIG)
        write_json(directory / "learning_config.json", learning_config)
        command = [
            str(self.nam_bin),
            str(directory / "data_config.json"),
            str(directory / "model_config.json"),
            str(directory / "learning_config.json"),
            str(directory / "out"),
        ]
        environment = {**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu)}
        log_path = directory / "train.log"
        with log_path.open("w") as log:
            subprocess.run(  # noqa: S603
                command,
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
            )
        candidates = sorted(directory.glob("out/*/model.nam"))
        if not candidates:
            msg = "NAM trainer produced no model.nam"
            raise RuntimeError(msg)
        model = directory / "model.nam"
        shutil.copy2(candidates[-1], model)
        output_root = job["output_root"]
        for artifact in (
            "model.nam",
            "data_config.json",
            "model_config.json",
            "learning_config.json",
            "train.log",
        ):
            self.s3.upload(directory / artifact, f"{output_root}/training/{artifact}")
        result = {
            "format": "top-arena.nam-a2-full-training-result.v1",
            "job_id": job["job_id"],
            "amp_id": job["amp_id"],
            "position_id": job["position_id"],
            "positions": job["positions"],
            "architecture": job["architecture"],
            "epochs": self.epochs,
            "gpu": gpu,
            "model_key": f"{output_root}/training/model.nam",
            "model_sha256": sha256(model),
            "dry_key": job["dry_key"],
            "wet_key": job["wet_key"],
            "latency_samples": job["latency_samples"],
            "completed_at": datetime.now(UTC).isoformat(),
        }
        result_path = directory / "training-result.json"
        write_json(result_path, result)
        self.s3.upload(result_path, f"{output_root}/training/training-result.json")
        self.store.transition(job["job_id"], "trained")
        LOGGER.info("trained %s on GPU %d", job["job_id"], gpu)

    def _ensure_benchmark_dry(self, job: dict[str, Any]) -> Path:
        directory = self.root / "cache" / "benchmark-dry"
        with self.dry_lock:
            existing = list(directory.glob("sound-*.flac")) if directory.exists() else []
            if len(existing) == 50:
                return directory
            directory.mkdir(parents=True, exist_ok=True)
            keys = [
                f"{job['benchmark_dry_prefix']}/sound-{index:02d}.flac" for index in range(1, 51)
            ]
            with ThreadPoolExecutor(max_workers=8) as downloads:
                list(
                    downloads.map(
                        lambda key: self.s3.download(key, directory / Path(key).name),
                        keys,
                    )
                )
        return directory

    def infer(self, row: dict[str, Any]) -> None:
        job = self._job_json(row)
        directory = self.root / "jobs" / row["amp_id"] / row["position_id"]
        model = directory / "model.nam"
        if not model.exists():
            self.s3.download(f"{job['output_root']}/training/model.nam", model)
        inputs = self._ensure_benchmark_dry(job)
        outputs = directory / "inference"
        if outputs.exists():
            shutil.rmtree(outputs)
        command = [
            str(self.nam_python),
            str(self.repo / "tools" / "nam_baselines" / "inference.py"),
            "--model",
            str(model),
            "--input-dir",
            str(inputs),
            "--output-dir",
            str(outputs),
            "--torch-threads",
            "8",
        ]
        environment = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
        subprocess.run(command, check=True, env=environment)  # noqa: S603
        inference = json.loads((outputs / "inference.json").read_text())
        output_root = job["output_root"]

        def upload_output(item: dict[str, Any]) -> None:
            self.s3.upload(
                outputs / item["file"],
                f"{output_root}/outputs/{item['file']}",
            )

        with ThreadPoolExecutor(max_workers=8) as uploads:
            list(uploads.map(upload_output, inference["outputs"]))
        cases = [
            {
                **item,
                "dry_key": f"{job['benchmark_dry_prefix']}/{item['file']}",
                "bias_reference_key": (f"{job['benchmark_reference_prefix']}/{item['file']}"),
                "nam_a2_full_key": f"{output_root}/outputs/{item['file']}",
            }
            for item in inference["outputs"]
        ]
        metadata = {
            "format": "top-arena.nam-a2-full-position.v1",
            "job_id": job["job_id"],
            "amp_id": job["amp_id"],
            "amp_name": job["amp_name"],
            "position_id": job["position_id"],
            "positions": job["positions"],
            "position_vector": job["position_vector"],
            "architecture": job["architecture"],
            "epochs": self.epochs,
            "model_key": f"{output_root}/training/model.nam",
            "model_sha256": sha256(model),
            "training_dry_key": job["dry_key"],
            "training_wet_key": job["wet_key"],
            "latency_samples": job["latency_samples"],
            "comparison_alignment": "shift BIAS reference left by latency_samples",
            "cases": cases,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        metadata_path = directory / "metadata.json"
        write_json(metadata_path, metadata)
        self.s3.upload(metadata_path, f"{output_root}/metadata.json")
        self.store.transition(job["job_id"], "done")
        LOGGER.info("inference complete %s", job["job_id"])

    def training_loop(self, gpu: int) -> None:
        self._reserve_gpu(gpu)
        while True:
            self._wait_gpu_idle(gpu)
            row = self.store.claim("pending", "training")
            if row is None:
                time.sleep(2)
                continue
            try:
                self.train(row, gpu)
            except Exception as error:
                LOGGER.exception("training failed: %s", row["job_id"])
                self.store.fail(row["job_id"], "training", error)

    def inference_loop(self) -> None:
        while True:
            row = self.store.claim("trained", "inference")
            if row is None:
                time.sleep(2)
                continue
            try:
                self.infer(row)
            except Exception as error:
                LOGGER.exception("inference failed: %s", row["job_id"])
                self.store.fail(row["job_id"], "inference", error)

    def run(self) -> None:
        for gpu in self.gpus:
            threading.Thread(
                target=self.training_loop,
                args=(gpu,),
                name=f"trainer-gpu-{gpu}",
                daemon=True,
            ).start()
        for index in range(self.inference_workers):
            threading.Thread(
                target=self.inference_loop,
                name=f"inference-{index}",
                daemon=True,
            ).start()
        while True:
            try:
                self.scan()
                LOGGER.info("queue %s", self.store.counts())
            except Exception:
                LOGGER.exception("queue scan failed")
            time.sleep(self.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Two-GPU NAM A2 Full factory on bestia")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--nam-bin", type=Path, required=True)
    parser.add_argument("--nam-python", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--gpus", default="0,2")
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--inference-workers", type=int, default=4)
    parser.add_argument("--poll-seconds", type=int, default=10)
    arguments = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(threadName)s %(levelname)s %(message)s"
    )
    factory = BestiaFactory(
        root=arguments.root,
        bucket=arguments.bucket,
        prefix=arguments.prefix,
        nam_bin=arguments.nam_bin,
        nam_python=arguments.nam_python,
        repo=arguments.repo,
        gpus=[int(value) for value in arguments.gpus.split(",")],
        epochs=arguments.epochs,
        inference_workers=arguments.inference_workers,
        poll_seconds=arguments.poll_seconds,
    )
    factory.run()


if __name__ == "__main__":
    main()
