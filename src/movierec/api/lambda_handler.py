"""Lambda entrypoint for the API (T4.4).

    handler = movierec.api.lambda_handler.handler

The Flask app is built once, at module import, so the title index is loaded
and the DynamoDB client created once per cold start and reused by every warm
invocation. Mangum speaks ASGI and Flask is WSGI, so a2wsgi's WSGIMiddleware
sits between them (§9 2026-10-12). Lifespan is off: Flask has no startup or
shutdown events.

The title index path comes from env MOVIEREC_TITLES_PATH, which the API image
sets to its bundled copy (§7 T4.4).
"""
from a2wsgi import WSGIMiddleware
from mangum import Mangum

from movierec.api.app import create_app, load_config
from movierec.index.titles import default_titles_path, load_title_index
from movierec.storage.dynamo import DynamoReader

_config = load_config()
app = create_app(
    DynamoReader(),
    load_title_index(default_titles_path()),
    _config["pipeline"]["default_method"],
    config=_config,
)
handler = Mangum(WSGIMiddleware(app), lifespan="off")
