from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from gensim.models import KeyedVectors
from sklearn.feature_extraction.text import CountVectorizer

from movierec import pipeline
from movierec.data.ingest import load, prepare
from movierec.data.quality import QualityGateFailed
from movierec.embedders.word2vec import MODEL_PATH_ENV
from movierec.storage.local import LocalStore

REPO = Path(__file__).resolve().parents[1]
SAMPLE = str(REPO / "data" / "sample" / "movies_5k.csv")
OVERLAY = str(REPO / "config" / "sample.yaml")
RUN_DATE = "2026-10-06"
REPORT = f"quality/{RUN_DATE}/quality_report.json"


def _save_vectors(path: Path, words: list[str]) -> str:
    """A tiny random stand-in for GloVe; tests never load the real file."""
    rng = np.random.default_rng(0)
    kv = KeyedVectors(vector_size=8)
    kv.add_vectors(words, rng.standard_normal((len(words), 8)).astype(np.float32))
    kv.save(str(path))
    return str(path)


@pytest.fixture
def sample_vectors(tmp_path, monkeypatch):
    # Vectors for every sample token, so no document is all-OOV.
    catalog = prepare(load(SAMPLE))
    words = CountVectorizer(stop_words="english").fit(catalog["document"]).get_feature_names_out()
    monkeypatch.setenv(MODEL_PATH_ENV, _save_vectors(tmp_path / "kv.kv", list(words)))


@pytest.fixture
def store(tmp_path):
    return LocalStore(root=str(tmp_path / "artifacts"))


def test_deep_merge_changes_only_overlay_keys():
    merged = pipeline._deep_merge(
        {"quality": {"row_count_min": 1, "null_rate_max": {"title": 0.1}}, "index": {"k": 10}},
        {"quality": {"row_count_min": 5}},
    )
    assert merged == {
        "quality": {"row_count_min": 5, "null_rate_max": {"title": 0.1}},
        "index": {"k": 10},
    }


def test_sample_overlay_lowers_only_count_floors():
    default, sample = pipeline.load_config(), pipeline.load_config(OVERLAY)
    assert sample["quality"]["row_count_min"] == 5000
    assert sample["quality"]["catalog_count_min"] == 250
    for key in ("row_count_min", "catalog_count_min"):
        default["quality"].pop(key), sample["quality"].pop(key)
    assert sample == default


def test_end_to_end_on_sample(sample_vectors, store):
    pipeline.run(SAMPLE, RUN_DATE, store, pipeline.load_config(OVERLAY), config_overlay=OVERLAY)

    for method in ("tfidf", "word2vec"):
        neighbours = pd.read_parquet(store.root / method / RUN_DATE / "neighbours.parquet")
        assert neighbours["movie_id"].nunique() == 262
        assert store.exists(f"{method}/{RUN_DATE}/row_index.parquet")

    catalog = pd.read_parquet(store.root / "catalog" / RUN_DATE / "catalog.parquet")
    assert len(catalog) == 262

    report = store.get_json(REPORT)
    assert report["config_overlay"] == OVERLAY
    assert all(g["passed"] for g in report["gates"])
    assert {"tfidf.zero_vector_rate_max", "word2vec.embedding_nan_rate_max"} <= {
        g["name"] for g in report["gates"]
    }

    metrics = store.get_json(f"comparison/{RUN_DATE}/metrics.json")
    assert {r["method"] for r in metrics["rows"]} == {"tfidf", "word2vec", "random"}
    assert store.get_json("current.json") == {"run_date": RUN_DATE, "method": "tfidf"}


def test_data_gate_failure_writes_only_quality_report(sample_vectors, store):
    # No overlay: the 1.3M / 70K production floors fail on the sample.
    with pytest.raises(QualityGateFailed) as err:
        pipeline.run(SAMPLE, RUN_DATE, store, pipeline.load_config())

    assert {r.name for r in err.value.failed} == {"row_count_min", "catalog_count_min"}
    assert store.list("") == [REPORT]
    assert store.get_json(REPORT)["config_overlay"] is None


def test_embedding_gate_failure_writes_only_quality_report(tmp_path, monkeypatch, store):
    # Vectors share no word with the sample, so every Word2Vec row is zero.
    monkeypatch.setenv(MODEL_PATH_ENV, _save_vectors(tmp_path / "kv.kv", ["zzqx", "qqzx"]))
    with pytest.raises(QualityGateFailed) as err:
        pipeline.run(SAMPLE, RUN_DATE, store, pipeline.load_config(OVERLAY), config_overlay=OVERLAY)

    assert [r.name for r in err.value.failed] == ["word2vec.zero_vector_rate_max"]
    assert store.list("") == [REPORT]


def test_main_exits_nonzero_on_gate_failure(sample_vectors, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    assert pipeline.main(["--input", SAMPLE, "--run-date", RUN_DATE]) == 1
    assert LocalStore(root=str(tmp_path / "artifacts")).list("") == [REPORT]
