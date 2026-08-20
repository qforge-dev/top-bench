from __future__ import annotations

from tools.nam_baselines.bestia_worker import MODEL_CONFIG, QueueStore
from tools.nam_baselines.config import NamBaselineConfig
from tools.nam_baselines.producer import _upload_ready_job


def test_ready_marker_is_uploaded_after_wet(tmp_path, monkeypatch) -> None:
    wet = tmp_path / "wet.flac"
    marker = tmp_path / "job.json"
    wet.write_bytes(b"fLaC")
    marker.write_text("{}")
    calls = []

    def upload(path, destination):
        calls.append((path.name, destination))

    monkeypatch.setattr("tools.nam_baselines.producer.s3_upload", upload)
    config = NamBaselineConfig()
    row = {"wet_key": "wet.flac", "job_key": "ready.json"}
    assert _upload_ready_job(config, wet, marker, row) is row
    assert calls == [
        ("wet.flac", f"s3://{config.corpus.bucket}/wet.flac"),
        ("job.json", f"s3://{config.corpus.bucket}/ready.json"),
    ]
    assert not wet.exists()
    assert not marker.exists()


def test_queue_claims_jobs_once_and_recovers_interrupted_stages(tmp_path) -> None:
    path = tmp_path / "queue.sqlite3"
    store = QueueStore(path)
    job = {"job_id": "amp--position-01", "amp_id": "amp", "position_id": "position-01"}
    store.add("ready/job.json", job)
    claimed = store.claim("pending", "training")
    assert claimed is not None
    assert claimed["job_id"] == job["job_id"]
    assert store.claim("pending", "training") is None
    recovered = QueueStore(path)
    assert recovered.claim("pending", "training") is not None


def test_model_contract_is_official_a2_full() -> None:
    layer = MODEL_CONFIG["net"]["config"]["layers_configs"][0]
    assert MODEL_CONFIG["net"]["name"] == "WaveNet"
    assert layer["channels"] == 8
    assert len(layer["kernel_sizes"]) == 23
    assert len(layer["dilations"]) == 23
