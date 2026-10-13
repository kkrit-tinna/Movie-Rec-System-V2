"""DynamoDB item builder, batch writer (T4.2b) and reader (T4.4a).

build_items() is pure: catalog + neighbour frames in, plain Python items out,
shaped as in IMPLEMENTATION.md §7. DynamoWriter() sends those items to the
`movies` table with BatchWriteItem, 25 per call, retrying UnprocessedItems
with exponential backoff and full jitter. DynamoReader() serves the API:
GetItem for one movie, BatchGetItem (100 keys per call, UnprocessedKeys
retried the same way) to hydrate its neighbours.

Lives under storage/ because §3 allows boto3 only there. pandas is imported
only inside the build_items path, so the API's DynamoReader import stays
pandas-free (slim image, §9 2026-10-12).
"""
from __future__ import annotations

import logging
import math
import os
import random
import time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
import yaml
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

if TYPE_CHECKING:
    import pandas as pd

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"

# Service limits, not tunables: BatchWriteItem takes at most 25 put requests,
# BatchGetItem at most 100 keys, and an item may be at most 400 KB counted the
# way item_size_bytes counts.
BATCH_SIZE = 25
BATCH_GET_SIZE = 100
MAX_ITEM_BYTES = 400 * 1024

OVERVIEW_SHORT_CHARS = 200
SCORE_DECIMALS = 4

MAX_RETRIES_ENV = "MOVIEREC_DYNAMO_MAX_RETRIES"
BASE_DELAY_ENV = "MOVIEREC_DYNAMO_BASE_DELAY"
TABLE_NAME_ENV = "MOVIEREC_DYNAMO_TABLE"
DEFAULT_MAX_RETRIES = 8
DEFAULT_BASE_DELAY = 0.1
DEFAULT_TABLE_NAME = "movies"

log = logging.getLogger("movierec.storage.dynamo")

# Indirection so tests can patch the sleep without touching time.sleep globally.
_sleep = time.sleep


def _load_dynamo_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f).get("dynamo") or {}


def _retry_settings(max_retries: int | None, base_delay: float | None) -> tuple[int, float]:
    """Argument, then env var, then the yaml `dynamo` block, then code default."""
    config = _load_dynamo_config()
    if max_retries is None:
        max_retries = int(
            os.environ.get(MAX_RETRIES_ENV) or config.get("max_retries", DEFAULT_MAX_RETRIES)
        )
    if base_delay is None:
        base_delay = float(
            os.environ.get(BASE_DELAY_ENV) or config.get("base_delay", DEFAULT_BASE_DELAY)
        )
    return max_retries, base_delay


def _is_missing(value) -> bool:
    import pandas as pd

    if value is None or value is pd.NA:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _year(release_date) -> Decimal | None:
    if _is_missing(release_date):
        return None
    head = str(release_date)[:4]
    return Decimal(head) if head.isdigit() else None


def _genres(genres) -> list[str] | None:
    if _is_missing(genres):
        return None
    parts = [g.strip() for g in str(genres).split(",") if g.strip()]
    return parts or None


def _neighbour_lists(df: pd.DataFrame, method: str) -> dict[str, list[dict]]:
    """movie_id (str) -> [{"id": str, "score": Decimal}, ...] in rank order."""
    df = df.sort_values(["movie_id", "rank"])
    if df["score"].isna().any():
        raise ValueError(f"{method} neighbours contain NaN scores")
    lists: dict[str, list[dict]] = {}
    for movie_id, neighbour_id, score in zip(df["movie_id"], df["neighbour_id"], df["score"]):
        lists.setdefault(str(movie_id), []).append(
            {"id": str(neighbour_id), "score": Decimal(str(round(float(score), SCORE_DECIMALS)))}
        )
    return lists


def _attr_size(value) -> int:
    """Approximate DynamoDB storage size of one attribute value, in bytes."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bool) or value is None:
        return 1
    if isinstance(value, (int, Decimal)):
        digits = Decimal(value).normalize().as_tuple().digits
        return math.ceil(len(digits) / 2) + 1
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, list):
        return 3 + sum(1 + _attr_size(v) for v in value)
    if isinstance(value, dict):
        return 3 + sum(1 + len(k.encode("utf-8")) + _attr_size(v) for k, v in value.items())
    raise TypeError(f"unsupported DynamoDB value type: {type(value).__name__}")


def item_size_bytes(item: dict) -> int:
    """Approximate DynamoDB item size: attribute names plus values.

    Follows the published rules — strings as UTF-8 bytes, numbers at one
    byte per two significant digits plus one, lists and maps at 3 bytes plus
    1 per element, nested map keys counted like attribute names.
    """
    return sum(len(name.encode("utf-8")) + _attr_size(value) for name, value in item.items())


def build_items(catalog: pd.DataFrame, neighbours_by_method: dict[str, pd.DataFrame]) -> list[dict]:
    """One DynamoDB item per catalog movie, shaped as in §7.

    `catalog` is the prepared catalog (ingest.prepare): id, title,
    release_date, poster_path, vote_average, overview, genres.
    `neighbours_by_method` maps a method name ("tfidf", "word2vec") to its
    neighbours frame (movie_id, rank, neighbour_id, score); each becomes a
    `similar_{method}` attribute.

    Numbers are Decimal (boto3 rejects float). Missing or NaN values are
    left out of the item rather than written as None. Raises ValueError on
    a duplicate movie_id, a catalog movie absent from a method's neighbours,
    or an item over DynamoDB's 400 KB limit.
    """
    neighbour_lists = {
        method: _neighbour_lists(df, method) for method, df in neighbours_by_method.items()
    }

    items = []
    seen = set()
    for row in catalog.itertuples(index=False):
        movie_id = str(row.id)
        if movie_id in seen:
            raise ValueError(f"duplicate movie_id {movie_id} in catalog")
        seen.add(movie_id)

        candidates = {
            "title": None if _is_missing(row.title) else str(row.title),
            "year": _year(row.release_date),
            "poster_path": None if _is_missing(row.poster_path) else str(row.poster_path),
            "vote_average": (
                None if _is_missing(row.vote_average) else Decimal(str(float(row.vote_average)))
            ),
            "overview_short": (
                None if _is_missing(row.overview) else str(row.overview)[:OVERVIEW_SHORT_CHARS]
            ),
            "genres": _genres(row.genres),
        }
        item = {"movie_id": movie_id}
        item.update({k: v for k, v in candidates.items() if v is not None})

        for method, lists in neighbour_lists.items():
            if movie_id not in lists:
                raise ValueError(f"movie_id {movie_id} has no {method} neighbours")
            item[f"similar_{method}"] = lists[movie_id]

        size = item_size_bytes(item)
        if size > MAX_ITEM_BYTES:
            raise ValueError(
                f"item for movie_id {movie_id} is {size} bytes, over the {MAX_ITEM_BYTES}-byte limit"
            )
        items.append(item)
    return items


class DynamoWriter:
    """BatchWriteItem loader for one table, using the low-level client.

    Throttling on BatchWriteItem does not raise; the throttled puts come back
    in UnprocessedItems. Those are resent with exponential backoff and full
    jitter, up to max_retries times per batch.

    max_retries and base_delay follow §3's precedence: constructor argument,
    then env var, then config/default.yaml's `dynamo` block, then code default.
    """

    def __init__(
        self,
        table_name: str,
        client=None,
        max_retries: int | None = None,
        base_delay: float | None = None,
    ):
        max_retries, base_delay = _retry_settings(max_retries, base_delay)
        self.table_name = table_name
        self.client = client if client is not None else boto3.client("dynamodb")
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._serializer = TypeSerializer()

    def _to_request(self, item: dict) -> dict:
        return {"PutRequest": {"Item": {k: self._serializer.serialize(v) for k, v in item.items()}}}

    def write(self, items: list[dict]) -> dict:
        """Write `items`; return {"items_written", "batches", "retries"}.

        Raises RuntimeError once a batch still has UnprocessedItems after
        max_retries resends, reporting how many items remain unwritten
        (that batch's leftovers plus every batch not yet sent).
        """
        requests = [self._to_request(item) for item in items]
        written = batches = retries = 0

        for start in range(0, len(requests), BATCH_SIZE):
            pending = requests[start : start + BATCH_SIZE]
            batches += 1
            attempt = 0
            while True:
                response = self.client.batch_write_item(RequestItems={self.table_name: pending})
                unprocessed = response.get("UnprocessedItems", {}).get(self.table_name, [])
                written += len(pending) - len(unprocessed)
                if not unprocessed:
                    break
                if attempt >= self.max_retries:
                    not_sent = len(requests) - (start + BATCH_SIZE)
                    unwritten = len(unprocessed) + max(not_sent, 0)
                    raise RuntimeError(
                        f"{unwritten} items unwritten to {self.table_name} after "
                        f"{self.max_retries} retries ({len(unprocessed)} unprocessed in "
                        f"batch {batches}, {max(not_sent, 0)} not yet sent)"
                    )
                _sleep(random.uniform(0, self.base_delay * 2**attempt))
                attempt += 1
                retries += 1
                pending = unprocessed

        return {"items_written": written, "batches": batches, "retries": retries}


def _plain(value):
    """Deserialized DynamoDB value -> JSON-able Python: Decimal becomes int
    when it has no fractional digits (year 2010), float otherwise (8.4)."""
    if isinstance(value, Decimal):
        return int(value) if value.as_tuple().exponent >= 0 else float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, set):
        return [_plain(v) for v in value]
    return value


class DynamoReader:
    """GetItem / BatchGetItem reader for the API, using the low-level client.

    Reads are eventually consistent (half the cost of strongly consistent;
    the table changes once a week). Items come back with plain int/float in
    place of Decimal, so callers can jsonify them directly.

    table_name: argument, then env MOVIEREC_DYNAMO_TABLE, then the yaml
    `dynamo.table_name`, then code default. Retry settings are shared with
    DynamoWriter.
    """

    def __init__(
        self,
        table_name: str | None = None,
        client=None,
        max_retries: int | None = None,
        base_delay: float | None = None,
    ):
        if table_name is None:
            table_name = (
                os.environ.get(TABLE_NAME_ENV)
                or _load_dynamo_config().get("table_name")
                or DEFAULT_TABLE_NAME
            )
        self.table_name = table_name
        self.client = client if client is not None else boto3.client("dynamodb")
        self.max_retries, self.base_delay = _retry_settings(max_retries, base_delay)
        self._deserializer = TypeDeserializer()

    def _from_dynamo(self, raw: dict) -> dict:
        return {k: _plain(self._deserializer.deserialize(v)) for k, v in raw.items()}

    def get_item(self, movie_id: str) -> dict | None:
        """The item for `movie_id`, or None if the table has no such key."""
        response = self.client.get_item(
            TableName=self.table_name, Key={"movie_id": {"S": str(movie_id)}}
        )
        raw = response.get("Item")
        return self._from_dynamo(raw) if raw else None

    def batch_get(self, ids: list[str]) -> list[dict]:
        """Items for `ids`, in the requested order.

        Keys go out at most BATCH_GET_SIZE per call (duplicates dropped: the
        service rejects a request that repeats a key). UnprocessedKeys are
        resent with exponential backoff and full jitter, up to max_retries
        times per chunk, then RuntimeError. Ids the table does not hold are
        logged and skipped.
        """
        unique = list(dict.fromkeys(str(i) for i in ids))
        found: dict[str, dict] = {}

        for start in range(0, len(unique), BATCH_GET_SIZE):
            pending = [{"movie_id": {"S": i}} for i in unique[start : start + BATCH_GET_SIZE]]
            attempt = 0
            while True:
                response = self.client.batch_get_item(
                    RequestItems={self.table_name: {"Keys": pending}}
                )
                for raw in response.get("Responses", {}).get(self.table_name, []):
                    item = self._from_dynamo(raw)
                    found[item["movie_id"]] = item
                pending = response.get("UnprocessedKeys", {}).get(self.table_name, {}).get("Keys", [])
                if not pending:
                    break
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"{len(pending)} keys unread from {self.table_name} after "
                        f"{self.max_retries} retries"
                    )
                _sleep(random.uniform(0, self.base_delay * 2**attempt))
                attempt += 1

        missing = [i for i in unique if i not in found]
        if missing:
            log.warning("batch_get: %d ids not in %s: %s", len(missing), self.table_name, missing)
        return [found[str(i)] for i in ids if str(i) in found]
