import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from movierec.data.quality import (
    GateResult,
    QualityGateFailed,
    enforce,
    gate_catalog_count_min,
    gate_duplicate_id_rate,
    gate_embedding_nan_rate,
    gate_null_rate,
    gate_row_count_drift,
    gate_row_count_min,
    gate_zero_vector_rate,
)
from movierec.storage.local import LocalStore

CONFIG = {
    "row_count_min": 1000,
    "row_count_drift_pct": 15,
    "catalog_count_min": 100,
    "null_rate_max": {"title": 0.001, "overview": 0.60},
    "duplicate_id_rate_max": 0.0,
    "embedding_nan_rate_max": 0.0,
    "zero_vector_rate_max": 0.02,
}


def _store(tmp_path):
    return LocalStore(root=str(tmp_path / "artifacts"))


class TestRowCountMin:
    def test_passes_above_threshold(self):
        result = gate_row_count_min(1500, CONFIG)
        assert result.passed
        assert result.observed == 1500

    def test_fails_below_threshold(self):
        result = gate_row_count_min(500, CONFIG)
        assert not result.passed


class TestRowCountDrift:
    def test_skips_with_no_previous_run(self, tmp_path):
        store = _store(tmp_path)
        result = gate_row_count_drift(1000, store, "2026-09-21", CONFIG)
        assert result.passed
        assert result.skipped_reason == "no previous run"

    def test_passes_within_threshold(self, tmp_path):
        store = _store(tmp_path)
        enforce([gate_row_count_min(1000, CONFIG)], store, "2026-09-14")

        result = gate_row_count_drift(1100, store, "2026-09-21", CONFIG)
        assert result.passed
        assert result.skipped_reason is None

    def test_fails_beyond_threshold(self, tmp_path):
        store = _store(tmp_path)
        enforce([gate_row_count_min(1000, CONFIG)], store, "2026-09-14")

        result = gate_row_count_drift(1200, store, "2026-09-21", CONFIG)
        assert not result.passed


class TestNullRate:
    def test_passes_below_threshold(self):
        series = pd.Series(["a", "b", "c", None], dtype="string")
        result = gate_null_rate(series, "overview", CONFIG)
        assert result.passed

    def test_fails_above_threshold(self):
        series = pd.Series([None, None, "a"], dtype="string")
        result = gate_null_rate(series, "title", CONFIG)
        assert not result.passed


class TestCatalogCountMin:
    def test_passes_above_threshold(self):
        result = gate_catalog_count_min(150, CONFIG)
        assert result.passed

    def test_fails_below_threshold(self):
        result = gate_catalog_count_min(50, CONFIG)
        assert not result.passed


class TestDuplicateIdRate:
    def test_passes_with_no_duplicates(self):
        result = gate_duplicate_id_rate(pd.Series([1, 2, 3]), CONFIG)
        assert result.passed
        assert result.observed == 0.0

    def test_fails_on_one_repeated_id(self):
        result = gate_duplicate_id_rate(pd.Series([1, 1, 2, 3]), CONFIG)
        assert not result.passed
        assert result.observed > 0.0


class TestEmbeddingNanRate:
    def test_passes_with_dense_clean_matrix(self):
        arr = np.array([[1.0, 0.0], [0.0, 1.0]])
        result = gate_embedding_nan_rate(arr, CONFIG)
        assert result.passed

    def test_fails_with_dense_nan(self):
        arr = np.array([[1.0, np.nan], [0.0, 1.0]])
        result = gate_embedding_nan_rate(arr, CONFIG)
        assert not result.passed

    def test_passes_with_sparse_clean_matrix(self):
        matrix = sp.csr_matrix(np.array([[1.0, 0.0], [0.0, 1.0]]))
        result = gate_embedding_nan_rate(matrix, CONFIG)
        assert result.passed

    def test_fails_with_sparse_nan(self):
        matrix = sp.csr_matrix(np.array([[1.0, 0.0], [0.0, 1.0]]))
        matrix.data[0] = np.nan
        result = gate_embedding_nan_rate(matrix, CONFIG)
        assert not result.passed


class TestZeroVectorRate:
    def test_passes_with_dense_nonzero_rows(self):
        arr = np.array([[1.0, 0.0], [0.0, 1.0]])
        result = gate_zero_vector_rate(arr, CONFIG)
        assert result.passed

    def test_fails_with_dense_zero_row(self):
        arr = np.array([[0.0, 0.0], [0.0, 1.0]])
        result = gate_zero_vector_rate(arr, CONFIG)
        assert not result.passed

    def test_passes_with_sparse_nonzero_rows(self):
        matrix = sp.csr_matrix(np.array([[1.0, 0.0], [0.0, 1.0]]))
        result = gate_zero_vector_rate(matrix, CONFIG)
        assert result.passed

    def test_fails_with_sparse_zero_row(self):
        matrix = sp.csr_matrix(np.array([[0.0, 0.0], [0.0, 1.0]]))
        result = gate_zero_vector_rate(matrix, CONFIG)
        assert not result.passed


class TestEnforce:
    def test_writes_report_and_does_not_raise_when_all_pass(self, tmp_path):
        store = _store(tmp_path)
        results = [gate_row_count_min(1500, CONFIG)]
        enforce(results, store, "2026-09-21")
        assert store.exists("quality/2026-09-21/quality_report.json")

    def test_writes_report_then_raises_when_any_fail(self, tmp_path):
        store = _store(tmp_path)
        results = [
            gate_row_count_min(1500, CONFIG),
            gate_catalog_count_min(10, CONFIG),
        ]
        with pytest.raises(QualityGateFailed):
            enforce(results, store, "2026-09-21")
        assert store.exists("quality/2026-09-21/quality_report.json")

    def test_failure_lists_only_failed_gates(self, tmp_path):
        store = _store(tmp_path)
        results = [
            gate_row_count_min(1500, CONFIG),
            gate_catalog_count_min(10, CONFIG),
        ]
        with pytest.raises(QualityGateFailed) as exc_info:
            enforce(results, store, "2026-09-21")
        assert len(exc_info.value.failed) == 1
        assert exc_info.value.failed[0].name == "catalog_count_min"

    def test_second_enforce_call_overwrites_with_fuller_report(self, tmp_path):
        store = _store(tmp_path)
        enforce([gate_row_count_min(1500, CONFIG)], store, "2026-09-21")

        fuller = [
            gate_row_count_min(1500, CONFIG),
            gate_catalog_count_min(150, CONFIG),
        ]
        enforce(fuller, store, "2026-09-21")

        report = store.get_json("quality/2026-09-21/quality_report.json")
        assert len(report["gates"]) == 2


def test_gate_result_is_a_dataclass_with_expected_fields():
    result = GateResult(name="x", passed=True, observed=1, threshold=2)
    assert result.skipped_reason is None
