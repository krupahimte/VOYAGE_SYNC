"""
Benchmark charts (static PNGs for the report), drawn from the drift-on results.

Each strategy keeps one colour in every chart (the validated reference
categorical order, slots 1–6). Thin bars with a surface-coloured gap, a
recessive grid, one y-axis per chart, a legend on every multi-series chart,
and values printed on the bars only where they carry the message.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from eval.baselines import STRATEGIES  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
COLORS = {  # fixed by entity, never by rank
    "ours": "#2a78d6",
    "b1_full": "#eb6834",
    "b2_naive": "#1baf7a",
    "abl_no_contracts": "#eda100",
    "abl_no_frozen": "#e87ba4",
    "abl_no_gate": "#008300",
}
LABELS = {s.name: s.label for s in STRATEGIES}


def _style(ax: Any, title: str, ylabel: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=12, color=TEXT, pad=12, fontweight="bold")
    ax.set_ylabel(ylabel, color=TEXT_2, fontsize=9)
    ax.tick_params(colors=TEXT_2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)


def _figure(width: float = 9.0, height: float = 4.6):
    fig, ax = plt.subplots(figsize=(width, height), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    return fig, ax


def _save(fig: Any, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def _grouped(ax: Any, groups: list[str], series: list[str], value) -> float:
    n = len(series)
    width = 0.8 / n
    for i, name in enumerate(series):
        xs = [g + (i - (n - 1) / 2) * width for g in range(len(groups))]
        ax.bar(xs, [value(name, g) for g in groups], width=width, color=COLORS[name], label=LABELS[name],
               edgecolor=SURFACE, linewidth=1.2)
    ax.set_xticks(range(len(groups)))
    return width


def quality(agg: dict[str, Any], out: Path) -> None:
    metrics = [("precision", "Precision"), ("recall", "Recall"), ("f1", "F1"), ("stability", "Plan stability")]
    fig, ax = _figure(10, 4.8)
    names = list(agg)
    _grouped(ax, [m for m, _ in metrics], names, lambda n, m: agg[n][m] * 100)
    ax.set_xticklabels([label for _, label in metrics])
    ax.set_ylim(0, 112)
    # Direct labels only for ours vs the two baselines.
    n = len(names)
    for i, name in enumerate(names):
        if name not in ("ours", "b1_full", "b2_naive"):
            continue
        for g, (m, _) in enumerate(metrics):
            x = g + (i - (n - 1) / 2) * (0.8 / n)
            ax.text(x, agg[name][m] * 100 + 1.5, f"{agg[name][m] * 100:.0f}", ha="center", fontsize=7, color=TEXT_2)
    _style(ax, "Replan quality: ours vs baselines and ablations (mean over scenarios)", "%")
    ax.legend(ncol=3, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.08), labelcolor=TEXT_2)
    _save(fig, out / "quality.png")


def regenerated_by_kind(kinds: dict[str, Any], out: Path) -> None:
    series = ["ours", "b2_naive", "b1_full"]
    names = list(kinds)
    fig, ax = _figure(10, 5)
    width = _grouped(ax, names, series, lambda s, k: kinds[k][s]["regenerated_mean"])
    ax.hlines([kinds[k]["ours"]["affected_mean"] for k in names], [g - 0.44 for g in range(len(names))],
              [g + 0.44 for g in range(len(names))], colors=TEXT, linewidths=1.6, linestyles="dashed", zorder=3,
              label="Oracle |A| (truly affected)")
    ax.set_xticklabels([k.replace("_", "\n") for k in names], fontsize=8)
    for g, k in enumerate(names):
        ax.text(g - width, kinds[k]["ours"]["regenerated_mean"] + 0.3, f"{kinds[k]['ours']['regenerated_mean']:.1f}",
                ha="center", fontsize=7, color=TEXT_2)
    _style(ax, "Tasks regenerated per disruption, by kind (mean)", "tasks regenerated")
    ax.legend(ncol=4, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16), labelcolor=TEXT_2)
    _save(fig, out / "regenerated_by_kind.png")


def _single(agg: dict[str, Any], key: str, title: str, ylabel: str, path: Path, fmt: str, scale: float = 1.0,
            whisker: str | None = None) -> None:
    fig, ax = _figure(8.5, 4.2)
    names = list(agg)
    values = [agg[n][key] * scale for n in names]
    ax.bar(range(len(names)), values, width=0.6, color=[COLORS[n] for n in names], edgecolor=SURFACE, linewidth=1.2)
    if whisker:
        ax.scatter(range(len(names)), [agg[n][whisker] for n in names], marker="_", s=300, color=TEXT,
                   linewidths=1.6, zorder=3, label="p95")
        ax.legend(fontsize=8, frameon=False, labelcolor=TEXT_2)
    top = max(values + ([agg[n][whisker] for n in names] if whisker else [])) or 1
    for i, v in enumerate(values):
        ax.text(i, v + top * 0.02, fmt.format(v), ha="center", fontsize=8, color=TEXT_2)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([LABELS[n].replace(": ", ":\n") for n in names], fontsize=8)
    ax.set_ylim(0, top * 1.15)
    _style(ax, title, ylabel)
    _save(fig, path)


def write_charts(rows: list[dict[str, Any]], summary: dict[str, Any], out: Path) -> list[Path]:
    mode = "on" if "on" in summary["aggregate"] else next(iter(summary["aggregate"]))
    agg, kinds = summary["aggregate"][mode], summary["by_kind"][mode]
    quality(agg, out)
    regenerated_by_kind(kinds, out)
    _single(agg, "llm_calls_mean", "LLM calls per re-plan (mean)", "calls", out / "cost_llm.png", "{:.2f}")
    _single(agg, "api_calls_mean", "API calls per re-plan (mean)", "calls", out / "cost_api.png", "{:.1f}")
    _single(agg, "latency_median_ms", "Recovery latency: median, with p95 marker (mock mode)", "ms",
            out / "latency.png", "{:.1f}", whisker="latency_p95_ms")
    _single(agg, "unsafe_action_rate", "Unsafe-action rate: T2 actions executed without approval", "%",
            out / "unsafe_actions.png", "{:.0f} %", scale=100)
    return [out / f for f in ("quality.png", "regenerated_by_kind.png", "cost_llm.png", "cost_api.png",
                              "latency.png", "unsafe_actions.png")]
