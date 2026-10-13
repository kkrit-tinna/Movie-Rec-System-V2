import boto3
import pandas as pd
import pytest
from flask import Flask
from moto import mock_aws
from rapidfuzz import fuzz, utils

from movierec.api import __main__ as api_main
from movierec.api.app import METHODS, create_app, load_config
from movierec.index.titles import build_title_index
from movierec.storage.dynamo import DynamoReader, DynamoWriter, build_items

TABLE = "movies"
K = 10
TITLES = [
    "Inception", "The Godfather", "Toy Story", "Interstellar", "The Dark Knight",
    "Insomnia", "Memento", "Tenet", "Up!", "Up", "Alien", "Aliens", "Heat",
    "Heat", "M",
]
RECOMMENDATION_FIELDS = {"id", "title", "year", "score", "rating", "poster_path", "overview", "genres"}


def _catalog() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "id": [100 + i for i in range(len(TITLES))],
            "title": TITLES,
            "release_date": [f"{1990 + i}-01-01" for i in range(len(TITLES))],
            "poster_path": [f"/p{i}.jpg" for i in range(len(TITLES))],
            "vote_average": [7.0 + i / 10 for i in range(len(TITLES))],
            "overview": [f"overview of {t}, " + "x" * 300 for t in TITLES],
            "genres": "Action, Science Fiction",
        }
    )
    return df.astype({"title": "string", "release_date": "string", "poster_path": "string"})


def _neighbours(catalog: pd.DataFrame, reverse: bool) -> pd.DataFrame:
    """Ten neighbours each; word2vec ranks them in the opposite order so the
    two methods are distinguishable."""
    ids = list(catalog["id"])
    records = []
    for movie_id in ids:
        others = [m for m in ids if m != movie_id][:K]
        if reverse:
            others = others[::-1]
        for rank, nid in enumerate(others, start=1):
            records.append((movie_id, rank, nid, round(0.9 - rank * 0.05, 4)))
    return pd.DataFrame(records, columns=["movie_id", "rank", "neighbour_id", "score"])


@pytest.fixture
def client(monkeypatch):
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    catalog = _catalog()
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "movie_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "movie_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        items = build_items(
            catalog,
            {"tfidf": _neighbours(catalog, False), "word2vec": _neighbours(catalog, True)},
        )
        DynamoWriter(TABLE, client=ddb).write(items)
        app = create_app(DynamoReader(TABLE, client=ddb), build_title_index(catalog), "tfidf")
        yield app.test_client()


class TestHealth:
    def test_reports_title_count(self, client):
        assert client.get("/api/health").get_json() == {"status": "ok", "titles": len(TITLES)}


class TestSearch:
    def test_best_match_first(self, client):
        results = client.get("/api/search?q=incep").get_json()["results"]
        assert results[0] == {"id": "100", "title": "Inception", "year": 1990}

    def test_case_and_word_order_ignored(self, client):
        results = client.get("/api/search?q=GODFATHER the").get_json()["results"]
        assert results[0]["title"] == "The Godfather"

    def test_one_char_query_returns_empty(self, client):
        assert client.get("/api/search?q=i").get_json() == {"results": []}
        assert client.get("/api/search?q= ").get_json() == {"results": []}
        assert client.get("/api/search").get_json() == {"results": []}

    def test_limit_respected_and_clamped(self, client):
        assert len(client.get("/api/search?q=alien&limit=3").get_json()["results"]) == 3
        assert len(client.get("/api/search?q=alien&limit=0").get_json()["results"]) == 1
        # Every title but "M": one char is under min_fuzzy_length_ratio of "alien".
        results = client.get("/api/search?q=alien&limit=500").get_json()["results"]
        assert len(results) == len(TITLES) - 1
        assert "M" not in [r["title"] for r in results]

    def test_result_shape(self, client):
        for r in client.get("/api/search?q=alien").get_json()["results"]:
            assert set(r) == {"id", "title", "year"}


class TestExactMatch:
    def test_one_char_exact_title_found(self, client):
        results = client.get("/api/search?q=m").get_json()["results"]
        assert [r["title"] for r in results] == ["M"]

    def test_one_char_non_title_is_empty(self, client):
        assert client.get("/api/search?q=z").get_json() == {"results": []}

    def test_exact_match_first_even_when_fuzzy_scores_tie(self, client):
        # "Up!" and "Up" both process to "up" and score 100; "Up!" comes first
        # in the catalog, so fuzzy order alone would put it first.
        assert fuzz.WRatio("up", utils.default_process("Up!")) == fuzz.WRatio("up", "up") == 100
        results = client.get("/api/search?q=  UP ").get_json()["results"]
        assert [r["title"] for r in results[:2]] == ["Up", "Up!"]

    def test_remakes_newest_first(self, client):
        results = client.get("/api/search?q=heat").get_json()["results"]
        years = sorted((1990 + i for i, t in enumerate(TITLES) if t == "Heat"), reverse=True)
        assert len(years) == 2
        assert [(r["title"], r["year"]) for r in results[:2]] == [("Heat", y) for y in years]

    def test_no_duplicates(self, client):
        for q in ("heat", "up", "inception", "alien"):
            ids = [r["id"] for r in client.get(f"/api/search?q={q}&limit=20").get_json()["results"]]
            assert len(ids) == len(set(ids))

    def test_exact_matches_respect_limit(self, client):
        assert len(client.get("/api/search?q=heat&limit=1").get_json()["results"]) == 1


class TestSimilar:
    @pytest.mark.parametrize("method,reverse", [("tfidf", False), ("word2vec", True)])
    def test_ten_in_neighbour_order_with_v1_fields(self, client, method, reverse):
        body = client.get(f"/api/similar/100?method={method}").get_json()
        assert body["found"] is True
        assert body["method"] == method
        assert body["query"] == {"id": "100", "title": "Inception", "year": 1990}
        recs = body["recommendations"]
        expected = [str(101 + i) for i in range(K)]
        assert [r["id"] for r in recs] == (expected[::-1] if reverse else expected)
        assert all(set(r) == RECOMMENDATION_FIELDS for r in recs)
        assert [r["score"] for r in recs] == [round(0.9 - i * 0.05, 4) for i in range(1, K + 1)]

    def test_display_fields(self, client):
        first = client.get("/api/similar/100").get_json()["recommendations"][0]
        assert first == {
            "id": "101",
            "title": "The Godfather",
            "year": 1991,
            "score": 0.85,
            "rating": 7.1,
            "poster_path": "/p1.jpg",
            "overview": ("overview of The Godfather, " + "x" * 300)[:200],
            "genres": ["Action", "Science Fiction"],
        }

    def test_default_method_is_the_configured_one(self, client):
        assert client.get("/api/similar/100").get_json()["method"] == "tfidf"

    def test_n_clamped(self, client):
        def count(n):
            return len(client.get(f"/api/similar/100?n={n}").get_json()["recommendations"])

        assert count(3) == 3
        assert count(0) == 1
        assert count(-5) == 1
        assert count(500) == K  # clamped to 20, but an item holds only k neighbours
        assert count("abc") == K  # not an int -> default

    def test_unknown_method_is_400_listing_allowed(self, client):
        response = client.get("/api/similar/100?method=bert")
        assert response.status_code == 400
        error = response.get_json()["error"]
        assert all(m in error for m in METHODS)

    def test_unknown_id_is_404(self, client):
        response = client.get("/api/similar/999999")
        assert response.status_code == 404
        assert response.get_json() == {"found": False, "message": "Movie not found"}


class TestFuzzyLengthRule:
    """Fuzzy candidates far shorter than the query are dropped; exact matches
    are not. Search never touches the reader, so none is needed here."""

    TITLES = [
        "Inception", "I", "P", "IO", "The Crack: Inception", "Her", "The Godfather",
        "Godfather", "Matrix", "The Matrix", "Ma", "99", "M", "Up", "It", "Titanic",
    ]

    def _search(self, query, ratio=None):
        config = load_config()
        if ratio is not None:
            config["api"]["min_fuzzy_length_ratio"] = ratio
        titles = [{"id": str(i), "title": t, "year": 2000} for i, t in enumerate(self.TITLES)]
        client = create_app(None, titles, "tfidf", config=config).test_client()
        return [r["title"] for r in client.get(f"/api/search?q={query}&limit=20").get_json()["results"]]

    def test_short_titles_dropped_from_fuzzy_tail(self):
        assert self._search("incep")[0] == "Inception"
        assert not {"I", "P", "M"} & set(self._search("incep"))
        assert "IO" not in self._search("inception")
        assert "Her" not in self._search("godfather")

    def test_threshold_is_inclusive(self):
        # "the matrix 1999" is 15 chars: 6 >= 0.4 * 15 is kept, 2 is not.
        results = self._search("the matrix 1999")
        assert {"The Matrix", "Matrix"} <= set(results)
        assert not {"Ma", "99"} & set(results)

    def test_short_exact_matches_still_first(self):
        assert self._search("m") == ["M"]
        assert self._search("up")[0] == "Up"
        assert self._search("it")[0] == "It"
        assert self._search("inception")[0] == "Inception"

    def test_ratio_comes_from_config(self):
        assert "I" in self._search("incep", ratio=0.0)
        # 5.0 drops every fuzzy candidate here (longest title 20 < 5 * 5),
        # but not the exact match.
        assert self._search("incep", ratio=5.0) == []
        assert self._search("inception", ratio=5.0) == ["Inception"]


class TestCreateApp:
    def test_rejects_unknown_default_method(self):
        with pytest.raises(ValueError, match="bert"):
            create_app(reader=None, titles=[], default_method="bert")


class TestMain:
    @pytest.fixture
    def run_calls(self, monkeypatch):
        calls = []
        monkeypatch.setattr(Flask, "run", lambda self, **kw: calls.append(kw))
        monkeypatch.setattr(api_main, "DynamoReader", lambda: object())
        monkeypatch.setattr(api_main, "load_title_index", lambda path: [{"id": "1", "title": "X", "year": None}])
        monkeypatch.delenv(api_main.PORT_ENV, raising=False)
        monkeypatch.delenv(api_main.HOST_ENV, raising=False)
        return calls

    def test_local_dev_server_on_loopback_port_from_config_debug_off(self, run_calls):
        api_main.main(["--local"])
        assert run_calls == [{"host": "127.0.0.1", "port": 8000, "debug": False}]

    def test_port_flag_and_env(self, run_calls, monkeypatch):
        monkeypatch.setenv(api_main.PORT_ENV, "8123")
        api_main.main(["--local"])
        api_main.main(["--local", "--port", "9000"])
        assert [c["port"] for c in run_calls] == [8123, 9000]

    def test_host_env_overrides_loopback(self, run_calls, monkeypatch):
        monkeypatch.setenv(api_main.HOST_ENV, "0.0.0.0")
        api_main.main(["--local"])
        assert run_calls[0]["host"] == "0.0.0.0"

    def test_requires_local(self, run_calls):
        with pytest.raises(SystemExit):
            api_main.main([])
        assert run_calls == []
