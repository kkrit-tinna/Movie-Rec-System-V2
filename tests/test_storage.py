import pytest

from movierec.storage.local import LocalStore
from movierec.storage.s3 import S3Store


def _make_local(tmp_path):
    return LocalStore(root=str(tmp_path / "artifacts"))


# T4.2 adds an "s3" entry here (backed by moto or similar) to run the same
# suite against S3Store with no other changes to this file.
BACKENDS = {
    "local": _make_local,
}


@pytest.fixture(params=list(BACKENDS))
def store(request, tmp_path):
    return BACKENDS[request.param](tmp_path)


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


def test_s3_store_stub_raises_not_implemented():
    s3 = S3Store(bucket="dummy-bucket")
    with pytest.raises(NotImplementedError):
        s3.put_bytes("x", b"y")
    with pytest.raises(NotImplementedError):
        s3.get_bytes("x")
    with pytest.raises(NotImplementedError):
        s3.put_json("x", {})
    with pytest.raises(NotImplementedError):
        s3.get_json("x")
    with pytest.raises(NotImplementedError):
        s3.exists("x")
    with pytest.raises(NotImplementedError):
        s3.list("x")
