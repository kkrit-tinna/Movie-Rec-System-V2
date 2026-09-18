"""Kaggle download, dtype-explicit load, and document/catalog prep for T1.3.

document = title + tagline + overview + genres, space-joined, lowercased.
Never add keywords here -- they are the held-out label for Keyword
Jaccard@10 (see IMPLEMENTATION.md T1.3 and T2.3).
"""
import argparse
import time
from pathlib import Path

import pandas as pd
import yaml

DATASET_SLUG = "asaniczka/tmdb-movies-dataset-2023-930k-movies"
RAW_PATH = "data/raw/TMDB_movie_dataset_v11.csv"
CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
FRESHNESS_SECONDS = 24 * 60 * 60

DOCUMENT_FIELDS = ["title", "tagline", "overview", "genres"]

DTYPES = {
    "id": "int64",
    "title": "string",
    "tagline": "string",
    "overview": "string",
    "genres": "string",
    "keywords": "string",
    "release_date": "string",
    "runtime": "int64",
    "vote_average": "float64",
    "vote_count": "int64",
    "status": "string",
    "adult": "boolean",
    "poster_path": "string",
}


def download(dest: str = RAW_PATH) -> Path:
    """Download the TMDB dataset via the Kaggle API, skipping if `dest`
    already exists and is under 24h old."""
    dest = Path(dest)
    if dest.exists():
        age = time.time() - dest.stat().st_mtime
        if age < FRESHNESS_SECONDS:
            print(f"{dest} is {age / 3600:.1f}h old, skipping download")
            return dest

    from kaggle.api.kaggle_api_extended import KaggleApi

    dest.parent.mkdir(parents=True, exist_ok=True)
    api = KaggleApi()
    api.authenticate()
    api.dataset_download_files(DATASET_SLUG, path=str(dest.parent), unzip=True)

    if not dest.exists():
        raise FileNotFoundError(f"expected {dest} after Kaggle download, not found")
    return dest


def load(path: str) -> pd.DataFrame:
    """Dtype-explicit read of the raw TMDB CSV (see docs/schema.md).

    Only the columns the pipeline actually uses are read; the source file
    carries several more (backdrop_path, budget, homepage, ...) that are
    dropped here rather than loaded and ignored.
    """
    return pd.read_csv(path, usecols=list(DTYPES.keys()), dtype=DTYPES)


def _load_catalog_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["catalog"]


def _collapse_whitespace(series: pd.Series) -> pd.Series:
    return series.fillna("").str.replace(r"\s+", " ", regex=True).str.strip()


def prepare(df: pd.DataFrame, catalog_config: dict | None = None) -> pd.DataFrame:
    """Build the `document` column and apply the catalog filter.

    document = title + tagline + overview + genres, space-joined, lowercased.
    Whitespace is collapsed the same way scripts/build_sample.py collapses it
    when writing the sample, so sample and full-catalog documents agree.
    """
    catalog_config = catalog_config or _load_catalog_config()
    df = df.copy()

    for col in DOCUMENT_FIELDS:
        df[col] = _collapse_whitespace(df[col])

    document = df[DOCUMENT_FIELDS[0]]
    for col in DOCUMENT_FIELDS[1:]:
        document = document + " " + df[col]
    df["document"] = document.str.lower().str.replace(r"\s+", " ", regex=True).str.strip()

    mask = (
        (df["overview"].str.len() >= catalog_config["min_overview_chars"])
        & (df["vote_count"] >= catalog_config["min_vote_count"])
        & (df["status"].isin(catalog_config["status"]))
    )
    if catalog_config["exclude_adult"]:
        mask &= ~df["adult"].fillna(False)

    return df[mask].reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="path to an already-downloaded CSV; skips the Kaggle download")
    parser.add_argument("--dest", default=RAW_PATH, help="download destination when --input is not given")
    parser.add_argument("--dry-run", action="store_true", help="report counts only, never hit the Kaggle API")
    args = parser.parse_args()

    if args.input:
        path = args.input
    else:
        if args.dry_run:
            path = args.dest
        else:
            path = download(args.dest)

    df = load(path)
    catalog = prepare(df)

    print(f"rows ingested: {len(df)}")
    print(f"rows in catalog: {len(catalog)}")


if __name__ == "__main__":
    main()
