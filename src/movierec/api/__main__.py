"""Local dev server for the API (T4.4a).

    python -m movierec.api --local [--port 8000]

Binds 127.0.0.1 with debug off; env MOVIEREC_API_HOST (then api.host) changes
it, e.g. to 0.0.0.0 inside a container. Reads DynamoDB with the default AWS
credentials, so /api/similar hits the real table: every request is a few
read units. On Lambda the entrypoint is lambda_handler.handler instead.
"""
import argparse
import os
import sys

from movierec.api.app import create_app, load_config
from movierec.index.titles import default_titles_path, load_title_index
from movierec.storage.dynamo import DynamoReader

HOST_ENV = "MOVIEREC_API_HOST"
DEFAULT_HOST = "127.0.0.1"
PORT_ENV = "MOVIEREC_API_PORT"
DEFAULT_PORT = 8000


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the movierec API locally.")
    parser.add_argument("--local", action="store_true", help="run the Flask dev server")
    parser.add_argument("--port", type=int, default=None, help="default env/config api.port")
    args = parser.parse_args(argv)
    if not args.local:
        sys.exit("only --local is supported; on Lambda use movierec.api.lambda_handler.handler")

    config = load_config()
    host = os.environ.get(HOST_ENV) or (config.get("api") or {}).get("host") or DEFAULT_HOST
    port = args.port or int(
        os.environ.get(PORT_ENV) or (config.get("api") or {}).get("port") or DEFAULT_PORT
    )
    titles = load_title_index(default_titles_path())
    app = create_app(
        DynamoReader(), titles, config["pipeline"]["default_method"], config=config
    )
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
