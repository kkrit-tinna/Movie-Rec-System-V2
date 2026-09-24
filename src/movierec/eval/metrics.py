"""Embedding comparison harness for T2.2.

Runs any list of embedders over the same catalog and writes one row per
method to `comparison/{run_date}/comparison.json` -- see IMPLEMENTATION.md
T2.2. The runner knows nothing about individual methods: adding one means
appending it to the list, not editing `run_comparison`.

Cost and coverage columns only. T2.3 adds the accuracy metrics to the same
file.
"""
import argparse
import math
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample", action="store_true", help=f"use {SAMPLE_PATH} instead of the full catalog"
    )
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--storage-root", default="./artifacts", help="LocalStore root")
    args = parser.parse_args()

    catalog = prepare(load(SAMPLE_PATH if args.sample else RAW_PATH))
    store = LocalStore(root=args.storage_root)

    # TF-IDF first: Word2Vec is built from its fitted IDF map.
    methods: list[MethodSpec] = [TfidfEmbedder(), word2vec_after_tfidf]
    rows = run_comparison(
        methods, catalog["document"].tolist(), catalog["id"].tolist(), store, args.run_date
    )
    path = write_comparison(rows, store, args.run_date)

    for row in rows:
        print(row)
    print(f"wrote {len(rows)} rows to {path}")


if __name__ == "__main__":
    main()
