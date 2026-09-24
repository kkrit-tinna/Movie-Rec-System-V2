import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from movierec.index.neighbours import compute_neighbours

MOVIE_IDS = [100, 200, 300, 400, 500]


def _matrix():
    # Row 1 is an exact duplicate of row 0, so it must be row 0's top
    # neighbour and vice versa; rows are intentionally not pre-normalised.
    rows = np.array(
        [
            [1, 1, 0, 0],
            [1, 1, 0, 0],
            [0, 0, 1, 1],
            [0, 0, 1, 0],
            [1, 0, 0, 0],
        ],
        dtype=float,
    )
    return sp.csr_matrix(rows)


class TestComputeNeighbours:
    def test_output_columns(self):
        df = compute_neighbours(_matrix(), MOVIE_IDS, k=2, chunk_size=5)
        assert list(df.columns) == ["movie_id", "rank", "neighbour_id", "score"]

    def test_k_neighbours_per_movie(self):
        df = compute_neighbours(_matrix(), MOVIE_IDS, k=2, chunk_size=5)
        assert (df.groupby("movie_id").size() == 2).all()

    def test_self_never_a_neighbour(self):
        df = compute_neighbours(_matrix(), MOVIE_IDS, k=2, chunk_size=5)
        assert not (df["movie_id"] == df["neighbour_id"]).any()

    def test_ranks_ordered_by_descending_score(self):
        df = compute_neighbours(_matrix(), MOVIE_IDS, k=2, chunk_size=5)
        for _, group in df.groupby("movie_id"):
            group = group.sort_values("rank")
            assert list(group["rank"]) == [1, 2]
            assert group["score"].is_monotonic_decreasing

    def test_identical_document_is_top_neighbour(self):
        df = compute_neighbours(_matrix(), MOVIE_IDS, k=2, chunk_size=5)
        top = df[(df["movie_id"] == 100) & (df["rank"] == 1)]
        assert top["neighbour_id"].item() == 200
        assert top["score"].item() == pytest.approx(1.0)

    def test_chunking_matches_single_chunk(self):
        single = compute_neighbours(_matrix(), MOVIE_IDS, k=2, chunk_size=5)
        chunked = compute_neighbours(_matrix(), MOVIE_IDS, k=2, chunk_size=2)
        pd.testing.assert_frame_equal(
            single.sort_values(["movie_id", "rank"]).reset_index(drop=True),
            chunked.sort_values(["movie_id", "rank"]).reset_index(drop=True),
        )

    def test_k_and_chunk_size_default_from_config(self):
        # config/default.yaml sets k: 10 -- with only 5 rows, k is capped at n-1.
        df = compute_neighbours(_matrix(), MOVIE_IDS)
        assert (df.groupby("movie_id").size() == 4).all()


class TestDenseInput:
    """Word2Vec hands over a dense float16 ndarray, not a sparse matrix."""

    def test_dense_float16_runs(self):
        dense = _matrix().toarray().astype(np.float16)
        df = compute_neighbours(dense, MOVIE_IDS, k=2, chunk_size=2)
        assert (df.groupby("movie_id").size() == 2).all()
        assert not (df["movie_id"] == df["neighbour_id"]).any()
        assert df["score"].dtype == np.float64

    def test_dense_float16_matches_sparse(self):
        sparse_df = compute_neighbours(_matrix(), MOVIE_IDS, k=2, chunk_size=2)
        dense_df = compute_neighbours(
            _matrix().toarray().astype(np.float16), MOVIE_IDS, k=2, chunk_size=2
        )
        key = ["movie_id", "rank"]
        sparse_df = sparse_df.sort_values(key).reset_index(drop=True)
        dense_df = dense_df.sort_values(key).reset_index(drop=True)
        # Exact ties (e.g. 0.0 scores) may order differently; compare scores.
        np.testing.assert_allclose(dense_df["score"], sparse_df["score"], atol=1e-3)
        top = dense_df[(dense_df["movie_id"] == 100) & (dense_df["rank"] == 1)]
        assert top["neighbour_id"].item() == 200

    def test_zero_row_does_not_crash(self):
        # A fully-OOV Word2Vec document is a zero vector.
        dense = _matrix().toarray().astype(np.float16)
        dense[4] = 0
        df = compute_neighbours(dense, MOVIE_IDS, k=2, chunk_size=5)
        assert np.isfinite(df["score"]).all()
