"""Local dev server for the API (T4.4a, T4.6).

    python -m movierec.api --local [--port 8000]
    python -m movierec.api --local --storage local --run-date YYYY-MM-DD
        [--config config/sample.yaml]

Binds 127.0.0.1 with debug off; env MOVIEREC_API_HOST (then api.host) changes
it, e.g. to 0.0.0.0 inside a container.

--storage dynamo (the default) reads DynamoDB with the default AWS credentials,
so /api/similar hits the real table: every request is a few read units.
--storage local serves one run's artifacts from the local store, with no AWS
account and no titles.json.gz; `make demo` uses it. --config picks the store
through storage.local_root. On Lambda the entrypoint is
lambda_handler.handler instead.
"""
import argparse
import os
import sys

from movierec.api.app import METHODS, create_app, load_config
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
    parser.add_argument(
        "--storage", choices=("local", "dynamo"), default="dynamo", help="where items come from"
    )
    parser.add_argument("--run-date", help="YYYY-MM-DD of the run to serve; required for local")
    parser.add_argument("--config", help="overlay merged over config/default.yaml")
    args = parser.parse_args(argv)
    if not args.local:
        sys.exit("only --local is supported; on Lambda use movierec.api.lambda_handler.handler")
    if args.storage == "local" and not args.run_date:
        parser.error("--storage local needs --run-date")

    config = load_config(args.config)
    host = os.environ.get(HOST_ENV) or (config.get("api") or {}).get("host") or DEFAULT_HOST
    port = args.port or int(
        os.environ.get(PORT_ENV) or (config.get("api") or {}).get("port") or DEFAULT_PORT
    )
    if args.storage == "local":
        # Local branch only: these pull in pandas/pyarrow, which the slim
        # Lambda image does not have (tests/test_api_imports.py).
        from movierec.index.titles import build_title_index
        from movierec.storage.factory import get_store
        from movierec.storage.local_reader import LocalReader

        store = get_store("local", root=config["storage"].get("local_root"))
        reader = LocalReader(store, args.run_date, METHODS)
        titles = build_title_index(reader.catalog)
    else:
        reader = DynamoReader()
        titles = load_title_index(default_titles_path())
    app = create_app(reader, titles, config["pipeline"]["default_method"], config=config)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
