"""Title index for the API's search box (T4.4a).

build_title_index() is pure: prepared catalog in, [{"id", "title", "year"}]
out, one entry per catalog movie, so it covers exactly the ids DynamoDB was
loaded from. write/load store it as gzip JSON; the API loads it once at
start-up and fuzzy-matches over it (the matching lives in api/, so this
module stays free of rapidfuzz and the batch image can import it later).

For the Oct 15 fair the file is built by the CLI below and bundled in the
API image; after that it moves to the pipeline (§7 T4.4).

    python -m movierec.index.titles --input data/raw/TMDB_movie_dataset_v11.csv

pandas and ingest are imported inside the functions that use them, so the API
can import load_title_index without loading pandas (slim image, §9 2026-10-12).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import pandas as pd

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"

TITLES_PATH_ENV = "MOVIEREC_TITLES_PATH"
DEFAULT_TITLES_PATH = "artifacts/api/titles.json.gz"


def default_titles_path() -> str:
    """Env MOVIEREC_TITLES_PATH, then config api.titles_path, then code default."""
    if os.environ.get(TITLES_PATH_ENV):
        return os.environ[TITLES_PATH_ENV]
    with open(CONFIG_PATH) as f:
        api_config = yaml.safe_load(f).get("api") or {}
    return api_config.get("titles_path") or DEFAULT_TITLES_PATH


def _year(release_date) -> int | None:
    """release_date[:4] as an int, the rule storage/dynamo.py's build_items uses;
    None when the date is missing or does not start with four digits."""
    import pandas as pd

    if release_date is None or release_date is pd.NA:
        return None
    if isinstance(release_date, float):  # NaN
        return None
    head = str(release_date)[:4]
    return int(head) if len(head) == 4 and head.isdigit() else None


def build_title_index(catalog_df: pd.DataFrame) -> list[dict]:
    """One {"id": str, "title": str, "year": int | None} per catalog row, in
    catalog order. A missing title becomes "" (prepare() already fills it)."""
    import pandas as pd

    entries = []
    for movie_id, title, release_date in zip(
        catalog_df["id"], catalog_df["title"], catalog_df["release_date"]
    ):
        entries.append(
            {
                "id": str(movie_id),
                "title": "" if title is None or title is pd.NA else str(title),
                "year": _year(release_date),
            }
        )
    return entries


def write_title_index(entries: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, separators=(",", ":"))
    return path


def load_title_index(path: str | Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    from movierec.data import ingest

    parser = argparse.ArgumentParser(description="Build the API title index from the raw TMDB CSV.")
    parser.add_argument("--input", default=ingest.RAW_PATH, help="raw TMDB CSV (already downloaded)")
    parser.add_argument("--out", default=None, help="output path; default env/config api.titles_path")
    args = parser.parse_args()

    out = args.out or default_titles_path()
    catalog = ingest.prepare(ingest.load(args.input))
    entries = build_title_index(catalog)
    path = write_title_index(entries, out)
    print(f"entries: {len(entries)}")
    print(f"without year: {sum(e['year'] is None for e in entries)}")
    print(f"empty title: {sum(not e['title'] for e in entries)}")
    print(f"wrote {path} ({path.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
