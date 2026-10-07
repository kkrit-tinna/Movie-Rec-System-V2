"""ArtifactStore factory: STORAGE_BACKEND=local|s3 picks the backend (§3).

Every CLI gets its store from get_store() so the same code runs on the laptop
and on Fargate; only the environment differs.
"""
import os
from pathlib import Path

import yaml

from movierec.storage.base import ArtifactStore
from movierec.storage.local import LocalStore
from movierec.storage.s3 import S3Store

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"

BACKEND_ENV = "STORAGE_BACKEND"
BUCKET_ENV = "MOVIEREC_S3_BUCKET"
DEFAULT_BACKEND = "local"
BACKENDS = ("local", "s3")


def _load_storage_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f).get("storage") or {}


def get_store(
    backend: str | None = None,
    bucket: str | None = None,
    root: str = "./artifacts",
) -> ArtifactStore:
    """Return the configured ArtifactStore.

    backend and bucket follow §3's precedence: argument, then env var, then
    config/default.yaml's `storage` block, then code default (local / none).
    root is LocalStore's directory and is ignored for s3.
    """
    config = _load_storage_config()
    if backend is None:
        backend = os.environ.get(BACKEND_ENV) or config.get("backend") or DEFAULT_BACKEND
    backend = backend.strip().lower()

    if backend == "local":
        return LocalStore(root=root)
    if backend == "s3":
        if bucket is None:
            bucket = os.environ.get(BUCKET_ENV) or config.get("bucket")
        if not bucket:
            raise ValueError(
                f"STORAGE_BACKEND=s3 needs a bucket: set {BUCKET_ENV} or storage.bucket"
            )
        return S3Store(bucket=bucket)
    raise ValueError(f"unknown storage backend {backend!r}; expected one of {BACKENDS}")
