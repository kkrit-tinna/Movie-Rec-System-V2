"""Batch entrypoint for T4.3: the eight steps of IMPLEMENTATION.md §2, in order.

1 ingest -> 2 data gates -> 3 fit TF-IDF + Word2Vec, embedding gates ->
4 top-K neighbours -> 5 evaluate -> 6 write artifacts -> 7 DynamoDB ->
8 flip current.json.

Until every gate has passed, the only thing written is
quality/{run_date}/quality_report.json; a failed gate exits non-zero with no
artifacts, no DynamoDB writes and no current.json. current.json is written
last, so a run that dies anywhere earlier leaves the previous run serving.

Every step reuses the module that owns it; this file only orders them.
"""
import argparse
import io
import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import yaml

from movierec.data import quality
from movierec.data.ingest import download, load, prepare
from movierec.embedders.tfidf import TfidfEmbedder
from movierec.embedders.word2vec import Word2VecEmbedder
from movierec.eval.metrics import run_dir_mb, score_run, unigram_idf, write_metrics
from movierec.index.neighbours import compute_neighbours, save_neighbours
from movierec.storage.base import ArtifactStore
from movierec.storage.dynamo import DynamoWriter, build_items
from movierec.storage.factory import get_store
from movierec.storage.local import LocalStore

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.yaml"
CURRENT_PATH = "current.json"
METHODS = (TfidfEmbedder.name, Word2VecEmbedder.name)

log = logging.getLogger("movierec.pipeline")


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(overlay_path: str | None = None) -> dict:
    """default.yaml, with `overlay_path` (e.g. config/sample.yaml) merged over it.

    Only the overlay's keys change; nested blocks merge key by key.
    """
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    if overlay_path:
        with open(overlay_path) as f:
            config = _deep_merge(config, yaml.safe_load(f) or {})
    return config


@contextmanager
def _step(name: str):
    log.info("step %s: start", name)
    start = time.perf_counter()
    yield
    log.info("step %s: done in %.1fs", name, time.perf_counter() - start)


def run(
    input_path: str | None,
    run_date: str,
    store: ArtifactStore,
    config: dict,
    config_overlay: str | None = None,
    skip_dynamo: bool = True,
) -> None:
    """Run all eight steps. Raises quality.QualityGateFailed on a failed gate."""
    gate_config = config["quality"]
    default_method = config["pipeline"]["default_method"]
    if default_method not in METHODS:
        raise ValueError(f"pipeline.default_method {default_method!r} not in {METHODS}")

    with _step("1 ingest"):
        raw = load(input_path or download())
        catalog = prepare(raw, config["catalog"])
    log.info("rows ingested: %d", len(raw))
    log.info("rows in catalog: %d", len(catalog))

    with _step("2 data quality gates"):
        results = [
            quality.gate_row_count_min(len(raw), gate_config),
            quality.gate_row_count_drift(len(raw), store, run_date, gate_config),
            *(
                quality.gate_null_rate(raw[field], field, gate_config)
                for field in gate_config["null_rate_max"]
            ),
            quality.gate_catalog_count_min(len(catalog), gate_config),
            quality.gate_duplicate_id_rate(catalog["id"], gate_config),
        ]
        quality.enforce(results, store, run_date, config_overlay)
    # The raw frame is the largest object in the run and nothing past the
    # gates needs it; drop it before fitting to stay inside 4 GiB (§9 09-18).
    del raw

    texts = catalog["document"].tolist()
    movie_ids = catalog["id"].tolist()
    fit_seconds = {}

    def fit(embedder):
        start = time.perf_counter()
        embedder.fit(texts, movie_ids)
        fit_seconds[embedder.name] = time.perf_counter() - start
        return embedder

    with _step("3 fit embedders + embedding gates"):
        tfidf = fit(TfidfEmbedder(config["tfidf"]))
        # Word2Vec weights by the fitted TF-IDF's IDF, so it is built second.
        word2vec = fit(
            Word2VecEmbedder(
                unigram_idf(tfidf), config=config["word2vec"], tfidf_config=config["tfidf"]
            )
        )
        embedders = [tfidf, word2vec]
        for embedder in embedders:
            for gate in (quality.gate_embedding_nan_rate, quality.gate_zero_vector_rate):
                result = gate(embedder.matrix, gate_config)
                results.append(replace(result, name=f"{embedder.name}.{result.name}"))
        quality.enforce(results, store, run_date, config_overlay)

    index_config = config["index"]
    with _step("4 top-K neighbours"):
        neighbours = {
            e.name: compute_neighbours(
                e.matrix, e.movie_ids, k=index_config["k"], chunk_size=index_config["chunk_size"]
            )
            for e in embedders
        }

    with _step("5 evaluate"):
        costs = {name: {"fit_seconds": seconds} for name, seconds in fit_seconds.items()}
        metrics = score_run(catalog, neighbours, costs, config["eval"], index_config["k"])
    if metrics["warning"]:
        log.warning("metrics: %s", metrics["warning"])

    with _step("6 write artifacts"):
        for embedder in embedders:
            embedder.save(store, run_date)
            save_neighbours(neighbours[embedder.name], store, run_date, method=embedder.name)
        # artifact_mb can only be measured once the run directories exist.
        for row in metrics["rows"]:
            if row["method"] in METHODS:
                row["artifact_mb"] = run_dir_mb(store, row["method"], run_date)
        write_metrics(metrics, store, run_date)

        # Persisted so the DynamoDB load can be rebuilt without re-ingesting (§9 10-02).
        buf = io.BytesIO()
        catalog.to_parquet(buf, index=False)
        store.put_bytes(f"catalog/{run_date}/catalog.parquet", buf.getvalue())

    with _step("7 DynamoDB"):
        if skip_dynamo:
            log.info("DynamoDB write skipped")
        else:
            table = config["dynamo"]["table_name"]
            written = DynamoWriter(table).write(build_items(catalog, neighbours))
            log.info("DynamoDB %s: %s", table, written)

    with _step("8 flip current.json"):
        store.put_json(CURRENT_PATH, {"run_date": run_date, "method": default_method})
    log.info("run %s complete; current.json -> %s", run_date, default_method)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="YYYY-MM-DD (default: today, UTC)",
    )
    parser.add_argument("--input", help="local CSV; when absent, download from Kaggle")
    parser.add_argument(
        "--storage", choices=("local", "s3"), help="overrides STORAGE_BACKEND"
    )
    parser.add_argument(
        "--skip-dynamo", action="store_true", help="skip step 7 (always on for local storage)"
    )
    parser.add_argument(
        "--config", help="overlay merged over config/default.yaml, e.g. config/sample.yaml"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )

    store = get_store(args.storage)
    skip_dynamo = args.skip_dynamo or isinstance(store, LocalStore)
    if skip_dynamo and not args.skip_dynamo:
        log.info("local storage: --skip-dynamo forced on")

    try:
        run(
            args.input,
            args.run_date,
            store,
            load_config(args.config),
            config_overlay=args.config,
            skip_dynamo=skip_dynamo,
        )
    except quality.QualityGateFailed as err:
        for r in err.failed:
            log.error("gate %s failed: observed=%s threshold=%s", r.name, r.observed, r.threshold)
        # Stable token for T4.5's CloudWatch metric filter.
        log.error("quality_gate_failed run_date=%s; only quality_report.json written", args.run_date)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
