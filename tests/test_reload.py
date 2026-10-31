"""The rollback command, on moto S3 and DynamoDB."""
import io

import boto3
import pandas as pd
import pytest
from moto import mock_aws

from movierec import reload as reload_module
from movierec.index.neighbours import save_neighbours
from movierec.pipeline import CURRENT_PATH, METHODS
from movierec.storage.dynamo import DynamoReader, build_items
from movierec.storage.factory import BACKEND_ENV, BUCKET_ENV, get_store
from movierec.storage.s3 import S3Store

BUCKET = "movierec-test-bucket"
TABLE = "movies"
RUN_DATE = "2026-10-07"
N_MOVIES = 12
K = 10
PASSED = {"name": "row_count_min", "passed": True}


def _catalog() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "id": [100 + i for i in range(N_MOVIES)],
            "title": [f"Movie {i}" for i in range(N_MOVIES)],
            "release_date": ["2010-05-01"] * N_MOVIES,
            "poster_path": ["/p.jpg"] * N_MOVIES,
            "vote_average": [7.5] * N_MOVIES,
            "overview": ["An overview long enough to pass the filter"] * N_MOVIES,
            "genres": ["Action"] * N_MOVIES,
        }
    )
    return df.astype({c: "string" for c in ("title", "release_date", "poster_path", "genres")})


def _neighbours(catalog: pd.DataFrame) -> pd.DataFrame:
    ids = list(catalog["id"])
    records = [
        (movie_id, rank, other, 0.9 - rank * 0.01)
        for movie_id in ids
        for rank, other in enumerate([m for m in ids if m != movie_id][:K], start=1)
    ]
    return pd.DataFrame(records, columns=["movie_id", "rank", "neighbour_id", "score"])


def _save_run(store: S3Store, run_date: str = RUN_DATE) -> list[dict]:
    catalog = _catalog()
    buf = io.BytesIO()
    catalog.to_parquet(buf, index=False)
    store.put_bytes(f"catalog/{run_date}/catalog.parquet", buf.getvalue())
    neighbours = {m: _neighbours(catalog) for m in METHODS}
    for method, df in neighbours.items():
        save_neighbours(df, store, run_date, method=method)
    store.put_json(f"quality/{run_date}/quality_report.json", {"gates": [PASSED]})
    return build_items(catalog, neighbours)


def _delete(path: str) -> None:
    boto3.client("s3").delete_object(Bucket=BUCKET, Key=path)


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv(BACKEND_ENV, "s3")
    monkeypatch.setenv(BUCKET_ENV, BUCKET)
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        boto3.client("dynamodb").create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "movie_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "movie_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


@pytest.fixture
def store(aws):
    return get_store()


def _table_count() -> int:
    return boto3.client("dynamodb").scan(TableName=TABLE, Select="COUNT")["Count"]


def _assert_nothing_written(store) -> None:
    assert _table_count() == 0
    assert not store.exists(CURRENT_PATH)


def test_happy_path_writes_items_then_current_json(store):
    _save_run(store)
    assert reload_module.main(["--run-date", RUN_DATE]) == 0
    assert _table_count() == N_MOVIES
    item = DynamoReader(table_name=TABLE).get_item("103")
    assert item["title"] == "Movie 3"
    assert all(len(item[f"similar_{m}"]) == K for m in METHODS)
    assert store.get_json(CURRENT_PATH) == {"run_date": RUN_DATE, "method": "tfidf"}


def test_older_run_overwrites_items_but_deletes_nothing(store):
    _save_run(store)
    boto3.client("dynamodb").put_item(
        TableName=TABLE, Item={"movie_id": {"S": "100"}, "title": {"S": "Newer title"}}
    )
    boto3.client("dynamodb").put_item(
        TableName=TABLE, Item={"movie_id": {"S": "999"}, "title": {"S": "Added later"}}
    )
    assert reload_module.main(["--run-date", RUN_DATE]) == 0
    reader = DynamoReader(table_name=TABLE)
    assert reader.get_item("100")["title"] == "Movie 0"
    assert reader.get_item("999")["title"] == "Added later"


@pytest.mark.parametrize(
    "path",
    [
        f"catalog/{RUN_DATE}/catalog.parquet",
        *(f"{m}/{RUN_DATE}/neighbours.parquet" for m in METHODS),
        f"quality/{RUN_DATE}/quality_report.json",
    ],
)
def test_refuses_a_missing_artifact(store, capsys, path):
    _save_run(store)
    _delete(path)
    assert reload_module.main(["--run-date", RUN_DATE]) == 1
    assert path in capsys.readouterr().err
    _assert_nothing_written(store)


@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]])
def test_refuses_a_failed_gate(store, capsys, dry_run):
    _save_run(store)
    store.put_json(
        f"quality/{RUN_DATE}/quality_report.json",
        {"gates": [PASSED, {"name": "catalog_count_min", "passed": False}]},
    )
    assert reload_module.main(["--run-date", RUN_DATE, *dry_run]) == 1
    assert "catalog_count_min" in capsys.readouterr().err
    _assert_nothing_written(store)


def test_refuses_a_report_with_no_gates(store, capsys):
    _save_run(store)
    store.put_json(f"quality/{RUN_DATE}/quality_report.json", {"gates": []})
    assert reload_module.main(["--run-date", RUN_DATE]) == 1
    assert "no gates" in capsys.readouterr().err
    _assert_nothing_written(store)


def test_refuses_a_local_store_for_a_real_reload(aws, monkeypatch, capsys):
    monkeypatch.setenv(BACKEND_ENV, "local")
    assert reload_module.main(["--run-date", RUN_DATE]) == 1
    assert "STORAGE_BACKEND=s3" in capsys.readouterr().err
    assert _table_count() == 0


def test_dry_run_prints_counts_and_writes_nothing(store, capsys):
    items = _save_run(store)
    assert reload_module.main(["--run-date", RUN_DATE, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert RUN_DATE in out
    assert f"items: {N_MOVIES}" in out
    assert f"WCU total: {reload_module.wcu_total(items)}" in out
    _assert_nothing_written(store)


def test_wcu_rounds_up_per_kb():
    assert reload_module.wcu_total([{"movie_id": "1"}]) == 1
    # "title" is 5 bytes, so 1,019 of value is exactly 1 KB.
    assert reload_module.wcu_total([{"title": "x" * 1019}]) == 1
    assert reload_module.wcu_total([{"title": "x" * 1020}]) == 2


def test_failed_write_leaves_current_json(store, monkeypatch):
    _save_run(store)

    class FailingWriter:
        def __init__(self, table):
            pass

        def write(self, items):
            raise RuntimeError("items unwritten")

    monkeypatch.setattr(reload_module, "DynamoWriter", FailingWriter)
    with pytest.raises(RuntimeError):
        reload_module.main(["--run-date", RUN_DATE])
    assert not store.exists(CURRENT_PATH)
