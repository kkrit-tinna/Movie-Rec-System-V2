"""Data quality gates for T1.6 (see IMPLEMENTATION.md T1.6).

Each gate is a pure function: given the data it checks plus the `quality`
block from config/default.yaml, it returns a GateResult. Nothing here
decides whether to abort a run -- that's enforce()'s job, and it always
writes the report before it raises, so a failed run still leaves a
quality_report.json behind.
"""
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
import yaml

from movierec.storage.base import ArtifactStore

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"


def _load_quality_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["quality"]


@dataclass
class GateResult:
    name: str
    passed: bool
    observed: float
    threshold: float
    skipped_reason: str | None = None


class QualityGateFailed(Exception):
    def __init__(self, results: list[GateResult]):
        self.failed = [r for r in results if not r.passed]
        names = ", ".join(r.name for r in self.failed)
        super().__init__(f"quality gates failed: {names}")


# --- raw-frame gates -------------------------------------------------------

def gate_row_count_min(raw_row_count: int, config: dict | None = None) -> GateResult:
    config = config or _load_quality_config()
    threshold = config["row_count_min"]
    return GateResult("row_count_min", raw_row_count >= threshold, raw_row_count, threshold)


def gate_row_count_drift(
    raw_row_count: int,
    store: ArtifactStore,
    run_date: str,
    config: dict | None = None,
) -> GateResult:
    config = config or _load_quality_config()
    threshold = config["row_count_drift_pct"]

    previous_row_count = _previous_row_count(store, run_date)
    if previous_row_count is None:
        return GateResult(
            "row_count_drift_pct", True, 0.0, threshold, skipped_reason="no previous run"
        )

    drift_pct = abs(raw_row_count - previous_row_count) / previous_row_count * 100
    return GateResult("row_count_drift_pct", drift_pct <= threshold, drift_pct, threshold)


def gate_null_rate(raw_series: pd.Series, field_name: str, config: dict | None = None) -> GateResult:
    config = config or _load_quality_config()
    threshold = config["null_rate_max"][field_name]
    null_rate = raw_series.isna().mean() if len(raw_series) else 0.0
    return GateResult(f"null_rate_max.{field_name}", null_rate <= threshold, null_rate, threshold)


# --- catalog gates (run on prepare()'s output, not the raw frame) ---------

def gate_catalog_count_min(catalog_row_count: int, config: dict | None = None) -> GateResult:
    config = config or _load_quality_config()
    threshold = config["catalog_count_min"]
    return GateResult(
        "catalog_count_min", catalog_row_count >= threshold, catalog_row_count, threshold
    )


def gate_duplicate_id_rate(catalog_ids: pd.Series, config: dict | None = None) -> GateResult:
    config = config or _load_quality_config()
    threshold = config["duplicate_id_rate_max"]
    ids = pd.Series(catalog_ids)
    n = len(ids)
    duplicate_rate = 0.0 if n == 0 else (n - ids.nunique()) / n
    return GateResult(
        "duplicate_id_rate_max", duplicate_rate <= threshold, duplicate_rate, threshold
    )


# --- embedding gates (dense numpy or scipy sparse) -------------------------

def gate_embedding_nan_rate(matrix, config: dict | None = None) -> GateResult:
    config = config or _load_quality_config()
    threshold = config["embedding_nan_rate_max"]

    if sp.issparse(matrix):
        nan_count = int(np.isnan(matrix.data).sum())
        total = matrix.shape[0] * matrix.shape[1]
    else:
        arr = np.asarray(matrix)
        nan_count = int(np.isnan(arr).sum())
        total = arr.size

    nan_rate = nan_count / total if total else 0.0
    return GateResult("embedding_nan_rate_max", nan_rate <= threshold, nan_rate, threshold)


def gate_zero_vector_rate(matrix, config: dict | None = None) -> GateResult:
    config = config or _load_quality_config()
    threshold = config["zero_vector_rate_max"]

    if sp.issparse(matrix):
        n_rows = matrix.shape[0]
        zero_rows = int((matrix.getnnz(axis=1) == 0).sum())
    else:
        arr = np.asarray(matrix)
        n_rows = arr.shape[0]
        zero_rows = int((~arr.any(axis=1)).sum())

    zero_rate = zero_rows / n_rows if n_rows else 0.0
    return GateResult("zero_vector_rate_max", zero_rate <= threshold, zero_rate, threshold)


# --- enforcement ------------------------------------------------------------

def _previous_row_count(store: ArtifactStore, run_date: str) -> float | None:
    paths = store.list("quality")
    dates = sorted(
        {p.split("/")[1] for p in paths if p.endswith("quality_report.json")}
    )
    previous_dates = [d for d in dates if d < run_date]
    if not previous_dates:
        return None

    report = store.get_json(f"quality/{previous_dates[-1]}/quality_report.json")
    for gate in report["gates"]:
        if gate["name"] == "row_count_min":
            return gate["observed"]
    return None


def enforce(results: list[GateResult], store: ArtifactStore, run_date: str) -> None:
    """Write the cumulative report, then raise if any gate failed.

    Callers pass the cumulative list of results collected so far; calling
    this again later (e.g. once embeddings are available) overwrites the
    report at the same path with the fuller one.
    """
    report = {"run_date": run_date, "gates": [asdict(r) for r in results]}
    store.put_json(f"quality/{run_date}/quality_report.json", report)

    if any(not r.passed for r in results):
        raise QualityGateFailed(results)
