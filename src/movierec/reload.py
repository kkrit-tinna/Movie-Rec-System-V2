"""Reload DynamoDB from an earlier run's artifacts: the rollback (T4.7).

    python -m movierec.reload --run-date YYYY-MM-DD [--dry-run]

Serving reads DynamoDB directly, and nothing reads current.json, so going back
to an earlier run means writing that run's items into the table again. Only
after a successful write does current.json move to the reloaded run.

The store comes from get_store() (STORAGE_BACKEND, MOVIEREC_S3_BUCKET), like
the pipeline. As in the pipeline, a local store never writes to DynamoDB, so a
real reload refuses it: forgetting STORAGE_BACKEND=s3 must not load the live
table from a laptop's artifacts/. --dry-run works with either.

It refuses a run whose catalog or neighbours are missing, or whose quality
report is missing, empty or shows a failed gate.

Limit: it overwrites items but does not delete items missing from the older
run, so a film added since then stays in the table. That is harmless while the
catalog is frozen (IMPLEMENTATION.md §3).
"""
import argparse
import io
import math
import sys

import pandas as pd

from movierec.pipeline import CURRENT_PATH, METHODS, load_config
from movierec.storage.base import ArtifactStore
from movierec.storage.dynamo import DynamoWriter, build_items, item_size_bytes
from movierec.storage.factory import get_store
from movierec.storage.local import LocalStore

WCU_BYTES = 1024


class ReloadRefused(Exception):
    """The run must not be loaded; the message says why."""


def _check_run(store: ArtifactStore, run_date: str) -> None:
    paths = [f"catalog/{run_date}/catalog.parquet"]
    paths += [f"{method}/{run_date}/neighbours.parquet" for method in METHODS]
    missing = [path for path in paths if not store.exists(path)]
    if missing:
        raise ReloadRefused(f"run {run_date} is missing: {', '.join(missing)}")

    report_path = f"quality/{run_date}/quality_report.json"
    if not store.exists(report_path):
        raise ReloadRefused(f"run {run_date} is missing: {report_path}")
    gates = store.get_json(report_path).get("gates")
    if not gates:
        raise ReloadRefused(f"run {run_date}: {report_path} records no gates")
    failed = [gate["name"] for gate in gates if not gate["passed"]]
    if failed:
        raise ReloadRefused(f"run {run_date} failed quality gates: {', '.join(failed)}")


def _read_parquet(store: ArtifactStore, path: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(store.get_bytes(path)))


def wcu_total(items: list[dict]) -> int:
    """Write units for one full load: each item rounded up to a whole KB."""
    return sum(math.ceil(item_size_bytes(item) / WCU_BYTES) for item in items)


def reload_run(store: ArtifactStore, run_date: str, config: dict, dry_run: bool = False) -> None:
    """Check the run, build its items, write them, then move current.json.

    Raises ReloadRefused before anything is written. A failed write raises
    too, and leaves current.json where it was.
    """
    if not dry_run and isinstance(store, LocalStore):
        raise ReloadRefused(
            "a local store never writes to DynamoDB: set STORAGE_BACKEND=s3 and "
            "MOVIEREC_S3_BUCKET, or use --dry-run"
        )
    _check_run(store, run_date)

    catalog = _read_parquet(store, f"catalog/{run_date}/catalog.parquet")
    neighbours = {m: _read_parquet(store, f"{m}/{run_date}/neighbours.parquet") for m in METHODS}
    items = build_items(catalog, neighbours)

    if dry_run:
        print(f"run date: {run_date}")
        print(f"items: {len(items):,}")
        print(f"WCU total: {wcu_total(items):,}")
        print("dry run: nothing written")
        return

    table = config["dynamo"]["table_name"]
    written = DynamoWriter(table).write(items)
    print(f"DynamoDB {table}: {written}")
    method = config["pipeline"]["default_method"]
    store.put_json(CURRENT_PATH, {"run_date": run_date, "method": method})
    print(f"current.json -> {run_date}, {method}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reload DynamoDB from an earlier run's artifacts (rollback)."
    )
    parser.add_argument("--run-date", required=True, help="YYYY-MM-DD of the run to load")
    parser.add_argument(
        "--dry-run", action="store_true", help="print run date, item count and WCU; write nothing"
    )
    args = parser.parse_args(argv)

    try:
        reload_run(get_store(), args.run_date, load_config(), dry_run=args.dry_run)
    except ReloadRefused as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
