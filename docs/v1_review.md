# v1 Code Review — what carries into v2

**Reviewed:** Sep 10, 2026
**Scope:** v1 GitHub repo — `spark-processing/weekly_movie_processing_spark.py`, `app.py`, `index.html`, `api-service/`, `cluster-manager/`, both READMEs.
**Purpose:** decide what to port, what to rewrite, and what to learn from. Read the relevant section at the start of the task it names.

---

## 0. Security action (done before any v2 work)

- [ ] v1 Spark job hardcodes the Kaggle username and API key. The key is in git history, so editing the file does not remove it.
- [ ] **Fix:** Kaggle → Settings → API → expire old token, create new one → `~/.kaggle/kaggle.json`, `chmod 600`.
- [ ] Consider replacing the hardcoded block in v1 with a comment pointing to `~/.kaggle/kaggle.json`, so the public repo doesn't show the pattern.

---

## 1. Verdict summary

| v1 component | v2 target | Verdict |
|---|---|---|
| Spark TF-IDF (`HashingTF` + `IDF`) | T1.4 `TfidfEmbedder` | **Rewrite.** Different library; see §2 |
| Spark similarity (top 5,000, `argsort[1:21]`) | T1.5 `neighbours.py` | **Rewrite.** Covers 5K movies only; self-match assumption breaks on duplicate text |
| Spark cleaning filters | T1.3 `prepare()` + config | **Port** the rules to pandas |
| Kaggle download, largest-CSV pick | T1.3 `download()` | **Port** the logic, drop the hardcoded auth |
| `index.html` | T4.4 `api/static/index.html` | **Port** with two fixes; see §4 |
| Flask routes + response shape | T4.4 `api/app.py` | **Keep the contract**, replace the backend |
| `api-service/main.py` | — | Placeholder only; real code lived in Cloud Run. Not needed |

---

## 2. T1.4 — why the old embedder isn't reused

1. **Spark ML.** v2 has no Spark (decision #2). sklearn `TfidfVectorizer` is ~30 lines.
2. **Keywords in the document.** v1 `combined_text` = genres + keywords + overview. v2 holds keywords out as evaluation labels (decision #5). Porting v1 would make Keyword Jaccard@10 circular.
3. **1,000 hashed features.** Collisions between unrelated words, and no vocabulary to inspect. v2 uses a real vocabulary (`max_features: 50000`, bigrams).
4. **Minor:** the regex `[^a-zA-Z0-9\s]` strips all non-ASCII letters, damaging non-English text. `TfidfVectorizer`'s default tokenizer handles this better; don't port the regex.

---

## 3. T1.3 — cleaning rules to port

From `load_and_clean_data`, translated to pandas:

| v1 rule | In v2 spec? | Action |
|---|---|---|
| `dropDuplicates(['id'])` | implied | keep — make explicit |
| title, id, release_date not null | partial | keep |
| release year 1900 → current year | **no** | add to `config/default.yaml` → log in §9 Deviations |
| runtime > 0 and ≤ 360 | **no** | add to config → log in §9 Deviations |
| `status == 'Released'` | yes | already in spec |
| `adult == False` | yes | already in spec |

Proposed config addition:

```yaml
catalog:
  min_overview_chars: 40
  min_vote_count: 10
  exclude_adult: true
  status: ["Released"]
  min_release_year: 1900      # from v1
  max_runtime_min: 360        # from v1
  min_runtime_min: 1          # from v1
```

Report how many rows each rule removes. Per-rule counts are better data-quality material than one before/after number.

**Do not port:** v1 `app.py`'s `df.dropna()`, which drops a row if *any* column is null. Check T1.1's null-rate table to see how much it would have cost.

### Also fix in the spec (same commit as T1.3)

The first line of T1.3's "Document text" says the document includes `keywords`; the note below it says to exclude them. Delete `+ keywords` from the first line so the spec isn't self-contradictory.

---

## 4. T4.4 — frontend and API contract

**Endpoints to keep:** `/api/search?q=`, `/api/recommend?title=&n=`, `/api/trending?n=`.
**Response fields to keep:** `title, year, score, rating, runtime, poster_path, overview`.

**Change:** `/api/recommend` should accept a `movie_id` (the dropdown already knows which movie was picked). Titles are not unique; see §5.

**Fixes when porting `index.html`:**
- Movie text is inserted via `innerHTML`. Use `textContent` for title and overview.
- via.placeholder.com appears to be defunct. Use a local placeholder SVG in `static/`.
- The results heading shows the *normalized* query (e.g. `thedarkknight`). Show the selected title instead.
- `search_movies` in v1 uses `str.contains` with regex on; a query like `(` would error. Irrelevant once rapidfuzz replaces it, but worth knowing why.

---

## 5. Lessons (interview material)

- **Keying on title.** v1 indexed the embedding table by normalized title. With ~930K movies, titles repeat, so a lookup could return several films. v2 keys on `movie_id` and uses titles only for search.
- **Silent data loss.** `dropna()` on every column, plus the Spark job's "Skipping corrupted row" handler, meant v1 never reported how much data it discarded. v2 reports ingested vs. catalog counts every run.
- **Possible CSV parsing issue.** Spark's CSV reader doesn't handle line breaks inside quoted fields unless `multiLine` is set, and overviews may contain them. Unconfirmed; T1.1 will show whether the file has multi-line fields.
- **Rightsizing.** A multi-node cluster for under 1 GB of text. The v2 story is choosing the smallest thing that works.
- **Code outside version control.** The v1 API source existed only inside Cloud Run. When the account lapsed, the code went with it. v2 keeps everything, including infra, in the repo.

---

## 6. v1 README corrections (after T2.5)

Fix once v2's evaluation produces real numbers:
- "4-node" (README) vs. "6-node" (handoff): check which is right.
- "0.8 average cosine similarity accuracy" is a similarity score, not accuracy. Replace or remove.
- "75% processing time reduction": remove unless you can say what it was measured against.
- Add a line pointing to the v2 repo.
