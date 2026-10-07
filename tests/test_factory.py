import boto3
import pytest
from moto import mock_aws

from movierec.storage import factory
from movierec.storage.factory import BACKEND_ENV, BUCKET_ENV, get_store
from movierec.storage.local import LocalStore
from movierec.storage.s3 import S3Store

TEST_BUCKET = "movierec-test-bucket"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # A developer's .env or shell must not leak into these tests.
    monkeypatch.delenv(BACKEND_ENV, raising=False)
    monkeypatch.delenv(BUCKET_ENV, raising=False)


@pytest.fixture
def aws(monkeypatch):
    # Same guard as test_storage.py: fake credentials before any client exists.
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=TEST_BUCKET)
        yield


def test_default_is_local(tmp_path):
    store = get_store(root=str(tmp_path))
    assert isinstance(store, LocalStore)
    store.put_json("x/y.json", {"a": 1})
    assert (tmp_path / "x" / "y.json").exists()


def test_env_selects_s3_with_bucket_from_env(aws, monkeypatch):
    monkeypatch.setenv(BACKEND_ENV, "s3")
    monkeypatch.setenv(BUCKET_ENV, TEST_BUCKET)
    store = get_store()
    assert isinstance(store, S3Store)
    assert store.bucket == TEST_BUCKET
    store.put_json("x/y.json", {"a": 1})
    assert store.get_json("x/y.json") == {"a": 1}


def test_argument_overrides_env(tmp_path, monkeypatch):
    # pipeline.py's --storage flag passes backend explicitly.
    monkeypatch.setenv(BACKEND_ENV, "s3")
    assert isinstance(get_store("local", root=str(tmp_path)), LocalStore)


def test_bucket_falls_back_to_config(aws, monkeypatch):
    monkeypatch.setattr(
        factory, "_load_storage_config", lambda: {"backend": "s3", "bucket": TEST_BUCKET}
    )
    store = get_store()
    assert isinstance(store, S3Store)
    assert store.bucket == TEST_BUCKET


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv(BACKEND_ENV, "gcs")
    with pytest.raises(ValueError, match="unknown storage backend 'gcs'"):
        get_store()


def test_s3_without_bucket_raises():
    # default.yaml ships bucket: null, so with no env var there is no bucket.
    with pytest.raises(ValueError, match=BUCKET_ENV):
        get_store("s3")
