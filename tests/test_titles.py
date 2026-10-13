import pandas as pd

from movierec.index import titles
from movierec.index.titles import build_title_index, load_title_index, write_title_index


def _catalog() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "id": [27205, 238, 862, 5],
            "title": ["Inception", "The Godfather", "Toy Story", "Amélie"],
            "release_date": ["2010-07-15", "1972-03-14", None, "n/a"],
        }
    )
    df["title"] = df["title"].astype("string")
    df["release_date"] = df["release_date"].astype("string")
    return df


class TestBuildTitleIndex:
    def test_one_entry_per_row_in_catalog_order(self):
        entries = build_title_index(_catalog())
        assert [e["title"] for e in entries] == ["Inception", "The Godfather", "Toy Story", "Amélie"]

    def test_ids_are_strings(self):
        entries = build_title_index(_catalog())
        assert [e["id"] for e in entries] == ["27205", "238", "862", "5"]
        assert all(isinstance(e["id"], str) for e in entries)

    def test_year_is_int_from_release_date(self):
        entries = build_title_index(_catalog())
        assert entries[0]["year"] == 2010
        assert type(entries[0]["year"]) is int

    def test_missing_release_date_gives_none(self):
        assert build_title_index(_catalog())[2]["year"] is None

    def test_malformed_release_date_gives_none(self):
        assert build_title_index(_catalog())[3]["year"] is None

    def test_nan_release_date_gives_none(self):
        df = pd.DataFrame({"id": [1], "title": ["X"], "release_date": [float("nan")]})
        assert build_title_index(df)[0]["year"] is None


class TestRoundTrip:
    def test_write_then_load_is_identical(self, tmp_path):
        entries = build_title_index(_catalog())
        path = write_title_index(entries, tmp_path / "nested" / "titles.json.gz")
        assert load_title_index(path) == entries

    def test_file_is_gzip(self, tmp_path):
        path = write_title_index(build_title_index(_catalog()), tmp_path / "titles.json.gz")
        assert path.read_bytes()[:2] == b"\x1f\x8b"


class TestDefaultPath:
    def test_comes_from_config(self, monkeypatch):
        monkeypatch.delenv(titles.TITLES_PATH_ENV, raising=False)
        assert titles.default_titles_path() == "artifacts/api/titles.json.gz"

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv(titles.TITLES_PATH_ENV, "/tmp/elsewhere.json.gz")
        assert titles.default_titles_path() == "/tmp/elsewhere.json.gz"
