from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(  # noqa: S603
        command,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout if capture else ""


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


def s3_upload(path: Path, destination: str) -> None:
    run(["aws", "s3", "cp", str(path), destination, "--only-show-errors"])


def s3_download(source: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    run(["aws", "s3", "cp", source, str(temporary), "--only-show-errors"])
    temporary.replace(destination)


def s3_exists(bucket: str, key: str) -> bool:
    result = subprocess.run(  # noqa: S603
        ["aws", "s3api", "head-object", "--bucket", bucket, "--key", key],  # noqa: S607
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0
