import pandas as pd

from movierec.data.ingest import download, prepare

CATALOG_CONFIG = {
    "min_overview_chars": 10,
    "min_vote_count": 10,
    "exclude_adult": True,
    "status": ["Released"],
}


def _movie(**overrides):
    row = {
        "title": "The Movie",
        "tagline": "A tagline",
        "overview": "An overview long enough to pass the filter.",
        "genres": "Action, Drama",
        "vote_count": 50,
        "status": "Released",
        "adult": False,
    }
    row.update(overrides)
    return row


class TestPrepare:
    def test_document_is_title_tagline_overview_genres_lowercased(self):
        df = pd.DataFrame([_movie()])
        out = prepare(df, CATALOG_CONFIG)
        assert out.loc[0, "document"] == (
            "the movie a tagline an overview long enough to pass the filter. action, drama"
        )

    def test_document_never_contains_keywords(self):
        df = pd.DataFrame([_movie(keywords="spoiler, twist, ending")])
        out = prepare(df, CATALOG_CONFIG)
        assert "spoiler" not in out.loc[0, "document"]
        assert "twist" not in out.loc[0, "document"]

    def test_whitespace_is_collapsed(self):
        df = pd.DataFrame([_movie(title="The   Movie", overview="Weird\n\nspacing   here forever.")])
        out = prepare(df, CATALOG_CONFIG)
        assert "  " not in out.loc[0, "document"]

    def test_missing_text_fields_do_not_break_document(self):
        df = pd.DataFrame([_movie(tagline=None)])
        out = prepare(df, CATALOG_CONFIG)
        assert out.loc[0, "document"] == (
            "the movie an overview long enough to pass the filter. action, drama"
        )

    def test_catalog_filter_drops_short_overview(self):
        df = pd.DataFrame([_movie(overview="short")])
        out = prepare(df, CATALOG_CONFIG)
        assert len(out) == 0

    def test_catalog_filter_drops_low_vote_count(self):
        df = pd.DataFrame([_movie(vote_count=1)])
        out = prepare(df, CATALOG_CONFIG)
        assert len(out) == 0

    def test_catalog_filter_drops_adult_when_excluded(self):
        df = pd.DataFrame([_movie(adult=True)])
        out = prepare(df, CATALOG_CONFIG)
        assert len(out) == 0

    def test_catalog_filter_drops_non_released_status(self):
        df = pd.DataFrame([_movie(status="Rumored")])
        out = prepare(df, CATALOG_CONFIG)
        assert len(out) == 0

    def test_catalog_filter_keeps_passing_row(self):
        df = pd.DataFrame([_movie()])
        out = prepare(df, CATALOG_CONFIG)
        assert len(out) == 1


class TestDownload:
    def test_skips_download_when_file_is_fresh(self, tmp_path):
        dest = tmp_path / "raw.csv"
        dest.write_text("id,title\n1,x\n")
        result = download(str(dest))
        assert result == dest
        assert dest.read_text() == "id,title\n1,x\n"
