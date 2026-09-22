"""TF-IDF embedder for T1.4.

Wraps sklearn's TfidfVectorizer with every parameter read from the `tfidf`
block in config/default.yaml -- see IMPLEMENTATION.md T1.4.
"""
import argparse
import io
from datetime import date
from pathlib import Path

import joblib
import pandas as pd
import scipy.sparse as sp
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer

from movierec.embedders.base import BaseEmbedder
from movierec.storage.base import ArtifactStore

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"


def _load_tfidf_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["tfidf"]


class TfidfEmbedder(BaseEmbedder):
    name = "tfidf"

    def __init__(self, config: dict | None = None):
        self.config = config or _load_tfidf_config()
        # YAML has no tuple type; sklearn wants tuples for list-valued params
        # (e.g. an n-gram range), so convert generically rather than by name.
        params = {
            key: (tuple(value) if isinstance(value, list) else value)
            for key, value in self.config.items()
        }
        self.vectorizer = TfidfVectorizer(**params)
        self.matrix: sp.csr_matrix | None = None
        self.movie_ids: list | None = None

    def fit(self, texts: list[str], movie_ids: list) -> None:
        if len(texts) != len(movie_ids):
            raise ValueError("texts and movie_ids must be the same length")
        self.matrix = self.vectorizer.fit_transform(texts)
        self.movie_ids = list(movie_ids)

    def transform(self, texts: list[str]):
        return self.vectorizer.transform(texts)

    def save(self, store: ArtifactStore, run_date: str) -> None:
        if self.matrix is None or self.movie_ids is None:
            raise RuntimeError("fit() must be called before save()")

        prefix = f"{self.name}/{run_date}"

        vectorizer_buf = io.BytesIO()
        joblib.dump(self.vectorizer, vectorizer_buf)
        store.put_bytes(f"{prefix}/vectorizer.joblib", vectorizer_buf.getvalue())

        matrix_buf = io.BytesIO()
        sp.save_npz(matrix_buf, self.matrix)
        store.put_bytes(f"{prefix}/matrix.npz", matrix_buf.getvalue())

        row_index = pd.DataFrame(
            {"row": range(len(self.movie_ids)), "movie_id": self.movie_ids}
        )
        row_index_buf = io.BytesIO()
        row_index.to_parquet(row_index_buf, index=False)
        store.put_bytes(f"{prefix}/row_index.parquet", row_index_buf.getvalue())

    @classmethod
    def load(cls, store: ArtifactStore, run_date: str) -> "TfidfEmbedder":
        prefix = f"{cls.name}/{run_date}"

        embedder = cls()
        vectorizer_buf = io.BytesIO(store.get_bytes(f"{prefix}/vectorizer.joblib"))
        embedder.vectorizer = joblib.load(vectorizer_buf)

        matrix_buf = io.BytesIO(store.get_bytes(f"{prefix}/matrix.npz"))
        embedder.matrix = sp.load_npz(matrix_buf)

        row_index_buf = io.BytesIO(store.get_bytes(f"{prefix}/row_index.parquet"))
        row_index = pd.read_parquet(row_index_buf)
        embedder.movie_ids = row_index.sort_values("row")["movie_id"].tolist()

        return embedder


def main() -> None:
    from movierec.data.ingest import load, prepare
    from movierec.storage.local import LocalStore

    parser = argparse.ArgumentParser(
        description="Fit a TfidfEmbedder on --input and save it via LocalStore."
    )
    parser.add_argument("--input", required=True, help="already-downloaded CSV path")
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--storage-root", default="./artifacts", help="LocalStore root")
    args = parser.parse_args()

    catalog = prepare(load(args.input))

    embedder = TfidfEmbedder()
    embedder.fit(catalog["document"].tolist(), catalog["id"].tolist())

    store = LocalStore(root=args.storage_root)
    embedder.save(store, args.run_date)

    print(f"fit and saved tfidf embedder for {len(catalog)} rows, run_date={args.run_date}")


if __name__ == "__main__":
    main()
