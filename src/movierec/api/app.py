"""Flask API for the movie site (T4.4a).

Serving is a key lookup: the neighbours were precomputed by the batch job, so
/api/similar is one GetItem plus one BatchGetItem, and /api/search is a fuzzy
match over the in-memory title index. Response shapes follow the v1 site
(found / query / recommendations, results) so its page can be reused.

create_app() takes its collaborators, so tests inject a moto-backed reader
and a small title list; __main__.py wires the real ones.
"""
from pathlib import Path

import yaml
from flask import Flask, jsonify, request
from rapidfuzz import fuzz, process, utils

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"

# Item attributes written by the batch job: similar_tfidf, similar_word2vec.
METHODS = ("tfidf", "word2vec")

DEFAULT_MIN_QUERY_CHARS = 2
DEFAULT_MAX_RESULTS = 20
DEFAULT_MIN_FUZZY_LENGTH_RATIO = 0.4
DEFAULT_K = 10


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _clamp(value: int, upper: int) -> int:
    return max(1, min(upper, value))


def _normalize(title: str) -> str:
    """Exact-match key: lowercased, stripped, inner whitespace collapsed."""
    return " ".join(title.lower().split())


def _exact_index(titles: list[dict]) -> dict[str, list[int]]:
    """Normalized title -> positions in `titles`, newest year first (remakes)."""
    index: dict[str, list[int]] = {}
    for position, entry in enumerate(titles):
        key = _normalize(entry["title"])
        if key:
            index.setdefault(key, []).append(position)
    for positions in index.values():
        positions.sort(key=lambda i: (titles[i]["year"] is None, -(titles[i]["year"] or 0)))
    return index


def _brief(item: dict) -> dict:
    return {"id": item["movie_id"], "title": item.get("title"), "year": item.get("year")}


def create_app(reader, titles: list[dict], default_method: str, config: dict | None = None) -> Flask:
    """Build the app around a DynamoReader-like `reader` (get_item, batch_get)
    and a title index from movierec.index.titles."""
    if default_method not in METHODS:
        raise ValueError(f"default_method {default_method!r} not in {METHODS}")
    config = config if config is not None else load_config()
    api_config = config.get("api") or {}
    min_query_chars = api_config.get("min_query_chars", DEFAULT_MIN_QUERY_CHARS)
    max_results = api_config.get("max_results", DEFAULT_MAX_RESULTS)
    min_length_ratio = api_config.get("min_fuzzy_length_ratio", DEFAULT_MIN_FUZZY_LENGTH_RATIO)
    default_n = (config.get("index") or {}).get("k", DEFAULT_K)

    # Both built once here rather than per request.
    exact = _exact_index(titles)
    choices = [utils.default_process(t["title"]) for t in titles]
    lengths = [len(_normalize(t["title"])) for t in titles]

    app = Flask(__name__)

    @app.route("/api/health")
    def health():
        """Liveness check; reports the size of the loaded title index."""
        return jsonify({"status": "ok", "titles": len(titles)})

    @app.route("/api/search")
    def search():
        """Title suggestions for the search box: exact title matches first,
        then fuzzy matches. Short queries get exact matches only, so a
        one-letter title ("M") is findable but "m" returns no noise."""
        query = request.args.get("q", "")
        limit = _clamp(request.args.get("limit", default_n, type=int), max_results)
        key = _normalize(query)
        positions = list(exact.get(key, [])) if key else []
        if len(key) < min_query_chars:
            return jsonify({"results": [titles[i] for i in positions[:limit]]})

        # WRatio: best of full, partial and token-order ratios, so prefixes
        # ("incep"), reordered words and case all still match. Its partial
        # match also scores one-letter titles 90 ("I" for "incep"), so titles
        # much shorter than the query are left out of the fuzzy tail (None
        # choices are skipped); exact matches above are unaffected. Asks for
        # enough extra to fill `limit` after the exact matches are removed.
        min_length = min_length_ratio * len(key)
        candidates = [c if lengths[i] >= min_length else None for i, c in enumerate(choices)]
        matches = process.extract(
            utils.default_process(query),
            candidates,
            scorer=fuzz.WRatio,
            processor=None,
            limit=limit + len(positions),
        )
        seen = set(positions)
        positions += [i for _, _, i in matches if i not in seen]
        return jsonify({"results": [titles[i] for i in positions[:limit]]})

    @app.route("/api/similar/<movie_id>")
    def similar(movie_id):
        """Precomputed neighbours of one movie, hydrated with display fields."""
        method = request.args.get("method", default_method)
        if method not in METHODS:
            return jsonify({"error": f"unknown method {method!r}; allowed: {list(METHODS)}"}), 400
        n = _clamp(request.args.get("n", default_n, type=int), max_results)

        item = reader.get_item(movie_id)
        if item is None:
            return jsonify({"found": False, "message": "Movie not found"}), 404

        neighbours = item.get(f"similar_{method}", [])[:n]
        scores = {nb["id"]: nb["score"] for nb in neighbours}
        hydrated = reader.batch_get([nb["id"] for nb in neighbours])

        recommendations = [
            {
                **_brief(movie),
                "score": scores[movie["movie_id"]],
                "rating": movie.get("vote_average"),
                "poster_path": movie.get("poster_path"),
                "overview": movie.get("overview_short"),
                "genres": movie.get("genres", []),
            }
            for movie in hydrated
        ]
        return jsonify(
            {
                "found": True,
                "query": _brief(item),
                "method": method,
                "recommendations": recommendations,
            }
        )

    return app
