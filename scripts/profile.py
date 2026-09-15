"""Throwaway data-profiling script for T1.1. Not committed.

Reads data/raw/TMDB_movie_dataset_v11.csv, computes the numbers required by
IMPLEMENTATION.md T1.1, prints them, and writes docs/schema.md.
"""
import random

import numpy as np
import pandas as pd

RAW_PATH = "data/raw/TMDB_movie_dataset_v11.csv"
SCHEMA_OUT = "docs/schema.md"

NULL_COLS = ["overview", "title", "genres", "keywords", "release_date", "poster_path"]
VOTE_THRESHOLDS = [0, 5, 10, 50, 100]

CATALOG = dict(min_overview_chars=40, min_vote_count=10, exclude_adult=True, status=["Released"])

JACCARD_SEED = 42
JACCARD_N_PAIRS = 1000
JACCARD_VOTE_COUNT_MIN = 50


def split_keywords(s):
    if not isinstance(s, str) or s == "":
        return set()
    return {k.strip() for k in s.split(", ") if k.strip()}


def jaccard(a, b):
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def main():
    print(f"Loading {RAW_PATH} ...")
    df = pd.read_csv(RAW_PATH, low_memory=False)
    n_total = len(df)
    print(f"Loaded {n_total:,} rows, {len(df.columns)} columns.\n")

    # 1. columns + dtypes
    dtypes = df.dtypes.astype(str)
    print("=== Columns and dtypes ===")
    for col, dt in dtypes.items():
        print(f"  {col:24s} {dt}")

    # 2. total row count
    print(f"\n=== Total row count === {n_total:,}")

    # 3. null rates for specific columns
    print("\n=== Null rate (selected columns) ===")
    null_rates = {}
    for col in NULL_COLS:
        rate = df[col].isna().mean()
        null_rates[col] = rate
        print(f"  {col:16s} {rate:.4f}")

    # 4. vote_count thresholds
    print("\n=== vote_count >= threshold ===")
    vote_counts = {}
    for t in VOTE_THRESHOLDS:
        c = int((df["vote_count"] >= t).sum())
        vote_counts[t] = c
        print(f"  >= {t:<4d} {c:,}")

    # 5. collection/franchise column
    franchise_cols = [c for c in df.columns if "collection" in c.lower() or "franchise" in c.lower()]
    has_franchise_col = len(franchise_cols) > 0
    print(f"\n=== Collection/franchise column exists === {has_franchise_col}"
          f" (matched columns: {franchise_cols})")

    # 6. catalog filter, individually and combined
    overview_len = df["overview"].fillna("").str.len()
    f_overview = overview_len >= CATALOG["min_overview_chars"]
    f_votes = df["vote_count"] >= CATALOG["min_vote_count"]
    f_adult = ~df["adult"].astype(bool) if CATALOG["exclude_adult"] else pd.Series(True, index=df.index)
    f_status = df["status"].isin(CATALOG["status"])

    n_f_overview = int(f_overview.sum())
    n_f_votes = int(f_votes.sum())
    n_f_adult = int(f_adult.sum())
    n_f_status = int(f_status.sum())
    combined = f_overview & f_votes & f_adult & f_status
    n_combined = int(combined.sum())

    print("\n=== Catalog filter (T1.3), applied individually ===")
    print(f"  overview >= {CATALOG['min_overview_chars']} chars : {n_f_overview:,}")
    print(f"  vote_count >= {CATALOG['min_vote_count']}         : {n_f_votes:,}")
    print(f"  exclude_adult                     : {n_f_adult:,}")
    print(f"  status in {CATALOG['status']}         : {n_f_status:,}")
    print(f"  ALL FOUR COMBINED                 : {n_combined:,}")

    # 7. keyword coverage at vote_count>=10, >=50
    kw_nonempty = df["keywords"].notna() & (df["keywords"] != "")
    cov = {}
    for t in (10, 50):
        subset = df["vote_count"] >= t
        cov[t] = kw_nonempty[subset].mean()
        print(f"\nKeyword coverage at vote_count >= {t}: {cov[t]:.4f} (n={int(subset.sum()):,})")

    # 8. distinct keyword vocabulary size
    print("\nBuilding keyword vocabulary ...")
    vocab = set()
    for s in df.loc[kw_nonempty, "keywords"]:
        vocab.update(split_keywords(s))
    vocab_size = len(vocab)
    print(f"Distinct keyword vocabulary size: {vocab_size:,}")

    # 9. mean Jaccard over 1000 random keyword pairs (random baseline floor)
    pool = df.loc[(df["vote_count"] >= JACCARD_VOTE_COUNT_MIN) & kw_nonempty, "keywords"]
    kw_sets = [split_keywords(s) for s in pool]
    kw_sets = [s for s in kw_sets if s]
    rng = random.Random(JACCARD_SEED)
    n_pool = len(kw_sets)
    scores = []
    for _ in range(JACCARD_N_PAIRS):
        i, j = rng.sample(range(n_pool), 2)
        scores.append(jaccard(kw_sets[i], kw_sets[j]))
    mean_jaccard = float(np.mean(scores))
    print(f"\nRandom-pair mean Jaccard (n_pairs={JACCARD_N_PAIRS}, seed={JACCARD_SEED}, "
          f"pool=vote_count>={JACCARD_VOTE_COUNT_MIN} with non-empty keywords, pool size={n_pool:,}): "
          f"{mean_jaccard:.4f}")

    # --- write docs/schema.md ---
    lines = []
    lines.append("# Data schema — TMDB_movie_dataset_v11.csv")
    lines.append("")
    lines.append(f"Source: `{RAW_PATH}`")
    lines.append("")
    lines.append("## Columns and dtypes")
    lines.append("")
    lines.append("| column | dtype |")
    lines.append("|---|---|")
    for col, dt in dtypes.items():
        lines.append(f"| {col} | {dt} |")
    lines.append("")
    lines.append(f"## Total row count")
    lines.append("")
    lines.append(f"{n_total:,}")
    lines.append("")
    lines.append("## Null rate (selected columns)")
    lines.append("")
    lines.append("| column | null rate |")
    lines.append("|---|---|")
    for col in NULL_COLS:
        lines.append(f"| {col} | {null_rates[col]:.4f} |")
    lines.append("")
    lines.append("## vote_count distribution")
    lines.append("")
    lines.append("| threshold | rows >= threshold |")
    lines.append("|---|---|")
    for t in VOTE_THRESHOLDS:
        lines.append(f"| >= {t} | {vote_counts[t]:,} |")
    lines.append("")
    lines.append("## Collection/franchise column")
    lines.append("")
    lines.append(f"Exists: **{has_franchise_col}** (matched columns: {franchise_cols}). "
                  "Decides T2.4 — franchise recall metric is skipped; see §9.")
    lines.append("")
    lines.append("## Catalog filter (§4 T1.3), each condition applied individually")
    lines.append("")
    lines.append("| filter | rows passing |")
    lines.append("|---|---|")
    lines.append(f"| overview >= {CATALOG['min_overview_chars']} chars | {n_f_overview:,} |")
    lines.append(f"| vote_count >= {CATALOG['min_vote_count']} | {n_f_votes:,} |")
    lines.append(f"| exclude_adult | {n_f_adult:,} |")
    lines.append(f"| status in {CATALOG['status']} | {n_f_status:,} |")
    lines.append(f"| **all four combined (catalog size)** | **{n_combined:,}** |")
    lines.append("")
    lines.append("## Keyword coverage")
    lines.append("")
    lines.append("| subset | coverage | n rows |")
    lines.append("|---|---|---|")
    for t in (10, 50):
        subset_n = int((df["vote_count"] >= t).sum())
        lines.append(f"| vote_count >= {t} | {cov[t]:.4f} | {subset_n:,} |")
    lines.append("")
    lines.append(f"Distinct keyword vocabulary size (split on `\", \"`, non-empty keywords "
                  f"catalog-wide): **{vocab_size:,}**")
    lines.append("")
    lines.append("## Random baseline: mean Jaccard over random keyword pairs")
    lines.append("")
    lines.append(f"n_pairs={JACCARD_N_PAIRS}, seed={JACCARD_SEED}, pool = rows with "
                  f"vote_count >= {JACCARD_VOTE_COUNT_MIN} and non-empty keywords "
                  f"(pool size {n_pool:,}).")
    lines.append("")
    lines.append(f"**Mean Jaccard: {mean_jaccard:.4f}** — this is the random-baseline floor for "
                  "T2.3 Keyword Jaccard@10.")
    lines.append("")

    with open(SCHEMA_OUT, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {SCHEMA_OUT}")


if __name__ == "__main__":
    main()
