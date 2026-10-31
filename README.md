# movierec

A content-based movie recommender that precomputes each film's 10 nearest neighbours in a weekly batch job and serves them as a key lookup, for about $0.28 a month on AWS. **[Try the live site](https://rueyegqfm4fjepd2vbstidhkti0crznq.lambda-url.us-east-1.on.aws/)**

## Run it yourself

You need Python 3.12, `make` and an internet connection for the first run. It's tested on macOS, and you don't need an AWS account or Kaggle key.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
make demo
```

The first run takes about 64 s, because it downloads the Python packages and ~128 MB of GloVe vectors once. Later runs take about 20 s. Flask's development-server warning is expected.

Open <http://localhost:8000> and try the examples and the "Match by" toggle. Ctrl+C stops the server. The demo catalog is the 5,000 most-voted films.

### Run the tests

```bash
pip install -e ".[api,dev]"
pytest -q
```

### Run the full pipeline

This downloads the full dataset (~1.5M rows) and needs a Kaggle API token and about 4 GB of RAM:

```bash
export KAGGLE_API_TOKEN=your-token
python -m movierec.pipeline --storage local
```

## How it works

```
                    ┌──────────────────────────────┐
   EventBridge      │  ECS Fargate task            │
   Scheduler   ───► │  2 vCPU / 4 GiB, ~6 min      │
   (Sun 02:00 ET)   │  PUBLIC subnet, no NAT       │
                    │                              │
                    │  1. download Kaggle → /tmp   │
                    │  2. quality gates (fail fast)│
                    │  3. fit TF-IDF, embed GloVe  │
                    │  4. top-K neighbours, both   │
                    │  5. evaluate → metrics.json  │
                    │  6. write artifacts → S3     │
                    │  7. batch-write → DynamoDB   │
                    │  8. flip current.json        │
                    └───────────┬──────────────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
  ┌───────────┐        ┌────────────────┐        ┌──────────────┐
  │    S3     │        │   DynamoDB     │        │  CloudWatch  │
  │ artifacts │        │ movie items +  │        │ logs + SNS   │
  │ metrics   │        │ top-K lists    │        │ on failure   │
  │ current   │        └───────┬────────┘        └──────────────┘
  └───────────┘                │
                               ▼
     ┌──────────────────────────────┐
     │  Lambda (container image)    │
     │  + Function URL              │
     │  Flask via Mangum            │
     │  rapidfuzz title search      │
     │  (title index in the image)  │
     └──────────────┬───────────────┘
                    ▼
              browser (static HTML served by the same Lambda)
```

Each film's neighbours are precomputed in the batch job, so serving is a key lookup in DynamoDB. The API never loads an embedding matrix.

## Engineering notes

- **Rightsizing:** v1's 4-node Dataproc cluster became one ~6-minute Fargate task.
- **Held-out evaluation:** TMDB keyword tags are withheld from the text; on them, TF-IDF beats GloVe 0.0376 vs 0.0241 Keyword Jaccard@10 ([docs/comparison.md](docs/comparison.md)).
- **Quality gates:** they stop a bad run before anything is published.
- **Cost:** about $0.28/month on AWS, with no NAT Gateway, public subnet only.
- **Rollback:** `current.json` records the last published run; rollback reloads DynamoDB from an earlier run's S3 artifacts (see [docs/runbook.md](docs/runbook.md)).

## Data and attribution

Movie data and posters from TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.

The data comes from [Full TMDB Movies Dataset 2024 (1M Movies)](https://www.kaggle.com/datasets/asaniczka/tmdb-movies-dataset-2023-930k-movies) by asaniczka on Kaggle, licensed under the [ODC Attribution License (ODC-By)](https://opendatacommons.org/licenses/by/1-0/). The files in `data/sample/` are extracts of that dataset.

---

Next: free-text search and collaborative filtering (v3)
