"""Top-K neighbour computation for T1.5.

Cosine similarity is computed in chunks of `chunk_size` query rows against
the full matrix. A full N x N dense similarity matrix does not fit in
memory at catalog scale, so each chunk (chunk_size x N) is densified,
reduced to its top K+1, and discarded before the next chunk starts -- see
IMPLEMENTATION.md T1.5.
"""
import argparse
import io
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
import yaml
from sklearn.preprocessing import normalize

from movierec.embedders.tfidf import TfidfEmbedder
from movierec.storage.base import ArtifactStore
from movierec.storage.local import LocalStore

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"


def _load_index_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["index"]


def _top_k_for_row(sim_row: np.ndarray, self_idx: int, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Top K+1 neighbours by similarity, then drop the self-match."""
    k_plus = min(k + 1, sim_row.shape[0])
    top_idx = np.argpartition(-sim_row, k_plus - 1)[:k_plus]
    order = np.argsort(-sim_row[top_idx])
    top_idx = top_idx[order]
    top_idx = top_idx[top_idx != self_idx][:k]
    return top_idx, sim_row[top_idx]


def compute_neighbours(
    matrix: sp.spmatrix | np.ndarray,
    movie_ids: list,
    k: int | None = None,
    chunk_size: int | None = None,
) -> pd.DataFrame:
    """Top-K neighbours for every row of `matrix`, by cosine similarity.

    Rows are L2-normalised here (idempotent if already normalised, as
    TfidfVectorizer's default output is) so that a plain dot product
    between rows equals cosine similarity.

    Sparse input (TF-IDF) and dense input (Word2Vec, float16) take separate
    paths: .tocsr() and .todense() exist only on sparse matrices. Dense input
    is cast to float32 before the matmul for the same memory reason as the
    sparse path, and because numpy has no BLAS path for float16.
    """
    config = _load_index_config()
    k = k if k is not None else config["k"]
    chunk_size = chunk_size if chunk_size is not None else config["chunk_size"]

    n = matrix.shape[0]
    movie_ids = np.asarray(movie_ids)
    # float32 before the matmul, not after -- casting a float64 dense block
    # down to float32 still pays the float64 block's peak memory first.
    is_sparse = sp.issparse(matrix)
    if is_sparse:
        normed = normalize(matrix, norm="l2", axis=1).astype(np.float32).tocsr()
        normed_t = normed.T.tocsr()
    else:
        normed = normalize(np.asarray(matrix, dtype=np.float32), norm="l2", axis=1)
        normed_t = normed.T

    records = []
    for start in range(0, n, chunk_size):
        end = min(start + chunk_size, n)
        if is_sparse:
            chunk_sim = np.asarray((normed[start:end] @ normed_t).todense(), dtype=np.float32)
        else:
            chunk_sim = normed[start:end] @ normed_t

        for local_i in range(end - start):
            global_i = start + local_i
            top_idx, scores = _top_k_for_row(chunk_sim[local_i], global_i, k)
            for rank, (neighbour_pos, score) in enumerate(zip(top_idx, scores), start=1):
                records.append(
                    (movie_ids[global_i], rank, movie_ids[neighbour_pos], float(score))
                )

    return pd.DataFrame(records, columns=["movie_id", "rank", "neighbour_id", "score"])


def save_neighbours(
    df: pd.DataFrame, store: ArtifactStore, run_date: str, method: str = "tfidf"
) -> None:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    store.put_bytes(f"{method}/{run_date}/neighbours.parquet", buf.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-date", required=True, help="run_date of an already-saved tfidf embedder"
    )
    parser.add_argument("--storage-root", default="./artifacts", help="LocalStore root")
    args = parser.parse_args()

    store = LocalStore(root=args.storage_root)
    embedder = TfidfEmbedder.load(store, args.run_date)
    neighbours = compute_neighbours(embedder.matrix, embedder.movie_ids)
    save_neighbours(neighbours, store, args.run_date)

    print(f"wrote {len(neighbours)} neighbour rows for run_date={args.run_date}")


if __name__ == "__main__":
    main()
