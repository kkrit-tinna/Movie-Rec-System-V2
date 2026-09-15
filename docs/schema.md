# Data schema — TMDB_movie_dataset_v11.csv

Source: `data/raw/TMDB_movie_dataset_v11.csv`

## Columns and dtypes

| column | dtype |
|---|---|
| id | int64 |
| title | str |
| vote_average | float64 |
| vote_count | int64 |
| status | str |
| release_date | str |
| revenue | int64 |
| runtime | int64 |
| adult | bool |
| backdrop_path | str |
| budget | int64 |
| homepage | str |
| imdb_id | str |
| original_language | str |
| original_title | str |
| overview | str |
| popularity | float64 |
| poster_path | str |
| tagline | str |
| genres | str |
| production_companies | str |
| production_countries | str |
| spoken_languages | str |
| keywords | str |

## Total row count

1,495,113

## Null rate (selected columns)

| column | null rate |
|---|---|
| overview | 0.2326 |
| title | 0.0000 |
| genres | 0.4484 |
| keywords | 0.7572 |
| release_date | 0.2359 |
| poster_path | 0.3644 |

## vote_count distribution

| threshold | rows >= threshold |
|---|---|
| >= 0 | 1,495,113 |
| >= 5 | 125,867 |
| >= 10 | 78,852 |
| >= 50 | 27,981 |
| >= 100 | 18,191 |

## Collection/franchise column

Exists: **False** (matched columns: []). Decides T2.4 — franchise recall metric is skipped; see §9.

## Catalog filter (§4 T1.3), each condition applied individually

| filter | rows passing |
|---|---|
| overview >= 40 chars | 1,071,912 |
| vote_count >= 10 | 78,852 |
| exclude_adult | 1,347,053 |
| status in ['Released'] | 1,437,552 |
| **all four combined (catalog size)** | **77,281** |

## Keyword coverage

| subset | coverage | n rows |
|---|---|---|
| vote_count >= 10 | 0.7134 | 78,852 |
| vote_count >= 50 | 0.8546 | 27,981 |

Distinct keyword vocabulary size (split on `", "`, non-empty keywords catalog-wide): **74,430**

## Random baseline: mean Jaccard over random keyword pairs

n_pairs=1000, seed=42, pool = rows with vote_count >= 50 and non-empty keywords (pool size 23,912).

**Mean Jaccard: 0.0027** — this is the random-baseline floor for T2.3 Keyword Jaccard@10.

