"""Embedding comparison harness (T2.2) and retrieval metrics (T2.3).

T2.2: runs any list of embedders over the same catalog and writes one row
per method to `comparison/{run_date}/comparison.json`. The runner knows
nothing about individual methods: adding one means appending it to the
list, not editing `run_comparison`.

T2.3: scores an existing run -- it never refits. Loads each method's
`{method}/{run_date}/neighbours.parquet`, adds a seeded random baseline, and
writes `comparison/{run_date}/metrics.json`. See IMPLEMENTATION.md T2.3.
"""
import argparse
import io
import math
import re
import threading
import sys
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psutil
import yaml

from movierec.data.ingest import RAW_PATH, load, prepare
from movierec.embedders.base import BaseEmbedder
from movierec.embedders.tfidf import TfidfEmbedder
from movierec.embedders.word2vec import Word2VecEmbedder
from movierec.index.neighbours import compute_neighbours, save_neighbours
from movierec.storage.base import ArtifactStore
from movierec.storage.local import LocalStore

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
SAMPLE_PATH = "data/sample/movies_5k.csv"

COLUMNS = [
    "method",
    "catalog_size",
    "fit_seconds",
    "peak_rss_mb",
    "artifact_mb",
    "oov_rate",
    "median_idf_fallback_rate",
]
COVERAGE_COLUMNS = ["oov_rate", "median_idf_fallback_rate"]

# An entry is either a ready embedder, or a factory that builds one from the
# embedders already fitted earlier in the list, keyed by name. Word2Vec needs
# the fitted TF-IDF's IDF map, so it is passed as a factory after TF-IDF.
EmbedderFactory = Callable[[dict[str, BaseEmbedder]], BaseEmbedder]
MethodSpec = BaseEmbedder | EmbedderFactory

_MB = 1024 * 1024


def _load_eval_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["eval"]


class PeakRss:
    """Peak resident set size of this process while the block runs, in MB.

    Sampled on a background thread: resource.getrusage's ru_maxrss is a
    process-lifetime high-water mark, so a second method would inherit the
    first method's peak. A spike shorter than the interval can be missed.
    """

    def __init__(self, interval: float):
        self.interval = interval
        self._process = psutil.Process()
        self._stop = threading.Event()
        self.peak_bytes = 0

    def _sample(self) -> None:
        self.peak_bytes = max(self.peak_bytes, self._process.memory_info().rss)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._sample()

    def __enter__(self) -> "PeakRss":
        self._sample()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()

    @property
    def peak_mb(self) -> float:
        return self.peak_bytes / _MB


def unigram_idf(tfidf: TfidfEmbedder) -> dict[str, float]:
    """The fitted TF-IDF's IDF weights, bigram keys dropped (T2.1 contract)."""
    vectorizer = tfidf.vectorizer
    return {
        token: float(vectorizer.idf_[column])
        for token, column in vectorizer.vocabulary_.items()
        if " " not in token
    }


def word2vec_after_tfidf(fitted: dict[str, BaseEmbedder]) -> Word2VecEmbedder:
    return Word2VecEmbedder(idf=unigram_idf(fitted[TfidfEmbedder.name]))


def run_dir_mb(store: ArtifactStore, method: str, run_date: str) -> float:
    """Total size of everything under `{method}/{run_date}/`, in MB."""
    paths = store.list(f"{method}/{run_date}")
    return sum(len(store.get_bytes(p)) for p in paths) / _MB


def run_method(
    embedder: BaseEmbedder,
    texts: list[str],
    movie_ids: list,
    store: ArtifactStore,
    run_date: str,
    rss_interval: float,
) -> dict:
    """Fit, build neighbours, and save one method; return its row.

    fit_seconds times fit() alone. peak_rss_mb covers fit, neighbours and
    save, since the neighbour step is where the full-catalog peak lives.
    """
    with PeakRss(rss_interval) as rss:
        start = time.perf_counter()
        embedder.fit(texts, movie_ids)
        fit_seconds = time.perf_counter() - start

        embedder.save(store, run_date)
        neighbours = compute_neighbours(embedder.matrix, embedder.movie_ids)
        save_neighbours(neighbours, store, run_date, method=embedder.name)

    row = {
        "method": embedder.name,
        "catalog_size": len(texts),
        "fit_seconds": fit_seconds,
        "peak_rss_mb": rss.peak_mb,
        "artifact_mb": run_dir_mb(store, embedder.name, run_date),
    }
    row.update({column: None for column in COVERAGE_COLUMNS})
    row.update(embedder.coverage_stats(texts))
    return row


def run_comparison(
    methods: list[MethodSpec],
    texts: list[str],
    movie_ids: list,
    store: ArtifactStore,
    run_date: str,
    rss_interval: float | None = None,
) -> list[dict]:
    """Run each method in list order over the same catalog; one row each."""
    if rss_interval is None:
        rss_interval = _load_eval_config()["rss_sample_seconds"]

    fitted: dict[str, BaseEmbedder] = {}
    rows = []
    for spec in methods:
        embedder = spec if isinstance(spec, BaseEmbedder) else spec(fitted)
        if embedder.name in fitted:
            raise ValueError(f"duplicate method name {embedder.name!r}")
        rows.append(run_method(embedder, texts, movie_ids, store, run_date, rss_interval))
        fitted[embedder.name] = embedder
    return rows


def _null_if_missing(value):
    # NaN and inf are not valid JSON; a missing measurement is a null cell.
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_comparison(rows: list[dict], store: ArtifactStore, run_date: str) -> str:
    """Write rows to `comparison/{run_date}/comparison.json`; return the path.

    Every row carries every column, null where a method has no value.
    """
    table = [
        {column: _null_if_missing(row.get(column)) for column in COLUMNS} for row in rows
    ]
    path = f"comparison/{run_date}/comparison.json"
    store.put_json(path, {"run_date": run_date, "rows": table})
    return path


# --- T2.3: retrieval metrics over an existing run --------------------------

SCORED_METHODS = [TfidfEmbedder.name, Word2VecEmbedder.name]
RANDOM_METHOD = "random"

METRIC_COLUMNS = [
    "method",
    "keyword_jaccard_at_k",
    "genre_precision_at_k",
    "latency_p50_ms",
    "latency_p95_ms",
    "fit_seconds",
    "artifact_mb",
]
CARRIED_COLUMNS = ["fit_seconds", "artifact_mb"]

# Queries whose label set is empty are SKIPPED, not scored as zero. With an
# empty query set every neighbour's Jaccard is 0 (or 0/0), so a zero would
# measure TMDB's keyword coverage (~1 in 7 queries at vote_count >= 50), not
# the embedder. The skip moves the headline number, so metrics.json records
# this rule and how many queries each metric actually scored. A neighbour
# with no labels is NOT skipped: the model chose it, and it scores 0.
EMPTY_LABEL_RULE = "skip"

LATENCY_DEFINITION = (
    "in-process dict lookup of one movie's precomputed top-k list, "
    "built from neighbours.parquet; mirrors the key-lookup serving path"
)

# Same-day sample and full runs would share `{method}/{run_date}/` (§9,
# 2026-09-23), so sample runs live under `{date}-sample` until Phase 4
# settles run_date semantics.
_FULL_RUN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAMPLE_RUN = re.compile(r"^\d{4}-\d{2}-\d{2}-sample$")


def parse_label_set(value) -> frozenset[str]:
    """A comma-separated TMDB label cell (keywords, genres) as a set.

    Missing or blank cells give the empty set.
    """
    if value is None or pd.isna(value):
        return frozenset()
    return frozenset(t for t in (part.strip().lower() for part in str(value).split(",")) if t)


def jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def shares_label(a: frozenset, b: frozenset) -> float:
    return 1.0 if a & b else 0.0


def mean_label_score(
    query_ids: list,
    neighbours: dict,
    labels: dict,
    pair_score: Callable[[frozenset, frozenset], float],
) -> tuple[float | None, int]:
    """Mean over queries of the mean `pair_score` against each neighbour.

    Returns (score, number of queries scored). Queries with no labels are
    skipped -- see EMPTY_LABEL_RULE. None when no query had labels.
    """
    per_query = []
    for query in query_ids:
        query_labels = labels[query]
        if not query_labels:
            continue
        per_query.append(
            np.mean([pair_score(query_labels, labels[n]) for n in neighbours[query]])
        )
    return (float(np.mean(per_query)) if per_query else None), len(per_query)


def keyword_jaccard_at_k(query_ids: list, neighbours: dict, keywords: dict):
    """Primary metric. `keywords` must come from the raw keywords column --
    never from `document`, which deliberately excludes them (T1.3)."""
    return mean_label_score(query_ids, neighbours, keywords, jaccard)


def genre_precision_at_k(query_ids: list, neighbours: dict, genres: dict):
    """Secondary metric: fraction of neighbours sharing >= 1 genre."""
    return mean_label_score(query_ids, neighbours, genres, shares_label)


def sample_queries(catalog: pd.DataFrame, n: int, min_vote_count: int, seed: int) -> list:
    """min(n, eligible) movie ids with vote_count >= min_vote_count.

    Eligible ids are sorted first so the sample depends only on the seed and
    the id set, not on catalog row order.
    """
    eligible = np.sort(catalog.loc[catalog["vote_count"] >= min_vote_count, "id"].to_numpy())
    rng = np.random.default_rng(seed)
    return rng.choice(eligible, size=min(n, len(eligible)), replace=False).tolist()


def random_neighbours(query_ids: list, catalog_ids: list, k: int, seed: int) -> dict:
    """k distinct random catalog movies per query, never the query itself.

    Seeded on a stream separate from sample_queries, so changing one does
    not shift the other.
    """
    catalog_ids = np.sort(np.asarray(catalog_ids))
    position = {movie_id: i for i, movie_id in enumerate(catalog_ids.tolist())}
    rng = np.random.default_rng([seed, 1])
    out = {}
    for query in query_ids:
        picks = rng.choice(len(catalog_ids) - 1, size=k, replace=False)
        picks[picks >= position[query]] += 1  # step over the query's own slot
        out[query] = catalog_ids[picks].tolist()
    return out


def neighbour_lists(neighbours: pd.DataFrame) -> dict:
    """neighbours.parquet rows as {movie_id: [neighbour_id, ...]} in rank order."""
    ordered = neighbours.sort_values(["movie_id", "rank"])
    return ordered.groupby("movie_id")["neighbour_id"].apply(list).to_dict()


def lookup_latency_ms(lookup: dict, query_ids: list) -> tuple[float, float]:
    """p50 and p95 of a single top-k lookup, in milliseconds. See LATENCY_DEFINITION."""
    timings = np.empty(len(query_ids))
    for i, query in enumerate(query_ids):
        start = time.perf_counter_ns()
        _ = lookup[query]
        timings[i] = time.perf_counter_ns() - start
    p50, p95 = np.percentile(timings, [50, 95]) / 1e6
    return float(p50), float(p95)


def latest_run_date(store: ArtifactStore, methods: list[str], sample: bool) -> str:
    """Most recent run_date with a neighbours.parquet for every method.

    Only `{date}-sample` runs are candidates with --sample, only plain
    `{date}` runs without it.
    """
    pattern = _SAMPLE_RUN if sample else _FULL_RUN
    common: set[str] | None = None
    for method in methods:
        dates = {
            path.split("/")[1]
            for path in store.list(method)
            if path.endswith("/neighbours.parquet")
        }
        common = dates if common is None else common & dates
    candidates = sorted(d for d in common or () if pattern.match(d))
    if not candidates:
        kind = "sample" if sample else "full-catalog"
        raise FileNotFoundError(f"no {kind} run with neighbours for all of {methods}")
    return candidates[-1]


def load_neighbours(store: ArtifactStore, method: str, run_date: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(store.get_bytes(f"{method}/{run_date}/neighbours.parquet")))


def load_carried_costs(store: ArtifactStore, run_date: str) -> dict[str, dict]:
    """fit_seconds and artifact_mb per method from T2.2's comparison.json, if any."""
    path = f"comparison/{run_date}/comparison.json"
    if not store.exists(path):
        return {}
    return {
        row["method"]: {column: row.get(column) for column in CARRIED_COLUMNS}
        for row in store.get_json(path)["rows"]
    }


def score_run(
    catalog: pd.DataFrame,
    neighbours_by_method: dict[str, pd.DataFrame],
    costs: dict[str, dict],
    eval_config: dict,
    k: int,
) -> dict:
    """Score each method's saved neighbours plus a random baseline.

    Returns the metrics.json payload minus run_date. Raises if a run's
    neighbours were not built from this catalog (e.g. a full run scored
    against --sample).
    """
    catalog_ids = set(catalog["id"].tolist())
    for method, frame in neighbours_by_method.items():
        run_ids = set(frame["movie_id"].tolist())
        if run_ids != catalog_ids or not set(frame["neighbour_id"].tolist()) <= catalog_ids:
            raise ValueError(
                f"{method} neighbours cover {len(run_ids)} movies but the catalog has "
                f"{len(catalog_ids)}; the run was built from a different catalog"
            )

    keywords = dict(zip(catalog["id"].tolist(), catalog["keywords"].map(parse_label_set)))
    genres = dict(zip(catalog["id"].tolist(), catalog["genres"].map(parse_label_set)))

    seed = eval_config["seed"]
    target = eval_config["n_queries"]
    query_ids = sample_queries(catalog, target, eval_config["query_min_vote_count"], seed)

    lookups = {method: neighbour_lists(frame) for method, frame in neighbours_by_method.items()}
    lookups[RANDOM_METHOD] = random_neighbours(query_ids, list(catalog_ids), k, seed)

    rows = []
    for method, lookup in lookups.items():
        keyword_score, n_keyword = keyword_jaccard_at_k(query_ids, lookup, keywords)
        genre_score, n_genre = genre_precision_at_k(query_ids, lookup, genres)
        row = {column: None for column in METRIC_COLUMNS}
        row.update(
            method=method, keyword_jaccard_at_k=keyword_score, genre_precision_at_k=genre_score
        )
        # The random baseline has no artifact to look up, fit, or size.
        if method != RANDOM_METHOD:
            row["latency_p50_ms"], row["latency_p95_ms"] = lookup_latency_ms(lookup, query_ids)
            row.update(costs.get(method, {}))
        rows.append(row)

    warning = None
    if len(query_ids) < target:
        warning = (
            f"only {len(query_ids)} eligible queries, short of {target}: "
            "smoke-test numbers, not a result"
        )
    return {
        "catalog_size": len(catalog_ids),
        "k": k,
        "seed": seed,
        "query_min_vote_count": eval_config["query_min_vote_count"],
        "n_queries_target": target,
        "n_queries": len(query_ids),
        "n_keyword_queries": n_keyword,
        "n_genre_queries": n_genre,
        "empty_label_rule": EMPTY_LABEL_RULE,
        "latency_definition": LATENCY_DEFINITION,
        "warning": warning,
        "rows": rows,
    }


def write_metrics(payload: dict, store: ArtifactStore, run_date: str) -> str:
    """Write `comparison/{run_date}/metrics.json`, next to comparison.json."""
    rows = [
        {column: _null_if_missing(row.get(column)) for column in METRIC_COLUMNS}
        for row in payload["rows"]
    ]
    path = f"comparison/{run_date}/metrics.json"
    store.put_json(path, {"run_date": run_date, **payload, "rows": rows})
    return path


def _load_k() -> int:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["index"]["k"]


def _compare(args, catalog: pd.DataFrame, store: ArtifactStore) -> None:
    run_date = args.run_date or date.today().isoformat()
    # TF-IDF first: Word2Vec is built from its fitted IDF map.
    methods: list[MethodSpec] = [TfidfEmbedder(), word2vec_after_tfidf]
    rows = run_comparison(
        methods, catalog["document"].tolist(), catalog["id"].tolist(), store, run_date
    )
    path = write_comparison(rows, store, run_date)

    for row in rows:
        print(row)
    print(f"wrote {len(rows)} rows to {path}")


def _score(args, catalog: pd.DataFrame, store: ArtifactStore) -> None:
    run_date = args.run_date or latest_run_date(store, SCORED_METHODS, args.sample)
    neighbours = {method: load_neighbours(store, method, run_date) for method in SCORED_METHODS}
    payload = score_run(
        catalog, neighbours, load_carried_costs(store, run_date), _load_eval_config(), _load_k()
    )
    path = write_metrics(payload, store, run_date)

    print(f"scored run_date={run_date}, {payload['n_queries']} queries "
          f"({payload['n_keyword_queries']} with keywords, empty_label_rule={EMPTY_LABEL_RULE})")
    for row in payload["rows"]:
        print(row)
    print(f"wrote {path}")
    if payload["warning"]:
        print(f"\n*** WARNING: {payload['warning']} ***", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample", action="store_true", help=f"use {SAMPLE_PATH} instead of the full catalog"
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="T2.2: refit every embedder and write comparison.json instead of scoring",
    )
    parser.add_argument(
        "--run-date",
        help="run to score (default: most recent on disk for this catalog); "
        "with --compare, the run to write (default: today)",
    )
    parser.add_argument("--storage-root", default="./artifacts", help="LocalStore root")
    args = parser.parse_args()

    catalog = prepare(load(SAMPLE_PATH if args.sample else RAW_PATH))
    store = LocalStore(root=args.storage_root)
    (_compare if args.compare else _score)(args, catalog, store)


if __name__ == "__main__":
    main()
