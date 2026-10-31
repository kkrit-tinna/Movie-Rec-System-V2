"""Serves one local run's artifacts to the API, with no DynamoDB (T4.6).

LocalReader is a drop-in for DynamoReader: it builds the same items the batch
job would load into the table (build_items) and passes them through the same
conversion DynamoReader applies on the way out (_plain), so get_item and
batch_get return the same shapes and Python types.

One known difference from the live table: real DynamoDB trims trailing zeros
from numbers, so it returns a whole rating of 8.0 as int 8, where this reader
(and moto) returns float 8.0. Both are the same number in JSON to the page.

pandas is imported only in __init__, so importing this module stays cheap and
the API's entry modules stay free of batch dependencies (slim image).
"""
from __future__ import annotations

import io
import logging

from movierec.storage.base import ArtifactStore
from movierec.storage.dynamo import _plain, build_items

log = logging.getLogger("movierec.storage.local_reader")


class LocalReader:
    """get_item / batch_get over catalog/{run_date}/catalog.parquet and each
    method's {method}/{run_date}/neighbours.parquet, indexed by movie_id.

    `catalog` is kept, so the caller can build the title index from it.
    """

    def __init__(self, store: ArtifactStore, run_date: str, methods: tuple[str, ...]):
        import pandas as pd

        def read(path: str) -> pd.DataFrame:
            return pd.read_parquet(io.BytesIO(store.get_bytes(path)))

        self.catalog = read(f"catalog/{run_date}/catalog.parquet")
        neighbours = {m: read(f"{m}/{run_date}/neighbours.parquet") for m in methods}
        self._items = {
            item["movie_id"]: _plain(item) for item in build_items(self.catalog, neighbours)
        }

    def get_item(self, movie_id: str) -> dict | None:
        """The item for `movie_id`, or None if the run has no such movie."""
        return self._items.get(str(movie_id))

    def batch_get(self, ids: list[str]) -> list[dict]:
        """Items for `ids`, in the requested order; unknown ids are logged and
        skipped, as in DynamoReader.batch_get."""
        missing = [i for i in dict.fromkeys(str(i) for i in ids) if i not in self._items]
        if missing:
            log.warning("batch_get: %d ids not in this run: %s", len(missing), missing)
        return [self._items[str(i)] for i in ids if str(i) in self._items]
