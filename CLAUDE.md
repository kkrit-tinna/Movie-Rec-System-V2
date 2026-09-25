# movierec v2
Content-based movie recommender. Batch job precomputes top-10 neighbours;
serving is a key lookup. `IMPLEMENTATION.md` is the source of truth —
read the relevant `T#.#` block before starting work.

## Session rules
- One `T#.#` task per session. Stop when that task's **Done when** command
  passes. Do not begin the next task.
- Do not start a task whose **Depends on** is not yet committed.
- If the real data disagrees with the guide (missing column, different
  count), update the guide in the same commit and add today's line to §9.
  Do not work around it silently.

## Hard constraint
`document` = title + tagline + overview + genres. **Never add keywords.**
Keywords are the held-out label for Keyword Jaccard@10, the project's
primary metric. Including them makes the metric circular and invalidates
Phase 2.

## Environment
- Python 3.12, venv at `movie_rec_venv/`. Activate before running anything.
- Dependencies in `pyproject.toml`. No `requirements.txt`.
- Thresholds and model params live in `config/default.yaml`. Never hardcode
  one in a module.
- `STORAGE_BACKEND=local|s3` selects the store. Nothing outside
  `src/movierec/storage/` may import boto3.

## Git
- One commit per task, on `main`, message format `feat: topk-neighbour-index`
  or `docs: embedding-comparison-report`.
- Never commit anything under `data/` except `data/sample/`.
- Never read, move, or commit `~/.kaggle/kaggle.json` or `.env`.
- Never run git commit or git push. Stop when Done-when passes and summarise the changed files.

## Scope
No Spark, no clusters, no AWS resources outside `infra/`. Cost target is
under $1/month — flag anything that would change that before creating it.