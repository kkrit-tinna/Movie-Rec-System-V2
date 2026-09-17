import numpy as np
import pytest

from movierec.embedders.tfidf import TfidfEmbedder
from movierec.storage.local import LocalStore

CONFIG = {
    "max_features": 100,
    "ngram_range": [1, 2],
    "min_df": 1,
    "max_df": 1.0,
    "sublinear_tf": True,
    "stop_words": "english",
}

TEXTS = [
    "a spy thriller with car chases and betrayal",
    "a romantic comedy set in paris",
    "a spy drama with betrayal and revenge",
    "an animated movie about talking animals",
]
MOVIE_IDS = [10, 20, 30, 40]


def _fitted_embedder():
    embedder = TfidfEmbedder(config=CONFIG)
    embedder.fit(TEXTS, MOVIE_IDS)
    return embedder


class TestTfidfEmbedder:
    def test_reads_params_from_config_not_hardcoded(self):
        embedder = TfidfEmbedder(config=CONFIG)
        assert embedder.vectorizer.max_features == 100
        assert embedder.vectorizer.ngram_range == (1, 2)
        assert embedder.vectorizer.min_df == 1
        assert embedder.vectorizer.max_df == 1.0
        assert embedder.vectorizer.sublinear_tf is True
        assert embedder.vectorizer.stop_words == "english"

    def test_default_config_loads_from_yaml(self):
        embedder = TfidfEmbedder()
        assert embedder.vectorizer.max_features == 50000
        assert embedder.vectorizer.ngram_range == (1, 2)

    def test_fit_transform_shapes(self):
        embedder = _fitted_embedder()
        assert embedder.matrix.shape[0] == len(TEXTS)
        out = embedder.transform(TEXTS)
        assert out.shape == embedder.matrix.shape

    def test_fit_rejects_mismatched_lengths(self):
        embedder = TfidfEmbedder(config=CONFIG)
        with pytest.raises(ValueError):
            embedder.fit(TEXTS, MOVIE_IDS[:-1])

    def test_save_before_fit_raises(self, tmp_path):
        embedder = TfidfEmbedder(config=CONFIG)
        store = LocalStore(root=str(tmp_path / "artifacts"))
        with pytest.raises(RuntimeError):
            embedder.save(store, "2026-09-17")

    def test_save_writes_three_artifacts(self, tmp_path):
        embedder = _fitted_embedder()
        store = LocalStore(root=str(tmp_path / "artifacts"))
        embedder.save(store, "2026-09-17")

        assert store.exists("tfidf/2026-09-17/vectorizer.joblib")
        assert store.exists("tfidf/2026-09-17/matrix.npz")
        assert store.exists("tfidf/2026-09-17/row_index.parquet")

    def test_round_trip_save_load_transform_identical(self, tmp_path):
        embedder = _fitted_embedder()
        before = embedder.transform(TEXTS).toarray()

        store = LocalStore(root=str(tmp_path / "artifacts"))
        embedder.save(store, "2026-09-17")

        loaded = TfidfEmbedder.load(store, "2026-09-17")
        after = loaded.transform(TEXTS).toarray()

        np.testing.assert_array_equal(before, after)
        assert loaded.movie_ids == MOVIE_IDS
        np.testing.assert_array_equal(
            loaded.matrix.toarray(), embedder.matrix.toarray()
        )

    def test_round_trip_preserves_row_index_mapping(self, tmp_path):
        embedder = _fitted_embedder()
        store = LocalStore(root=str(tmp_path / "artifacts"))
        embedder.save(store, "2026-09-17")

        loaded = TfidfEmbedder.load(store, "2026-09-17")
        assert loaded.movie_ids == MOVIE_IDS
