"""Render the two docs/comparison.md charts from a metrics.json.

Usage: python scripts/plot_comparison.py [--run-date 2026-09-23]
"""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "img"

# Entity colours stay fixed across both charts; random is a floor, so neutral.
COLOURS = {"tfidf": "#2a78d6", "word2vec": "#eb6834", "random": "#8a8984"}
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def accuracy_by_method(rows, meta, path):
    fig, ax = plt.subplots(figsize=(6, 4), facecolor=SURFACE)
    _style(ax)
    methods = [r["method"] for r in rows]
    vals = [r["keyword_jaccard_at_k"] for r in rows]
    bars = ax.bar(methods, vals, width=0.55,
                  color=[COLOURS[m] for m in methods], edgecolor=SURFACE, linewidth=2)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v, f"{v:.4f}",
                ha="center", va="bottom", fontsize=9, color=INK)
    rnd = next(r["keyword_jaccard_at_k"] for r in rows if r["method"] == "random")
    for r in rows:
        if r["method"] != "random":
            ax.text(methods.index(r["method"]), r["keyword_jaccard_at_k"] / 2,
                    f"{r['keyword_jaccard_at_k'] / rnd:.0f}× random",
                    ha="center", va="center", fontsize=9, color="white")
    ax.set_ylabel(f"Keyword Jaccard@{meta['k']}", color=INK_2)
    ax.set_title(f"Keyword Jaccard@{meta['k']} by method", loc="left", color=INK, fontsize=11)
    fig.text(0.01, 0.01,
             f"{meta['n_keyword_queries']:,} keyword queries, catalog {meta['catalog_size']:,}, "
             f"run {meta['run_date']}", fontsize=8, color=INK_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def cost_vs_accuracy(rows, meta, path):
    fig, ax = plt.subplots(figsize=(6, 4), facecolor=SURFACE)
    _style(ax)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    for r in rows:
        if r["fit_seconds"] is None:
            continue
        ax.scatter(r["fit_seconds"], r["keyword_jaccard_at_k"], s=80,
                   color=COLOURS[r["method"]], edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.annotate(f"{r['method']}\n{r['fit_seconds']:.1f}s fit, {r['artifact_mb']:.1f} MB",
                    (r["fit_seconds"], r["keyword_jaccard_at_k"]),
                    xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=9, color=INK)
    rnd = next(r for r in rows if r["method"] == "random")
    ax.axhline(rnd["keyword_jaccard_at_k"], color=COLOURS["random"], linestyle="--", linewidth=1.5)
    ax.text(0.5, rnd["keyword_jaccard_at_k"], f"random floor {rnd['keyword_jaccard_at_k']:.4f}",
            va="bottom", fontsize=8, color=INK_2)
    ax.set_xlim(0, 25)
    ax.set_ylim(0, 0.045)
    ax.set_xlabel("fit_seconds (full catalog)", color=INK_2)
    ax.set_ylabel(f"Keyword Jaccard@{meta['k']}", color=INK_2)
    ax.set_title("Cost vs accuracy", loc="left", color=INK, fontsize=11)
    fig.text(0.01, 0.01,
             f"Up and left is better. Run {meta['run_date']}, catalog {meta['catalog_size']:,}.",
             fontsize=8, color=INK_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-date", default="2026-09-23")
    args = p.parse_args()
    meta = json.loads((ROOT / "artifacts" / "comparison" / args.run_date / "metrics.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    accuracy_by_method(meta["rows"], meta, OUT / "accuracy_by_method.png")
    cost_vs_accuracy(meta["rows"], meta, OUT / "cost_vs_accuracy.png")
    print(f"wrote {OUT}/accuracy_by_method.png, {OUT}/cost_vs_accuracy.png")


if __name__ == "__main__":
    main()
