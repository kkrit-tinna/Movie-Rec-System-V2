"""DynamoDB item builder and batch writer for T4.2b.

build_items() is pure: catalog + neighbour frames in, plain Python items out,
shaped as in IMPLEMENTATION.md §7. DynamoWriter() sends those items to the
`movies` table with BatchWriteItem, 25 per call, retrying UnprocessedItems
with exponential backoff and full jitter.

Lives under storage/ because §3 allows boto3 only there.
"""
import math
import os
import random
import time
from decimal import Decimal
from pathlib import Path

import boto3
import pandas as pd
import yaml
from boto3.dynamodb.types import TypeSerializer

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"

# Service limits, not tunables: BatchWriteItem takes at most 25 put requests,
# and an item may be at most 400 KB counted the way item_size_bytes counts.
BATCH_SIZE = 25
MAX_ITEM_BYTES = 400 * 1024

OVERVIEW_SHORT_CHARS = 200
SCORE_DECIMALS = 4

MAX_RETRIES_ENV = "MOVIEREC_DYNAMO_MAX_RETRIES"
BASE_DELAY_ENV = "MOVIEREC_DYNAMO_BASE_DELAY"
DEFAULT_MAX_RETRIES = 8
DEFAULT_BASE_DELAY = 0.1

# Indirection so tests can patch the sleep without touching time.sleep globally.
_sleep = time.sleep


def _load_dynamo_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f).get("dynamo") or {}


def _is_missing(value) -> bool:
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
        config = _load_dynamo_config()
        if max_retries is None:
            max_retries = int(
                os.environ.get(MAX_RETRIES_ENV) or config.get("max_retries", DEFAULT_MAX_RETRIES)
            )
        if base_delay is None:
            base_delay = float(
                os.environ.get(BASE_DELAY_ENV) or config.get("base_delay", DEFAULT_BASE_DELAY)
            )
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
