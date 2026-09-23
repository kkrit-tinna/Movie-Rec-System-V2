import numpy as np
import pytest
from gensim.models import KeyedVectors
from sklearn.feature_extraction.text import TfidfVectorizer

from movierec.embedders import word2vec
from movierec.embedders.word2vec import Word2VecEmbedder
from movierec.storage.local import LocalStore

CONFIG = {"model_path": None, "model_name": "glove-wiki-gigaword-100"}
TFIDF_CONFIG = {
    "max_features": 100,
    "ngram_range": [1, 2],
    "min_df": 1,
    "max_df": 1.0,
    "sublinear_tf": True,
    "stop_words": "english",
}

# Tiny stand-in for GloVe: 6 words x 5 dims. Tests never load the real file.
WORDS = ["spy", "thriller", "betrayal", "paris", "comedy", "animals"]
VECTORS = np.array(
    [
        [1.0, 0.0, 0.0, 0.0, 0.0],
        [0.9, 0.1, 0.0, 0.0, 0.0],
        [0.5, 0.0, 0.5, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0, 0.0],
        [0.0, 0.8, 0.0, 0.6, 0.0],
        [0.0, 0.0, 0.0, 0.0, 1.0],
    ],
    dtype=np.float32,
)
VECTOR = dict(zip(WORDS, VECTORS))

# "animals" is deliberately absent, so it falls back to the median IDF.
IDF = {"spy": 1.0, "thriller": 2.0, "betrayal": 3.0, "paris": 5.0, "comedy": 4.0}

TEXTS = [
    "A spy thriller with betrayal",
    "A comedy set in Paris",
    "Talking animals",
    "Zyzzyva quuxly",
]
MOVIE_IDS = [10, 20, 30, 40]


@pytest.fixture
def keyed_vectors():
    kv = KeyedVectors(vector_size=5)
    kv.add_vectors(WORDS, VECTORS)
    return kv


def _embedder(keyed_vectors, idf=IDF):
    return Word2VecEmbedder(
        idf, config=CONFIG, tfidf_config=TFIDF_CONFIG, keyed_vectors=keyed_vectors
    )


def _unit(v):
    return v / np.linalg.norm(v)


class TestDocumentVectors:
    def test_fully_oov_document_is_zero_vector(self, keyed_vectors):
        out = _embedder(keyed_vectors).transform(["zyzzyva quuxly", "", "the and of"])
        assert out.shape == (3, 5)
        assert np.all(out == 0)

    def test_partially_oov_document_uses_only_known_tokens(self, keyed_vectors):
        embedder = _embedder(keyed_vectors)
        out = embedder.transform(["spy zyzzyva quuxly", "spy"])
        np.testing.assert_array_equal(out[0], out[1])
        np.testing.assert_allclose(out[0], _unit(VECTOR["spy"]), atol=1e-3)

    def test_idf_weighting_changes_result(self, keyed_vectors):
        # "paris" is rare (IDF 5) against "spy" (IDF 1).
        out = _embedder(keyed_vectors).transform(["spy paris"])[0]

        unweighted = _unit((VECTOR["spy"] + VECTOR["paris"]) / 2)
        weighted = _unit((1.0 * VECTOR["spy"] + 5.0 * VECTOR["paris"]) / 6.0)

        assert not np.allclose(out, unweighted, atol=1e-2)
        np.testing.assert_allclose(out, weighted, atol=1e-3)

    def test_token_missing_from_idf_map_gets_median_idf(self, keyed_vectors):
        embedder = _embedder(keyed_vectors)
        assert embedder.median_idf == 3.0

        out = embedder.transform(["spy animals"])[0]
        expected = _unit(1.0 * VECTOR["spy"] + 3.0 * VECTOR["animals"])
        np.testing.assert_allclose(out, expected, atol=1e-3)

    def test_non_zero_vectors_have_unit_norm(self, keyed_vectors):
        out = _embedder(keyed_vectors).transform(TEXTS).astype(np.float32)
        norms = np.linalg.norm(out, axis=1)
        non_zero = norms > 0
        assert non_zero.sum() == 3
        np.testing.assert_allclose(norms[non_zero], 1.0, atol=1e-3)

    def test_output_is_float16(self, keyed_vectors):
        out = _embedder(keyed_vectors).transform(TEXTS)
        assert out.dtype == np.float16

    def test_tokens_line_up_with_tfidf_unigram_vocabulary(self, keyed_vectors):
        vectorizer = TfidfVectorizer(
            **{k: tuple(v) if isinstance(v, list) else v for k, v in TFIDF_CONFIG.items()}
        )
        vectorizer.fit(TEXTS)
        unigrams = {t for t in vectorizer.get_feature_names_out() if " " not in t}

        embedder = _embedder(keyed_vectors)
        tokens = {t for text in TEXTS for t in embedder._analyser(text)}
        assert tokens == unigrams


class TestIdfMap:
    def test_rejects_bigram_keys(self, keyed_vectors):
        with pytest.raises(ValueError, match="unigrams only"):
            _embedder(keyed_vectors, idf={**IDF, "spy thriller": 6.0})

    def test_rejects_empty_map(self, keyed_vectors):
        with pytest.raises(ValueError):
            _embedder(keyed_vectors, idf={})


class TestModelSource:
    def test_default_config_loads_from_yaml(self, keyed_vectors):
        embedder = Word2VecEmbedder(IDF, keyed_vectors=keyed_vectors)
        assert embedder.config["model_name"] == "glove-wiki-gigaword-100"
        assert "model_path" in embedder.config
        assert embedder.tfidf_config["stop_words"] == "english"

    def test_model_path_from_config(self, keyed_vectors, tmp_path, monkeypatch):
        monkeypatch.delenv(word2vec.MODEL_PATH_ENV, raising=False)
        path = tmp_path / "tiny.kv"
        keyed_vectors.save(str(path))

        loaded = word2vec.load_keyed_vectors({**CONFIG, "model_path": str(path)})
        assert list(loaded.index_to_key) == WORDS

    def test_env_var_overrides_config_path(self, keyed_vectors, tmp_path, monkeypatch):
        path = tmp_path / "tiny.kv"
        keyed_vectors.save(str(path))
        monkeypatch.setenv(word2vec.MODEL_PATH_ENV, str(path))

        loaded = word2vec.load_keyed_vectors(
            {**CONFIG, "model_path": str(tmp_path / "missing.kv")}
        )
        assert list(loaded.index_to_key) == WORDS

    def test_falls_back_to_downloader_by_name(self, keyed_vectors, monkeypatch):
        import gensim.downloader

        monkeypatch.delenv(word2vec.MODEL_PATH_ENV, raising=False)
        requested = []

        def fake_load(name):
            requested.append(name)
            return keyed_vectors

        monkeypatch.setattr(gensim.downloader, "load", fake_load)

        assert word2vec.load_keyed_vectors(CONFIG) is keyed_vectors
        assert requested == ["glove-wiki-gigaword-100"]


class TestPersistence:
    def test_fit_rejects_mismatched_lengths(self, keyed_vectors):
        with pytest.raises(ValueError):
            _embedder(keyed_vectors).fit(TEXTS, MOVIE_IDS[:-1])

    def test_save_before_fit_raises(self, keyed_vectors, tmp_path):
        store = LocalStore(root=str(tmp_path / "artifacts"))
        with pytest.raises(RuntimeError):
            _embedder(keyed_vectors).save(store, "2026-09-22")

    def test_save_writes_three_artifacts(self, keyed_vectors, tmp_path):
        embedder = _embedder(keyed_vectors)
        embedder.fit(TEXTS, MOVIE_IDS)
        store = LocalStore(root=str(tmp_path / "artifacts"))
        embedder.save(store, "2026-09-22")

        assert store.exists("word2vec/2026-09-22/matrix.npy")
        assert store.exists("word2vec/2026-09-22/row_index.parquet")
        assert store.exists("word2vec/2026-09-22/embedder.json")

    def test_round_trip_save_load_within_float16_tolerance(
        self, keyed_vectors, tmp_path
    ):
        embedder = _embedder(keyed_vectors)
        embedder.fit(TEXTS, MOVIE_IDS)
        store = LocalStore(root=str(tmp_path / "artifacts"))
        embedder.save(store, "2026-09-22")

        loaded = Word2VecEmbedder.load(store, "2026-09-22", keyed_vectors=keyed_vectors)

        assert loaded.movie_ids == MOVIE_IDS
        assert loaded.matrix.dtype == np.float16
        assert loaded.idf == IDF
        assert np.allclose(loaded.matrix, embedder.matrix, atol=1e-3)
        assert np.allclose(loaded.transform(TEXTS), embedder.matrix, atol=1e-3)

    def test_load_does_not_touch_the_model(self, keyed_vectors, tmp_path):
        embedder = _embedder(keyed_vectors)
        embedder.fit(TEXTS, MOVIE_IDS)
        store = LocalStore(root=str(tmp_path / "artifacts"))
        embedder.save(store, "2026-09-22")

        loaded = Word2VecEmbedder.load(store, "2026-09-22")
        assert loaded._keyed_vectors is None
        assert loaded.matrix.shape == (4, 5)
