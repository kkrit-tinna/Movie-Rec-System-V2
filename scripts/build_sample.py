"""Builds data/sample/movies_5k.csv for T1.1.

Stratified sample of 5,000 rows from the raw TMDB dump, stratified on
vote_count decile, seed 42. Committed alongside the sample it produces.
"""
import numpy as np
import pandas as pd

RAW_PATH = "data/raw/TMDB_movie_dataset_v11.csv"
OUT_PATH = "data/sample/movies_5k.csv"
SEED = 42
N_TARGET = 5000


def main():
    df = pd.read_csv(RAW_PATH, low_memory=False)
    n_total = len(df)

    # vote_count is heavily skewed toward 0 (75th percentile is 0), so
    # duplicates='drop' collapses the requested 10 quantiles down to far
    # fewer effective bins -- that's expected, not a bug.
    df = df.assign(_bin=pd.qcut(df["vote_count"], 10, duplicates="drop"))

    bin_sizes = df.groupby("_bin", observed=True).size()
    alloc = (bin_sizes / n_total * N_TARGET).round().astype(int)

    parts = []
    for label, n in alloc.items():
        group = df[df["_bin"] == label]
        n = min(int(n), len(group))
        parts.append(group.sample(n=n, random_state=SEED))
    sample = pd.concat(parts)

    # Proportional rounding rarely sums to exactly N_TARGET. Top up from
    # the unsampled remainder, or trim, with a seeded draw either way.
    diff = N_TARGET - len(sample)
    if diff > 0:
        remaining = df.drop(sample.index)
        sample = pd.concat([sample, remaining.sample(n=diff, random_state=SEED)])
    elif diff < 0:
        rng = np.random.default_rng(SEED)
        drop_idx = rng.choice(sample.index, size=-diff, replace=False)
        sample = sample.drop(index=drop_idx)

    assert len(sample) == N_TARGET, f"expected {N_TARGET} rows, got {len(sample)}"

    sample = sample.drop(columns="_bin")

    # Collapse whitespace in every text field before writing. T1.3's
    # ingest.prepare() must apply this same normalisation when it builds
    # the `document` column, or the sample and the full pipeline will see
    # differently-shaped text for otherwise identical rows.
    text_cols = sample.select_dtypes(include="str").columns
    for col in text_cols:
        sample[col] = sample[col].str.replace(r"\s+", " ", regex=True)

    sample.to_csv(OUT_PATH, index=False)
    print(f"Wrote {OUT_PATH}: {len(sample)} rows")


if __name__ == "__main__":
    main()
