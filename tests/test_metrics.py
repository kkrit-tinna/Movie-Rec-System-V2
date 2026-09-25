import json

import numpy as np
import pandas as pd
import pytest
from gensim.models import KeyedVectors

from movierec.embedders.base import BaseEmbedder
from movierec.embedders.tfidf import TfidfEmbedder
from movierec.embedders.word2vec import Word2VecEmbedder
from movierec.eval.metrics import (
    COLUMNS,
    METRIC_COLUMNS,
    genre_precision_at_k,
    jaccard,
    keyword_jaccard_at_k,
    latest_run_date,
    parse_label_set,
    random_neighbours,
    run_comparison,
    run_dir_mb,
    sample_queries,
    score_run,
    unigram_idf,
    write_comparison,
    write_metrics,
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


# --- T2.3 metrics -----------------------------------------------------------

EVAL_CONFIG = {"n_queries": 2000, "query_min_vote_count": 50, "seed": 42}


def _labels(**sets):
    return {int(k[1:]): frozenset(v) for k, v in sets.items()}


class TestParseLabelSet:
    def test_splits_strips_and_lowercases(self):
        assert parse_label_set("Time Travel, hotel ,  , Beach") == {"time travel", "hotel", "beach"}

    @pytest.mark.parametrize("cell", [None, pd.NA, float("nan"), "", " , "])
    def test_missing_cell_is_empty(self, cell):
        assert parse_label_set(cell) == frozenset()


class TestKeywordJaccard:
    def test_hand_computed_example(self):
        # q1 = {a,b,c}; neighbours:
        #   n2 = {a,b}   -> 2/3
        #   n3 = {c,d}   -> 1/4
        #   n4 = {}      -> 0   (empty NEIGHBOUR still counts)
        # q1 mean = (2/3 + 1/4 + 0) / 3 = 11/36
        # q2 = {a,b}; neighbour n1 = {a,b,c} -> 2/3
        # overall = (11/36 + 2/3) / 2 = 35/72
        keywords = _labels(m1="abc", m2="ab", m3="cd", m4="")
        neighbours = {1: [2, 3, 4], 2: [1]}
        score, n = keyword_jaccard_at_k([1, 2], neighbours, keywords)
        assert score == pytest.approx(35 / 72)
        assert n == 2

    def test_jaccard_pairs(self):
        assert jaccard(frozenset("abc"), frozenset("ab")) == pytest.approx(2 / 3)
        assert jaccard(frozenset("ab"), frozenset("cd")) == 0.0

    def test_query_with_no_keywords_is_skipped_not_zero(self):
        # Rule: EMPTY_LABEL_RULE = "skip". Query 4 has no keywords; scored as
        # zero it would halve the mean to 1/3, skipped the mean stays 2/3.
        keywords = _labels(m1="abc", m2="ab", m4="")
        neighbours = {1: [2], 4: [1, 2]}
        score, n = keyword_jaccard_at_k([1, 4], neighbours, keywords)
        assert score == pytest.approx(2 / 3)
        assert n == 1

    def test_all_queries_empty_gives_null(self):
        keywords = _labels(m1="", m2="ab")
        assert keyword_jaccard_at_k([1], {1: [2]}, keywords) == (None, 0)


class TestGenrePrecision:
    def test_tiny_example(self):
        # q1 = {drama, crime}; neighbours share: crime yes, comedy no,
        # drama yes, none no -> 2/4. q2 = {comedy}; n1 no, n3 yes -> 1/2.
        genres = {
            1: frozenset({"drama", "crime"}),
            2: frozenset({"crime"}),
            3: frozenset({"comedy"}),
            4: frozenset({"drama", "thriller"}),
            5: frozenset(),
        }
        neighbours = {1: [2, 3, 4, 5], 3: [1, 3]}
        score, n = genre_precision_at_k([1, 3], neighbours, genres)
        assert score == pytest.approx((2 / 4 + 1 / 2) / 2)
        assert n == 2


def _catalog(n=300):
    rng = np.random.default_rng(7)
    ids = np.arange(1000, 1000 + n)
    return pd.DataFrame(
        {
            "id": ids,
            "vote_count": rng.integers(0, 200, size=n),
            "keywords": [f"k{i % 7}, k{i % 11}" if i % 5 else pd.NA for i in range(n)],
            "genres": [f"g{i % 3}" for i in range(n)],
        }
    )


class TestSampleQueries:
    def test_same_seed_same_sample(self):
        catalog = _catalog()
        first = sample_queries(catalog, 50, 50, seed=42)
        second = sample_queries(catalog, 50, 50, seed=42)
        assert first == second
        assert len(set(first)) == 50

    def test_independent_of_row_order(self):
        catalog = _catalog()
        shuffled = catalog.sample(frac=1, random_state=3)
        assert sample_queries(catalog, 50, 50, 42) == sample_queries(shuffled, 50, 50, 42)

    def test_different_seed_different_sample(self):
        catalog = _catalog()
        assert sample_queries(catalog, 50, 50, 1) != sample_queries(catalog, 50, 50, 2)

    def test_only_eligible_and_capped_at_eligible(self):
        catalog = _catalog()
        eligible = set(catalog.loc[catalog["vote_count"] >= 50, "id"])
        queries = sample_queries(catalog, 10_000, 50, 42)
        assert set(queries) == eligible


class TestRandomNeighbours:
    def test_k_distinct_never_self_and_seeded(self):
        ids = list(range(20))
        first = random_neighbours([0, 5, 19], ids, k=10, seed=42)
        assert first == random_neighbours([0, 5, 19], ids, k=10, seed=42)
        for query, picks in first.items():
            assert len(set(picks)) == 10
            assert query not in picks
            assert set(picks) <= set(ids)


def _neighbour_frame(catalog, k=10, offset=1):
    ids = catalog["id"].tolist()
    records = [
        (movie_id, rank, ids[(i + offset * rank) % len(ids)], 1.0 / rank)
        for i, movie_id in enumerate(ids)
        for rank in range(1, k + 1)
    ]
    return pd.DataFrame(records, columns=["movie_id", "rank", "neighbour_id", "score"])


class TestScoreRun:
    def test_rows_columns_and_short_query_warning(self, store, tmp_path):
        catalog = _catalog()
        frames = {"tfidf": _neighbour_frame(catalog), "word2vec": _neighbour_frame(catalog, offset=2)}
        costs = {"tfidf": {"fit_seconds": 4.6, "artifact_mb": 34.2}}
        payload = score_run(catalog, frames, costs, EVAL_CONFIG, k=10)

        assert [r["method"] for r in payload["rows"]] == ["tfidf", "word2vec", "random"]
        assert payload["n_queries"] < 2000
        assert payload["warning"] and "smoke-test" in payload["warning"]
        assert payload["empty_label_rule"] == "skip"
        assert payload["n_keyword_queries"] < payload["n_queries"]

        tfidf, w2v, rand = payload["rows"]
        assert tfidf["fit_seconds"] == 4.6 and tfidf["artifact_mb"] == 34.2
        assert w2v["fit_seconds"] is None
        for row in payload["rows"]:
            assert 0 <= row["keyword_jaccard_at_k"] <= 1
            assert 0 <= row["genre_precision_at_k"] <= 1
        assert tfidf["latency_p50_ms"] <= tfidf["latency_p95_ms"]
        assert rand["latency_p50_ms"] is None and rand["fit_seconds"] is None

        path = write_metrics(payload, store, RUN_DATE)
        assert path == f"comparison/{RUN_DATE}/metrics.json"
        data = json.loads((tmp_path / path).read_text())
        assert data["run_date"] == RUN_DATE
        assert all(list(row) == METRIC_COLUMNS for row in data["rows"])

    def test_no_warning_at_full_query_count(self):
        catalog = _catalog()
        config = {**EVAL_CONFIG, "n_queries": 20}
        payload = score_run(catalog, {"tfidf": _neighbour_frame(catalog)}, {}, config, k=10)
        assert payload["n_queries"] == 20
        assert payload["warning"] is None

    def test_run_from_another_catalog_is_rejected(self):
        catalog = _catalog()
        frame = _neighbour_frame(_catalog(n=100))
        with pytest.raises(ValueError, match="different catalog"):
            score_run(catalog, {"tfidf": frame}, {}, EVAL_CONFIG, k=10)


class TestLatestRunDate:
    def _touch(self, store, method, run_date):
        store.put_bytes(f"{method}/{run_date}/neighbours.parquet", b"x")

    def test_picks_latest_common_run_for_catalog_kind(self, store):
        for run_date in ("2026-09-18", "2026-09-23", "2026-09-23-sample"):
            self._touch(store, "tfidf", run_date)
        for run_date in ("2026-09-23", "2026-09-23-sample"):
            self._touch(store, "word2vec", run_date)
        self._touch(store, "tfidf", "2026-09-30")  # word2vec missing: not a candidate
        methods = ["tfidf", "word2vec"]
        assert latest_run_date(store, methods, sample=False) == "2026-09-23"
        assert latest_run_date(store, methods, sample=True) == "2026-09-23-sample"

    def test_no_run_raises(self, store):
        with pytest.raises(FileNotFoundError):
            latest_run_date(store, ["tfidf"], sample=True)
