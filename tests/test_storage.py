import boto3
import pytest
from moto import mock_aws

from movierec.storage.local import LocalStore
from movierec.storage.s3 import S3Store, download_with_sidecars

TEST_BUCKET = "movierec-test-bucket"


def _make_local(tmp_path, monkeypatch):
    yield LocalStore(root=str(tmp_path / "artifacts"))


def _make_s3(tmp_path, monkeypatch):
    # Fake credentials, set before any client exists, so nothing here can
    # authenticate against real AWS even if the mock were bypassed.
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=TEST_BUCKET)
        yield S3Store(bucket=TEST_BUCKET, client=client)


BACKENDS = {
    "local": _make_local,
    "s3": _make_s3,
}


@pytest.fixture(params=list(BACKENDS))
def store(request, tmp_path, monkeypatch):
    yield from BACKENDS[request.param](tmp_path, monkeypatch)


class TestArtifactStore:
    def test_put_get_bytes_roundtrip(self, store):
        store.put_bytes("a/b/c.bin", b"hello")
        assert store.get_bytes("a/b/c.bin") == b"hello"

    def test_put_get_json_roundtrip(self, store):
        obj = {"movie_id": "27205", "score": 0.41}
        store.put_json("tfidf/2026-09-15/meta.json", obj)
        assert store.get_json("tfidf/2026-09-15/meta.json") == obj

    def test_exists_false_before_write(self, store):
        assert not store.exists("x.bin")

    def test_exists_true_after_write(self, store):
        store.put_bytes("x.bin", b"data")
        assert store.exists("x.bin")

    def test_exists_false_for_missing(self, store):
        assert not store.exists("missing/path.bin")

    def test_list_returns_written_paths_under_prefix(self, store):
        store.put_bytes("tfidf/2026-09-15/a.bin", b"1")
        store.put_bytes("tfidf/2026-09-15/b.bin", b"2")
        store.put_bytes("word2vec/2026-09-15/a.bin", b"3")
        result = store.list("tfidf/2026-09-15")
        assert sorted(result) == [
            "tfidf/2026-09-15/a.bin",
            "tfidf/2026-09-15/b.bin",
        ]

    def test_list_missing_prefix_returns_empty(self, store):
        assert store.list("nonexistent") == []

    def test_get_bytes_missing_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.get_bytes("nope.bin")


    def test_list_crosses_paginator_page_boundary(self, store):
        # list_objects_v2 returns at most 1,000 keys per call.
        expected = [f"many/{i:04d}.bin" for i in range(1001)]
        for path in expected:
            store.put_bytes(path, b"x")
        assert store.list("many") == expected


# --- download_with_sidecars (GloVe from S3 on Fargate) -----------------------

@pytest.fixture
def s3_client(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=TEST_BUCKET)
        yield client


def test_download_with_sidecars_fetches_file_and_sidecars_only(s3_client, tmp_path):
    for key, body in {
        "models/glove.kv": b"main",
        "models/glove.kv.vectors.npy": b"vectors",
        "models/glove.kv2": b"different model",
        "models/other.kv": b"other",
    }.items():
        s3_client.put_object(Bucket=TEST_BUCKET, Key=key, Body=body)

    path = download_with_sidecars(f"s3://{TEST_BUCKET}/models/glove.kv", tmp_path, s3_client)

    assert path == tmp_path / "glove.kv"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["glove.kv", "glove.kv.vectors.npy"]
    assert (tmp_path / "glove.kv.vectors.npy").read_bytes() == b"vectors"


def test_download_with_sidecars_missing_key_raises(s3_client, tmp_path):
    s3_client.put_object(Bucket=TEST_BUCKET, Key="models/glove.kv.vectors.npy", Body=b"x")
    with pytest.raises(FileNotFoundError):
        download_with_sidecars(f"s3://{TEST_BUCKET}/models/glove.kv", tmp_path, s3_client)


def test_download_with_sidecars_rejects_non_s3_uri(tmp_path):
    with pytest.raises(ValueError):
        download_with_sidecars("/models/glove.kv", tmp_path)
