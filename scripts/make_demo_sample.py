"""Builds data/sample/movies_demo.csv, the catalog behind `make demo` (T4.6).

The 5,000 most-voted rows of the raw TMDB dump: vote_count descending, id
ascending as the tie-break, all columns kept. Every field is read and written
as text, so values reach the sample exactly as the raw file has them. The sort
is stable, so rows that tie on both keys (the raw file has duplicate ids) keep
their file order and the same raw file always gives the same sample.

The committed file was built from the 2026-09-14 raw snapshot (1,495,113 rows).
Output SHA-256:
60fb81e5fa271db8d461c1ee9adffa34337d242944b1b480e2fc91c30e9b8a9d

    python scripts/make_demo_sample.py [--input RAW] [--output OUT] [--rows N]
"""
import argparse

import pandas as pd

RAW_PATH = "data/raw/TMDB_movie_dataset_v11.csv"
OUT_PATH = "data/sample/movies_demo.csv"
N_ROWS = 5000


def top_voted(df: pd.DataFrame, n_rows: int) -> pd.DataFrame:
    keys = pd.DataFrame(
        {
            "vote_count": pd.to_numeric(df["vote_count"]),
            "id": pd.to_numeric(df["id"]),
        }
    )
    order = keys.sort_values(
        ["vote_count", "id"], ascending=[False, True], kind="stable"
    ).index
    return df.loc[order[:n_rows]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=RAW_PATH, help="raw TMDB CSV")
    parser.add_argument("--output", default=OUT_PATH, help="sample CSV to write")
    parser.add_argument("--rows", type=int, default=N_ROWS, help="rows to keep")
    args = parser.parse_args()

    raw = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    sample = top_voted(raw, args.rows)
    if len(sample) != args.rows:
        raise SystemExit(f"expected {args.rows} rows, got {len(sample)}")
    sample.to_csv(args.output, index=False)
    print(f"wrote {args.output}: {len(sample)} rows of {len(raw)}")


if __name__ == "__main__":
    main()
