"""Word2Vec embedder for T2.1.

Document vector = IDF-weighted mean of the document's GloVe word vectors,
L2-normalised and stored as float16 -- see IMPLEMENTATION.md T2.1.

The IDF map is passed in rather than taken from a TfidfEmbedder, so the two
embedders stay interchangeable. It must hold unigram keys only; the caller
drops the bigram keys of the fitted TF-IDF vocabulary before passing it in.
"""
import io
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from gensim.models import KeyedVectors
from sklearn.feature_extraction.text import CountVectorizer

from movierec.embedders.base import BaseEmbedder
from movierec.storage.base import ArtifactStore

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
MODEL_PATH_ENV = "MOVIEREC_WORD2VEC_MODEL_PATH"

# TfidfVectorizer params that decide how text splits into tokens. Copying
# these from the `tfidf` block keeps our tokens identical to the IDF keys.
_TOKENISER_PARAMS = ("lowercase", "strip_accents", "token_pattern", "stop_words")


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _build_analyser(tfidf_config: dict):
    params = {k: tfidf_config[k] for k in _TOKENISER_PARAMS if k in tfidf_config}
    return CountVectorizer(ngram_range=(1, 1), **params).build_analyzer()


def load_keyed_vectors(config: dict) -> KeyedVectors:
    """Resolve the model source: env var -> config `model_path` -> `model_name`."""
    model_path = os.environ.get(MODEL_PATH_ENV) or config.get("model_path")
    if model_path:
        return KeyedVectors.load(str(Path(model_path).expanduser()), mmap="r")

    import gensim.downloader

    return gensim.downloader.load(config["model_name"])


class Word2VecEmbedder(BaseEmbedder):
    name = "word2vec"

    def __init__(
        self,
        idf: dict[str, float],
        config: dict | None = None,
        tfidf_config: dict | None = None,
        keyed_vectors: KeyedVectors | None = None,
    ):
        if not idf:
            raise ValueError("idf map is empty")
        bigrams = [token for token in idf if " " in token]
        if bigrams:
            raise ValueError(
                f"idf map must be unigrams only; got {len(bigrams)} bigram keys, "
                f"e.g. {bigrams[0]!r}"
            )

        if config is None or tfidf_config is None:
            full_config = _load_config()
            config = config or full_config["word2vec"]
            tfidf_config = tfidf_config or full_config["tfidf"]

        self.config = config
        self.tfidf_config = tfidf_config
        self.idf = dict(idf)
        self.median_idf = float(np.median(list(self.idf.values())))
        self._analyser = _build_analyser(tfidf_config)
        self._keyed_vectors = keyed_vectors
        self.matrix: np.ndarray | None = None
        self.movie_ids: list | None = None

    @property
    def keyed_vectors(self) -> KeyedVectors:
        # Loaded on first use, so load() can read a saved matrix without the model.
        if self._keyed_vectors is None:
            self._keyed_vectors = load_keyed_vectors(self.config)
        return self._keyed_vectors

    def _embed(self, text: str) -> np.ndarray:
        kv = self.keyed_vectors
        tokens = [t for t in self._analyser(text) if t in kv.key_to_index]
        if not tokens:
            return np.zeros(kv.vector_size, dtype=np.float32)

        weights = np.array(
            [self.idf.get(t, self.median_idf) for t in tokens], dtype=np.float32
        )
        rows = kv.vectors[[kv.key_to_index[t] for t in tokens]].astype(np.float32)
        mean = weights @ rows / weights.sum()

        norm = np.linalg.norm(mean)
        if norm == 0:
            return np.zeros(kv.vector_size, dtype=np.float32)
        return mean / norm

    def fit(self, texts: list[str], movie_ids: list) -> None:
        if len(texts) != len(movie_ids):
            raise ValueError("texts and movie_ids must be the same length")
        self.matrix = self.transform(texts)
        self.movie_ids = list(movie_ids)

    def transform(self, texts: list[str]) -> np.ndarray:
        vector_size = self.keyed_vectors.vector_size
        out = np.zeros((len(texts), vector_size), dtype=np.float16)
        for i, text in enumerate(texts):
            out[i] = self._embed(text)
        return out

    def save(self, store: ArtifactStore, run_date: str) -> None:
        if self.matrix is None or self.movie_ids is None:
            raise RuntimeError("fit() must be called before save()")

        prefix = f"{self.name}/{run_date}"

        matrix_buf = io.BytesIO()
        np.save(matrix_buf, self.matrix, allow_pickle=False)
        store.put_bytes(f"{prefix}/matrix.npy", matrix_buf.getvalue())

        row_index = pd.DataFrame(
            {"row": range(len(self.movie_ids)), "movie_id": self.movie_ids}
        )
        row_index_buf = io.BytesIO()
        row_index.to_parquet(row_index_buf, index=False)
        store.put_bytes(f"{prefix}/row_index.parquet", row_index_buf.getvalue())

        store.put_json(
            f"{prefix}/embedder.json",
            {
                "idf": self.idf,
                "config": self.config,
                "tfidf_config": self.tfidf_config,
            },
        )

    @classmethod
    def load(
        cls,
        store: ArtifactStore,
        run_date: str,
        keyed_vectors: KeyedVectors | None = None,
    ) -> "Word2VecEmbedder":
        prefix = f"{cls.name}/{run_date}"

        state = store.get_json(f"{prefix}/embedder.json")
        embedder = cls(
            idf=state["idf"],
            config=state["config"],
            tfidf_config=state["tfidf_config"],
            keyed_vectors=keyed_vectors,
        )

        matrix_buf = io.BytesIO(store.get_bytes(f"{prefix}/matrix.npy"))
        embedder.matrix = np.load(matrix_buf, allow_pickle=False)

        row_index_buf = io.BytesIO(store.get_bytes(f"{prefix}/row_index.parquet"))
        row_index = pd.read_parquet(row_index_buf)
        embedder.movie_ids = row_index.sort_values("row")["movie_id"].tolist()

        return embedder
