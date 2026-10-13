"""The API image is slim (§9 2026-10-09): it has no pandas, numpy, scipy,
sklearn, gensim or pyarrow. Neither importing the API's entry modules nor
serving a request may load any of them, or the Lambda fails with
ModuleNotFoundError at cold start or on its first request.

Runs in a subprocess because this test process has already imported pandas
through other test modules.
"""
import os
import subprocess
import sys
import textwrap

from movierec.index.titles import write_title_index

BATCH_ONLY = ("pandas", "numpy", "scipy", "sklearn", "gensim", "pyarrow")

# app is the Flask app; __main__ (local server) and lambda_handler (Lambda)
# wire in DynamoReader and the title index. Importing lambda_handler builds
# the app: it loads the titles file and creates a boto3 client, no AWS call.
API_MODULES = ("movierec.api.app", "movierec.api.__main__", "movierec.api.lambda_handler")

TITLES = [
    {"id": "27205", "title": "Inception", "year": 2010},
    {"id": "155", "title": "The Dark Knight", "year": 2008},
    {"id": "157336", "title": "Interstellar", "year": 2014},
]

# Import, then serve /api/search and /api/similar through a real DynamoReader
# over a fake low-level client, so the reader's deserialize path runs too.
# Prints the batch-only modules loaded after each phase.
SCRIPT = textwrap.dedent(
    """
    import ast, importlib, sys

    BATCH_ONLY, API_MODULES = ast.literal_eval(sys.argv[1]), ast.literal_eval(sys.argv[2])
    titles_path = sys.argv[3]

    def loaded():
        return ",".join(m for m in BATCH_ONLY if m in sys.modules)

    for m in API_MODULES:
        importlib.import_module(m)
    print("import:", loaded())

    from movierec.api.app import create_app, load_config
    from movierec.index.titles import load_title_index
    from movierec.storage.dynamo import DynamoReader

    def wire(movie_id, title, year, neighbours):
        return {
            "movie_id": {"S": movie_id},
            "title": {"S": title},
            "year": {"N": str(year)},
            "vote_average": {"N": "8.4"},
            "poster_path": {"S": "/p.jpg"},
            "genres": {"L": [{"S": "Action"}]},
            "similar_tfidf": {"L": [
                {"M": {"id": {"S": n}, "score": {"N": "0.41"}}} for n in neighbours
            ]},
            "similar_word2vec": {"L": [
                {"M": {"id": {"S": n}, "score": {"N": "0.88"}}} for n in neighbours
            ]},
        }

    ITEMS = {
        "27205": wire("27205", "Inception", 2010, ["155", "157336"]),
        "155": wire("155", "The Dark Knight", 2008, ["27205"]),
        "157336": wire("157336", "Interstellar", 2014, ["27205"]),
    }

    class FakeClient:
        def get_item(self, TableName, Key):
            item = ITEMS.get(Key["movie_id"]["S"])
            return {"Item": item} if item else {}

        def batch_get_item(self, RequestItems):
            (table, req), = RequestItems.items()
            keys = [k["movie_id"]["S"] for k in req["Keys"]]
            return {"Responses": {table: [ITEMS[k] for k in keys if k in ITEMS]}}

    titles = load_title_index(titles_path)
    reader = DynamoReader(table_name="movies", client=FakeClient())
    client = create_app(reader, titles, "tfidf", config=load_config()).test_client()

    search = client.get("/api/search?q=incep")
    assert search.status_code == 200, search.status_code
    assert search.get_json()["results"][0]["id"] == "27205", search.get_json()
    for url in ("/api/similar/27205", "/api/similar/27205?method=word2vec"):
        similar = client.get(url)
        assert similar.status_code == 200, (url, similar.status_code)
        assert [r["id"] for r in similar.get_json()["recommendations"]] == ["155", "157336"]
    print("requests:", loaded())
    """
)


def test_api_import_and_requests_load_no_batch_dependencies(tmp_path):
    titles_path = write_title_index(TITLES, tmp_path / "titles.json.gz")
    env = {k: v for k, v in os.environ.items() if k != "AWS_PROFILE"}
    env.update(
        MOVIEREC_TITLES_PATH=str(titles_path),
        AWS_DEFAULT_REGION="us-east-1",
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            SCRIPT,
            repr(BATCH_ONLY),
            repr(API_MODULES),
            str(titles_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["import: ", "requests: "], result.stdout
