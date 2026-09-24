import json

import numpy as np
import pytest
from gensim.models import KeyedVectors

from movierec.embedders.base import BaseEmbedder
from movierec.embedders.tfidf import TfidfEmbedder
from movierec.embedders.word2vec import Word2VecEmbedder
from movierec.eval.metrics import (
    COLUMNS,
    run_comparison,
    run_dir_mb,
    unigram_idf,
    write_comparison,
)
from movierec.storage.local import LocalStore

RUN_DATE = "2026-09-23"
RSS_INTERVAL = 0.001

TFIDF_CONFIG = {
    "max_features": 100,
    "ngram_range": [1, 2],
    "min_df": 1,
    "max_df": 1.0,
    "sublinear_tf": True,
    "stop_words": "english",
}
W2V_CONFIG = {"model_path": None, "model_name": "glove-wiki-gigaword-100"}

WORDS = ["spy", "thriller", "betrayal", "paris", "comedy", "animals"]
VECTORS = np.eye(6, dtype=np.float32)

TEXTS = [
    "a spy thriller with betrayal",
    "a spy thriller in paris",
    "a comedy set in paris",
    "talking animals comedy",
    "zyzzyva quuxly",
]
MOVIE_IDS = [10, 20, 30, 40, 50]


@pytest.fixture
def keyed_vectors():
    kv = KeyedVectors(vector_size=6)
    kv.add_vectors(WORDS, VECTORS)
    return kv


@pytest.fixture
def store(tmp_path):
    return LocalStore(root=str(tmp_path))


class StubEmbedder(BaseEmbedder):
    """A trivial third method: nothing in the runner may need to know it exists."""

    name = "stub"

    def fit(self, texts, movie_ids):
        rng = np.random.default_rng(0)
        self.matrix = rng.random((len(texts), 3)).astype(np.float16)
        self.movie_ids = list(movie_ids)

    def transform(self, texts):
        return np.zeros((len(texts), 3), dtype=np.float16)

    def save(self, store, run_date):
        store.put_bytes(f"{self.name}/{run_date}/stub.bin", b"x" * 2048)

    @classmethod
    def load(cls, store, run_date):
        return cls()


def _word2vec_factory(keyed_vectors, seen=None):
    def factory(fitted):
        if seen is not None:
            seen.append(dict(fitted))
        return Word2VecEmbedder(
            unigram_idf(fitted["tfidf"]),
            config=W2V_CONFIG,
            tfidf_config=TFIDF_CONFIG,
            keyed_vectors=keyed_vectors,
        )

    return factory


def _run(store, keyed_vectors, extra=(), seen=None):
    methods = [TfidfEmbedder(TFIDF_CONFIG), _word2vec_factory(keyed_vectors, seen), *extra]
    return run_comparison(methods, TEXTS, MOVIE_IDS, store, RUN_DATE, RSS_INTERVAL)


class TestRunComparison:
    def test_one_row_per_method_in_list_order(self, store, keyed_vectors):
        rows = _run(store, keyed_vectors)
        assert [r["method"] for r in rows] == ["tfidf", "word2vec"]

    def test_third_method_needs_no_runner_change(self, store, keyed_vectors):
        rows = _run(store, keyed_vectors, extra=[StubEmbedder()])
        assert [r["method"] for r in rows] == ["tfidf", "word2vec", "stub"]
        stub = rows[2]
        assert stub["catalog_size"] == len(TEXTS)
        assert stub["oov_rate"] is None
        assert stub["median_idf_fallback_rate"] is None
        assert store.exists(f"stub/{RUN_DATE}/neighbours.parquet")

    def test_every_row_has_every_column(self, store, keyed_vectors):
        for row in _run(store, keyed_vectors, extra=[StubEmbedder()]):
            assert set(row) == set(COLUMNS)

    def test_cost_columns_are_populated(self, store, keyed_vectors):
        for row in _run(store, keyed_vectors):
            assert row["catalog_size"] == len(TEXTS)
            assert row["fit_seconds"] >= 0
            assert row["peak_rss_mb"] > 0
            assert row["artifact_mb"] > 0

    def test_tfidf_fitted_before_word2vec_is_built(self, store, keyed_vectors):
        seen = []
        _run(store, keyed_vectors, seen=seen)
        assert list(seen[0]) == ["tfidf"]
        assert seen[0]["tfidf"].matrix is not None

    def test_each_method_writes_its_own_run_dir(self, store, keyed_vectors):
        _run(store, keyed_vectors)
        for method in ("tfidf", "word2vec"):
            paths = store.list(f"{method}/{RUN_DATE}")
            assert f"{method}/{RUN_DATE}/neighbours.parquet" in paths
            assert len(paths) >= 3

    def test_artifact_mb_is_whole_run_dir(self, store, keyed_vectors, tmp_path):
        rows = _run(store, keyed_vectors, extra=[StubEmbedder()])
        for row in rows:
            run_dir = tmp_path / row["method"] / RUN_DATE
            on_disk = sum(p.stat().st_size for p in run_dir.rglob("*") if p.is_file())
            assert row["artifact_mb"] == pytest.approx(on_disk / (1024 * 1024))
            matrix_only = max(p.stat().st_size for p in run_dir.rglob("*") if p.is_file())
            assert on_disk > matrix_only

    def test_coverage_null_for_tfidf_set_for_word2vec(self, store, keyed_vectors):
        tfidf, w2v = _run(store, keyed_vectors)
        assert tfidf["oov_rate"] is None
        assert tfidf["median_idf_fallback_rate"] is None
        assert 0 <= w2v["oov_rate"] <= 1
        assert 0 <= w2v["median_idf_fallback_rate"] <= 1

    def test_duplicate_method_name_rejected(self, store):
        with pytest.raises(ValueError, match="duplicate"):
            run_comparison(
                [StubEmbedder(), StubEmbedder()], TEXTS, MOVIE_IDS, store, RUN_DATE, RSS_INTERVAL
            )


class TestUnigramIdf:
    def test_drops_bigrams_and_keeps_fitted_weights(self):
        tfidf = TfidfEmbedder(TFIDF_CONFIG)
        tfidf.fit(TEXTS, MOVIE_IDS)
        idf = unigram_idf(tfidf)
        vocab = tfidf.vectorizer.vocabulary_
        assert idf
        assert not any(" " in token for token in idf)
        assert any(" " in token for token in vocab)
        for token, weight in idf.items():
            assert weight == pytest.approx(tfidf.vectorizer.idf_[vocab[token]])


class TestCoverageStats:
    def test_token_level_rates(self, keyed_vectors):
        # Tokens after stop-word removal: spy, thriller, paris, zyzzyva.
        # zyzzyva has no vector -> oov 1/4. Of the 3 in-vocab tokens, paris is
        # missing from the IDF map -> fallback 1/3.
        embedder = Word2VecEmbedder(
            {"spy": 1.0, "thriller": 2.0},
            config=W2V_CONFIG,
            tfidf_config=TFIDF_CONFIG,
            keyed_vectors=keyed_vectors,
        )
        stats = embedder.coverage_stats(["a spy thriller", "paris zyzzyva"])
        assert stats["oov_rate"] == pytest.approx(1 / 4)
        assert stats["median_idf_fallback_rate"] == pytest.approx(1 / 3)

    def test_no_tokens_gives_null_not_error(self, keyed_vectors):
        embedder = Word2VecEmbedder(
            {"spy": 1.0}, config=W2V_CONFIG, tfidf_config=TFIDF_CONFIG, keyed_vectors=keyed_vectors
        )
        stats = embedder.coverage_stats(["the and of", "zyzzyva"])
        assert stats["oov_rate"] == 1.0
        assert stats["median_idf_fallback_rate"] is None

    def test_base_embedder_reports_nothing(self):
        assert StubEmbedder().coverage_stats(TEXTS) == {}


class TestWriteComparison:
    def test_writes_rows_with_null_cells(self, store, tmp_path):
        rows = [
            {"method": "tfidf", "catalog_size": 5, "fit_seconds": 0.1, "peak_rss_mb": 100.0,
             "artifact_mb": 0.5, "oov_rate": None, "median_idf_fallback_rate": None},
        ]
        path = write_comparison(rows, store, RUN_DATE)
        assert path == f"comparison/{RUN_DATE}/comparison.json"
        data = json.loads((tmp_path / path).read_text())
        assert data["run_date"] == RUN_DATE
        assert data["rows"][0]["oov_rate"] is None
        assert list(data["rows"][0]) == COLUMNS

    def test_missing_and_non_finite_cells_become_null(self, store, tmp_path):
        rows = [{"method": "stub", "fit_seconds": float("nan"), "peak_rss_mb": float("inf")}]
        path = write_comparison(rows, store, RUN_DATE)
        # Strict parse: bare NaN/Infinity in the file would raise here.
        text = (tmp_path / path).read_text()
        row = json.loads(text, parse_constant=lambda c: pytest.fail(f"invalid JSON {c}"))["rows"][0]
        assert row["fit_seconds"] is None
        assert row["peak_rss_mb"] is None
        assert row["artifact_mb"] is None


def test_run_dir_mb_empty_dir_is_zero(store):
    assert run_dir_mb(store, "nothing", RUN_DATE) == 0
