from decimal import Decimal

import boto3
import pandas as pd
import pytest
from moto import mock_aws

from movierec.storage import dynamo
from movierec.storage.dynamo import (
    MAX_ITEM_BYTES,
    DynamoWriter,
    build_items,
    item_size_bytes,
)

TABLE = "movies"
N_MOVIES = 12
K = 10


def _catalog() -> pd.DataFrame:
    rows = []
    for i in range(N_MOVIES):
        rows.append(
            {
                "id": 100 + i,
                "title": f"Movie {i}",
                "release_date": f"20{i:02d}-05-01",
                "poster_path": f"/p{i}.jpg",
                "vote_average": 6.5 + i / 10,
                "overview": "x" * 250 if i == 0 else f"overview for movie {i}, long enough to pass",
                "genres": "Action, Science Fiction",
            }
        )
    df = pd.DataFrame(rows)
    df["title"] = df["title"].astype("string")
    df["release_date"] = df["release_date"].astype("string")
    df["poster_path"] = df["poster_path"].astype("string")
    df["genres"] = df["genres"].astype("string")
    # Movie 1 is missing everything optional; NA, NaN and "" all count.
    df.loc[1, "poster_path"] = pd.NA
    df.loc[1, "vote_average"] = float("nan")
    df.loc[1, "release_date"] = pd.NA
    df.loc[1, "genres"] = ""
    return df


def _neighbours(catalog: pd.DataFrame, offset: float) -> pd.DataFrame:
    ids = list(catalog["id"])
    records = []
    for movie_id in ids:
        others = [m for m in ids if m != movie_id][:K]
        for rank, nid in enumerate(others, start=1):
            records.append((movie_id, rank, nid, offset - rank * 0.0123456))
    return pd.DataFrame(records, columns=["movie_id", "rank", "neighbour_id", "score"])


@pytest.fixture
def items():
    catalog = _catalog()
    return build_items(
        catalog, {"tfidf": _neighbours(catalog, 0.5), "word2vec": _neighbours(catalog, 0.95)}
    )


def _walk(value):
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


class TestBuildItems:
    def test_one_item_per_movie(self, items):
        assert len(items) == N_MOVIES
        assert [it["movie_id"] for it in items] == [str(100 + i) for i in range(N_MOVIES)]

    def test_full_item_shape(self, items):
        item = items[2]
        assert set(item) == {
            "movie_id", "title", "year", "poster_path", "vote_average",
            "overview_short", "genres", "similar_tfidf", "similar_word2vec",
        }
        assert item["movie_id"] == "102"
        assert item["title"] == "Movie 2"
        assert item["year"] == Decimal(2002)
        assert item["poster_path"] == "/p2.jpg"
        assert item["vote_average"] == Decimal("6.7")
        assert item["genres"] == ["Action", "Science Fiction"]

    def test_overview_short_is_first_200_chars(self, items):
        assert items[0]["overview_short"] == "x" * 200

    def test_exactly_ten_neighbours_per_method(self, items):
        for item in items:
            for method in ("tfidf", "word2vec"):
                neighbours = item[f"similar_{method}"]
                assert len(neighbours) == K
                assert all(set(n) == {"id", "score"} for n in neighbours)
                assert all(isinstance(n["id"], str) for n in neighbours)
                assert item["movie_id"] not in {n["id"] for n in neighbours}

    def test_neighbours_in_rank_order_with_rounded_scores(self, items):
        first = items[0]["similar_tfidf"][0]
        assert first == {"id": "101", "score": Decimal("0.4877")}

    def test_no_floats_anywhere(self, items):
        for item in items:
            for value in _walk(item):
                assert not isinstance(value, float)

    def test_numbers_are_decimal(self, items):
        item = items[2]
        assert isinstance(item["year"], Decimal)
        assert isinstance(item["vote_average"], Decimal)
        assert all(isinstance(n["score"], Decimal) for n in item["similar_word2vec"])

    def test_missing_and_nan_values_are_left_out(self, items):
        item = items[1]
        for attr in ("poster_path", "vote_average", "year", "genres"):
            assert attr not in item
        for value in _walk(item):
            assert value is not None

    def test_movie_without_neighbours_raises(self):
        catalog = _catalog()
        nbrs = _neighbours(catalog, 0.5)
        nbrs = nbrs[nbrs["movie_id"] != 105]
        with pytest.raises(ValueError, match="105"):
            build_items(catalog, {"tfidf": nbrs})

    def test_oversized_item_raises(self):
        catalog = _catalog()
        catalog["title"] = catalog["title"].astype(object)
        catalog.loc[0, "title"] = "t" * (MAX_ITEM_BYTES + 1)
        with pytest.raises(ValueError, match="limit"):
            build_items(catalog, {"tfidf": _neighbours(catalog, 0.5)})


class TestItemSize:
    def test_counts_names_and_values(self):
        # "movie_id"(8) + "27205"(5); "year"(4) + 2010 -> 4 digits -> 2+1
        assert item_size_bytes({"movie_id": "27205", "year": Decimal(2010)}) == 8 + 5 + 4 + 3

    def test_list_of_maps(self):
        # list 3 + per element (1 + map 3 + "id"(2)+1+"1"(1)) = 3 + 1 + 3 + 1+2+1 = 11
        assert item_size_bytes({"l": [{"id": "1"}]}) == 1 + 3 + 1 + 3 + 1 + 2 + 1


@pytest.fixture
def aws_credentials(monkeypatch):
    # Fake credentials, set before any client exists, so nothing here can
    # authenticate against real AWS even if the mock were bypassed.
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def ddb_client(aws_credentials):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "movie_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "movie_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


class TestDynamoWriterMoto:
    def test_write_then_get_item_round_trip(self, ddb_client, items):
        many = items * 3  # 36 puts -> two batches; duplicate keys land in different batches
        many = [dict(it, movie_id=f"{it['movie_id']}-{n}") for n, it in enumerate(many)]
        summary = DynamoWriter(TABLE, client=ddb_client).write(many)
        assert summary == {"items_written": 36, "batches": 2, "retries": 0}

        from boto3.dynamodb.types import TypeDeserializer

        des = TypeDeserializer()
        got = ddb_client.get_item(TableName=TABLE, Key={"movie_id": {"S": many[2]["movie_id"]}})
        item = {k: des.deserialize(v) for k, v in got["Item"].items()}
        assert item == many[2]
        assert ddb_client.scan(TableName=TABLE, Select="COUNT")["Count"] == 36


class FakeClient:
    """batch_write_item that returns everything unprocessed for the first
    `fail_calls` calls, then succeeds."""

    def __init__(self, fail_calls: int):
        self.fail_calls = fail_calls
        self.calls = []
        self.written = []

    def batch_write_item(self, RequestItems):
        (table, requests), = RequestItems.items()
        self.calls.append(len(requests))
        if len(self.calls) <= self.fail_calls:
            return {"UnprocessedItems": {table: requests}}
        self.written.extend(requests)
        return {"UnprocessedItems": {}}


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(dynamo, "_sleep", calls.append)
    return calls


class TestDynamoWriterRetries:
    def test_backoff_retries_unprocessed_then_succeeds(self, items, sleeps):
        client = FakeClient(fail_calls=1)
        summary = DynamoWriter(TABLE, client=client, max_retries=3, base_delay=0.1).write(items)
        assert summary == {"items_written": N_MOVIES, "batches": 1, "retries": 1}
        assert client.calls == [N_MOVIES, N_MOVIES]
        assert len(client.written) == N_MOVIES
        assert len(sleeps) == 1 and 0 <= sleeps[0] <= 0.1

    def test_gives_up_after_max_retries(self, items, sleeps):
        client = FakeClient(fail_calls=10**6)
        writer = DynamoWriter(TABLE, client=client, max_retries=3, base_delay=0.1)
        with pytest.raises(RuntimeError, match=f"{N_MOVIES} items unwritten"):
            writer.write(items)
        assert len(client.calls) == 1 + 3
        assert len(sleeps) == 3
        # Backoff ceiling doubles each attempt: 0.1, 0.2, 0.4.
        assert all(0 <= s <= 0.1 * 2**i for i, s in enumerate(sleeps))

    def test_unwritten_count_includes_unsent_batches(self, items, sleeps):
        many = items * 3  # 36 -> batches of 25 and 11; the first never succeeds
        client = FakeClient(fail_calls=10**6)
        with pytest.raises(RuntimeError, match="36 items unwritten"):
            DynamoWriter(TABLE, client=client, max_retries=2, base_delay=0.1).write(many)

    def test_settings_come_from_config(self, monkeypatch):
        monkeypatch.delenv(dynamo.MAX_RETRIES_ENV, raising=False)
        monkeypatch.delenv(dynamo.BASE_DELAY_ENV, raising=False)
        writer = DynamoWriter(TABLE, client=FakeClient(0))
        config = dynamo._load_dynamo_config()
        assert writer.max_retries == config["max_retries"]
        assert writer.base_delay == config["base_delay"]

    def test_env_var_overrides_config(self, monkeypatch):
        monkeypatch.setenv(dynamo.MAX_RETRIES_ENV, "2")
        monkeypatch.setenv(dynamo.BASE_DELAY_ENV, "0.5")
        writer = DynamoWriter(TABLE, client=FakeClient(0))
        assert (writer.max_retries, writer.base_delay) == (2, 0.5)
