"""LocalReader must be indistinguishable from DynamoReader to the API."""
import io
from pathlib import Path

import boto3
import pandas as pd
import pytest
from flask import Flask
from moto import mock_aws

from movierec.api import __main__ as api_main
from movierec.api.app import METHODS
from movierec.data.ingest import load, prepare
from movierec.index.neighbours import save_neighbours
from movierec.index.titles import build_title_index
from movierec.storage.dynamo import DynamoReader, DynamoWriter, build_items
from movierec.storage.local import LocalStore
from movierec.storage.local_reader import LocalReader

REPO = Path(__file__).resolve().parents[1]
DEMO_SAMPLE = REPO / "data" / "sample" / "movies_demo.csv"
RUN_DATE = "2026-10-31"
TABLE = "movies"
K = 10


def _catalog() -> pd.DataFrame:
    rows = [
        {
            "id": 100 + i,
            "title": f"Movie {i} é",
            "release_date": f"20{i:02d}-05-01",
            "poster_path": f"/p{i}.jpg",
            "vote_average": 6.5 + i / 10,
            "overview": "x" * 250 if i == 0 else f"overview for movie {i}, long enough",
            "genres": "Action, Science Fiction",
        }
        for i in range(12)
    ]
    df = pd.DataFrame(rows).astype(
        {c: "string" for c in ("title", "release_date", "poster_path", "genres")}
    )
    # Missing optional fields are left out of the item; a whole rating is
    # still a number with a fractional part in moto.
    df.loc[1, ["poster_path", "release_date"]] = pd.NA
    df.loc[1, "vote_average"] = float("nan")
    df.loc[1, "genres"] = ""
    df.loc[2, "vote_average"] = 8.0
    return df


def _neighbours(catalog: pd.DataFrame, top: float) -> pd.DataFrame:
    ids = list(catalog["id"])
    records = [
        (movie_id, rank, other, top - rank * 0.0123456)
        for movie_id in ids
        for rank, other in enumerate([m for m in ids if m != movie_id][:K], start=1)
    ]
    return pd.DataFrame(records, columns=["movie_id", "rank", "neighbour_id", "score"])


def _save_run(store: LocalStore, catalog: pd.DataFrame) -> dict[str, pd.DataFrame]:
    buf = io.BytesIO()
    catalog.to_parquet(buf, index=False)
    store.put_bytes(f"catalog/{RUN_DATE}/catalog.parquet", buf.getvalue())
    # A score of exactly 1.0 covers the whole-number case in neighbour lists.
    neighbours = {m: _neighbours(catalog, 1.0 + i * 0.5) for i, m in enumerate(METHODS)}
    neighbours[METHODS[0]].loc[0, "score"] = 1.0
    for method, df in neighbours.items():
        save_neighbours(df, store, RUN_DATE, method=method)
    return neighbours


def _assert_identical(a, b):
    """== alone would pass 8 against 8.0, so types are compared too."""
    assert type(a) is type(b), (a, b)
    if isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a:
            _assert_identical(a[key], b[key])
    elif isinstance(a, list):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            _assert_identical(x, y)
    else:
        assert a == b


@pytest.fixture
def table(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "movie_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "movie_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


@pytest.fixture
def store(tmp_path):
    return LocalStore(root=str(tmp_path / "store"))


@pytest.mark.parametrize(
    "catalog",
    [
        pytest.param(_catalog(), id="edge-cases"),
        pytest.param(prepare(load(str(DEMO_SAMPLE))).head(60), id="demo-sample"),
    ],
)
def test_same_items_as_dynamo_reader(table, store, catalog):
    neighbours = _save_run(store, catalog)
    DynamoWriter(TABLE, client=table).write(build_items(catalog, neighbours))
    dynamo = DynamoReader(table_name=TABLE, client=table)
    local = LocalReader(store, RUN_DATE, METHODS)

    ids = [str(i) for i in catalog["id"]]
    for movie_id in ids:
        _assert_identical(local.get_item(movie_id), dynamo.get_item(movie_id))
    # Order kept, a repeat kept, an unknown id skipped: batch_get's contract.
    request = [ids[3], ids[0], "999999", ids[3], *ids[5:]]
    _assert_identical(local.batch_get(request), dynamo.batch_get(request))
    assert local.get_item("999999") is None and dynamo.get_item("999999") is None


def test_integer_id_is_accepted_like_dynamo(store):
    _save_run(store, _catalog())
    reader = LocalReader(store, RUN_DATE, METHODS)
    assert reader.get_item(100)["movie_id"] == "100"
    assert [i["movie_id"] for i in reader.batch_get([101, "100"])] == ["101", "100"]


def test_missing_run_raises(store):
    with pytest.raises(FileNotFoundError):
        LocalReader(store, RUN_DATE, METHODS)


class TestMainLocal:
    @pytest.fixture
    def served(self, monkeypatch, tmp_path, store):
        """Runs main() in local mode and returns (app, run kwargs); DynamoReader
        and the title file are booby-trapped, so touching either fails."""
        catalog = _catalog()
        _save_run(store, catalog)
        overlay = tmp_path / "overlay.yaml"
        overlay.write_text(f"storage:\n  local_root: {store.root}\n")

        def forbidden(*args, **kwargs):
            raise AssertionError("local mode must not use DynamoDB or titles.json.gz")

        monkeypatch.setattr(api_main, "DynamoReader", forbidden)
        monkeypatch.setattr(api_main, "load_title_index", forbidden)
        for name in (api_main.PORT_ENV, api_main.HOST_ENV, "AWS_PROFILE"):
            monkeypatch.delenv(name, raising=False)
        calls = []
        monkeypatch.setattr(Flask, "run", lambda self, **kw: calls.append((self, kw)))
        api_main.main(
            ["--local", "--storage", "local", "--run-date", RUN_DATE, "--config", str(overlay)]
        )
        return calls[0][0].test_client(), catalog

    def test_serves_search_and_both_methods(self, served):
        client, catalog = served
        assert client.get("/api/health").get_json()["titles"] == len(catalog)
        assert client.get("/api/search?q=movie 3").get_json()["results"][0]["id"] == "103"
        for method in METHODS:
            body = client.get(f"/api/similar/100?method={method}").get_json()
            assert body["found"] and len(body["recommendations"]) == K

    def test_title_index_comes_from_the_catalog(self, served):
        client, catalog = served
        results = client.get("/api/search?q=movie 2&limit=20").get_json()["results"]
        expected = {e["id"]: e for e in build_title_index(catalog)}
        assert all(r == expected[r["id"]] for r in results)

    def test_run_date_is_required(self, monkeypatch):
        monkeypatch.setattr(Flask, "run", lambda self, **kw: pytest.fail("server started"))
        with pytest.raises(SystemExit):
            api_main.main(["--local", "--storage", "local"])


@pytest.fixture(scope="module")
def sample():
    return load(str(DEMO_SAMPLE))


class TestDemoSample:
    def test_size_order_and_catalog(self, sample):
        assert len(sample) == 5000
        keys = list(zip(-sample["vote_count"], sample["id"]))
        assert keys == sorted(keys)
        assert len(prepare(sample)) == 5000

    def test_holds_the_page_examples(self, sample):
        assert {27205, 862, 238, 155} <= set(sample["id"])
