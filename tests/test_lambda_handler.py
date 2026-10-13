import importlib
import json
import sys

import pytest

from movierec.index.titles import write_title_index
from movierec.storage import dynamo

MODULE = "movierec.api.lambda_handler"

TITLES = [
    {"id": "27205", "title": "Inception", "year": 2010},
    {"id": "155", "title": "The Dark Knight", "year": 2008},
    {"id": "1", "title": "I", "year": 2015},
]

ITEMS = {
    "27205": {
        "movie_id": "27205", "title": "Inception", "year": 2010,
        "similar_tfidf": [{"id": "155", "score": 0.41}],
        "similar_word2vec": [{"id": "155", "score": 0.88}],
    },
    "155": {"movie_id": "155", "title": "The Dark Knight", "year": 2008, "poster_path": "/p.jpg"},
}


class StubReader:
    """Stands in for DynamoReader: no AWS client, no calls."""

    def get_item(self, movie_id):
        return ITEMS.get(movie_id)

    def batch_get(self, ids):
        return [ITEMS[i] for i in ids if i in ITEMS]


def function_url_event(path: str, query: str = "") -> dict:
    """Minimal Lambda Function URL request, payload format 2.0."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": {"host": "abc123.lambda-url.us-east-1.on.aws", "accept": "application/json"},
        "requestContext": {
            "accountId": "anonymous",
            "apiId": "abc123",
            "domainName": "abc123.lambda-url.us-east-1.on.aws",
            "domainPrefix": "abc123",
            "http": {
                "method": "GET",
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.1",
                "userAgent": "pytest",
            },
            "requestId": "req-1",
            "routeKey": "$default",
            "stage": "$default",
            "time": "12/Oct/2026:12:00:00 +0000",
            "timeEpoch": 1791806400000,
        },
        "isBase64Encoded": False,
    }


class LambdaContext:
    function_name = "movierec-api"
    aws_request_id = "req-1"


@pytest.fixture
def handler(tmp_path, monkeypatch):
    """Import the module fresh, with the stub reader and a small title index."""
    titles_path = write_title_index(TITLES, tmp_path / "titles.json.gz")
    monkeypatch.setenv("MOVIEREC_TITLES_PATH", str(titles_path))
    monkeypatch.setattr(dynamo, "DynamoReader", StubReader)
    sys.modules.pop(MODULE, None)
    yield importlib.import_module(MODULE).handler
    sys.modules.pop(MODULE, None)


def _call(handler, path, query=""):
    response = handler(function_url_event(path, query), LambdaContext())
    assert response["headers"]["content-type"] == "application/json"
    return response["statusCode"], json.loads(response["body"])


def test_health(handler):
    assert _call(handler, "/api/health") == (200, {"status": "ok", "titles": len(TITLES)})


def test_search_passes_query_string(handler):
    status, body = _call(handler, "/api/search", "q=incep")
    assert status == 200
    titles = [r["title"] for r in body["results"]]
    assert titles[0] == "Inception"
    assert "I" not in titles  # fuzzy length rule applies through the adapter too


def test_similar_uses_configured_default_method(handler):
    status, body = _call(handler, "/api/similar/27205")
    assert status == 200
    assert body["method"] == "tfidf"
    assert [r["id"] for r in body["recommendations"]] == ["155"]


def test_not_found_status_passes_through(handler):
    status, body = _call(handler, "/api/similar/999")
    assert status == 404
    assert body["found"] is False
