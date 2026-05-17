"""Built-in visualization code templates for the figure CodeGen agent."""

from __future__ import annotations

def _esc(s: str) -> str:
    """Escape curly braces in user-provided strings for str.format()."""
    return s.replace("{", "{{").replace("}", "}}")


def _is_degenerate_data(values: list[float]) -> bool:
    """Return True if data values are too degenerate to produce a useful chart.

    Rejects: empty lists, all-zero, all-identical, or single-value data.
    """
    if not values or len(values) < 1:
        return True
    if all(v == 0 for v in values):
        return True
    if len(values) >= 2 and len(set(round(v, 6) for v in values)) <= 1:
        return True
    return False


_METRIC_DISPLAY_NAMES: dict[str, str] = {
    "primary_metric": "Performance",
    "accuracy": "Accuracy (%)",
    "loss": "Loss",
    "f1_score": "F1 Score",
    "precision": "Precision",
    "recall": "Recall",
    "reward": "Reward",
    "return": "Return",
    "mse": "MSE",
    "mae": "MAE",
    "rmse": "RMSE",
    "bleu": "BLEU",
    "rouge": "ROUGE",
    "perplexity": "Perplexity",
    "auc": "AUC",
}


def _humanize_label(raw: str) -> str:
    """Convert raw metric names like 'primary_metric' to human-readable labels."""
    if not raw:
        return ""
    low = raw.lower().strip()
    if low in _METRIC_DISPLAY_NAMES:
        return _METRIC_DISPLAY_NAMES[low]
    # Convert snake_case to Title Case
    return raw.replace("_", " ").title()


_TEMPLATE_BAR_COMPARISON = '''
{style_preamble}

# Data
conditions = {conditions}
values = {values}
ci_low = {ci_low}
ci_high = {ci_high}

# Plot
fig, ax = plt.subplots(figsize=({width}, {height}), constrained_layout=True)
x = np.arange(len(conditions))
bar_colors = [COLORS[i % len(COLORS)] for i in range(len(conditions))]

yerr_lo = [max(0, v - lo) for v, lo in zip(values, ci_low)]
yerr_hi = [max(0, hi - v) for v, hi in zip(values, ci_high)]

bars = ax.bar(x, values, color=bar_colors, alpha=0.85, edgecolor="white", linewidth=0.5)
ax.errorbar(x, values, yerr=[yerr_lo, yerr_hi],
            fmt="none", ecolor="#333", capsize=4, capthick=1.2, linewidth=1.2)

# Value labels
offset = max(yerr_hi) * 0.08 if yerr_hi and max(yerr_hi) > 0 else max(values) * 0.02
for i, v in enumerate(values):
    ax.text(i, v + offset, f"{{v:.4f}}", ha="center", va="bottom", fontweight="bold")

ax.set_xlabel("{x_label}")
ax.set_ylabel("{y_label}")
ax.set_title("{title}")
ax.set_xticks(x)
ax.set_xticklabels([c.replace("_", " ") for c in conditions], rotation=25, ha="right")
ax.grid(True, axis="y", alpha=0.3)
ax.set_axisbelow(True)
fig.savefig("{output_path}")
plt.close(fig)
print(f"Saved: {output_path}")
'''


_TEMPLATE_GROUPED_BAR = '''
{style_preamble}

# Data: conditions x metrics
conditions = {conditions}
metric_names = {metric_names}
# data_matrix[i][j] = value for condition i, metric j
data_matrix = {data_matrix}

# Plot
n_groups = len(conditions)
n_bars = len(metric_names)
fig, ax = plt.subplots(figsize=({width}, {height}), constrained_layout=True)
x = np.arange(n_groups)
bar_width = 0.8 / n_bars

for j, metric in enumerate(metric_names):
    offset = (j - n_bars / 2 + 0.5) * bar_width
    vals = [data_matrix[i][j] for i in range(n_groups)]
    ax.bar(x + offset, vals, bar_width, label=metric.replace("_", " "),
           color=COLORS[j % len(COLORS)], alpha=0.85, edgecolor="white", linewidth=0.5)

ax.set_xlabel("{x_label}")
ax.set_ylabel("{y_label}")
ax.set_title("{title}")
ax.set_xticks(x)
ax.set_xticklabels([c.replace("_", " ") for c in conditions], rotation=25, ha="right")
ax.legend(loc="upper left", bbox_to_anchor=(0, 1), framealpha=0.9, edgecolor="gray")
ax.grid(True, axis="y", alpha=0.3)
ax.set_axisbelow(True)
fig.savefig("{output_path}")
plt.close(fig)
print(f"Saved: {output_path}")
'''


_TEMPLATE_TRAINING_CURVE = '''
{style_preamble}

# Data: each series is (label, epochs, values, [optional std])
series_data = {series_data}

fig, ax = plt.subplots(figsize=({width}, {height}), constrained_layout=True)

for idx, series in enumerate(series_data):
    label = series["label"]
    epochs = series["epochs"]
    values = series["values"]
    color = COLORS[idx % len(COLORS)]
    ls = LINE_STYLES[idx % len(LINE_STYLES)]
    marker = MARKERS[idx % len(MARKERS)]

    ax.plot(epochs, values, linestyle=ls, color=color, linewidth=1.5,
            marker=marker, markersize=4, markevery=max(1, len(epochs)//10),
            label=label.replace("_", " "))

    if "std" in series and series["std"]:
        std = series["std"]
        lower = [v - s for v, s in zip(values, std)]
        upper = [v + s for v, s in zip(values, std)]
        ax.fill_between(epochs, lower, upper, alpha=0.15, color=color)

ax.set_xlabel("{x_label}")
ax.set_ylabel("{y_label}")
ax.set_title("{title}")
ax.legend(loc="best", framealpha=0.9, edgecolor="gray")
ax.grid(True, alpha=0.3)
fig.savefig("{output_path}")
plt.close(fig)
print(f"Saved: {output_path}")
'''


_TEMPLATE_HEATMAP = '''
{style_preamble}

# Data
row_labels = {row_labels}
col_labels = {col_labels}
data = np.array({data_matrix})

fig, ax = plt.subplots(figsize=({width}, {height}), constrained_layout=True)
im = ax.imshow(data, cmap="cividis", aspect="auto")

ax.set_xticks(np.arange(len(col_labels)))
ax.set_yticks(np.arange(len(row_labels)))
ax.set_xticklabels(col_labels, rotation=45, ha="right")
ax.set_yticklabels(row_labels)

# Annotate cells
for i in range(len(row_labels)):
    for j in range(len(col_labels)):
        val = data[i, j]
        color = "white" if val > (data.max() + data.min()) / 2 else "black"
        ax.text(j, i, f"{{val:.3f}}", ha="center", va="center", color=color)

ax.set_xlabel("{x_label}")
ax.set_ylabel("{y_label}")
ax.set_title("{title}")
fig.colorbar(im, ax=ax, shrink=0.8)
fig.savefig("{output_path}")
plt.close(fig)
print(f"Saved: {output_path}")
'''


_TEMPLATE_LINE_MULTI = '''
{style_preamble}

# Data: list of series dicts with label, x, y, [std]
series_data = {series_data}

fig, ax = plt.subplots(figsize=({width}, {height}), constrained_layout=True)

for idx, series in enumerate(series_data):
    label = series["label"]
    x = series["x"]
    y = series["y"]
    color = COLORS[idx % len(COLORS)]
    ls = LINE_STYLES[idx % len(LINE_STYLES)]
    marker = MARKERS[idx % len(MARKERS)]

    ax.plot(x, y, linestyle=ls, color=color, linewidth=1.5,
            marker=marker, markersize=4, markevery=max(1, len(x)//8),
            label=label.replace("_", " "))

    if "std" in series and series["std"]:
        std = series["std"]
        lower = [v - s for v, s in zip(y, std)]
        upper = [v + s for v, s in zip(y, std)]
        ax.fill_between(x, lower, upper, alpha=0.15, color=color)

ax.set_xlabel("{x_label}")
ax.set_ylabel("{y_label}")
ax.set_title("{title}")
ax.legend(loc="best", framealpha=0.9, edgecolor="gray")
ax.grid(True, alpha=0.3)
fig.savefig("{output_path}")
plt.close(fig)
print(f"Saved: {output_path}")
'''


_TEMPLATE_SCATTER = '''
{style_preamble}

# Data: list of groups with label, x, y
groups = {groups}

fig, ax = plt.subplots(figsize=({width}, {height}), constrained_layout=True)

for idx, group in enumerate(groups):
    label = group["label"]
    x = group["x"]
    y = group["y"]
    color = COLORS[idx % len(COLORS)]
    marker = MARKERS[idx % len(MARKERS)]
    ax.scatter(x, y, c=color, marker=marker, s=40, alpha=0.7, label=label.replace("_", " "))

ax.set_xlabel("{x_label}")
ax.set_ylabel("{y_label}")
ax.set_title("{title}")
ax.legend(loc="best", framealpha=0.9, edgecolor="gray")
ax.grid(True, alpha=0.3)
fig.savefig("{output_path}")
plt.close(fig)
print(f"Saved: {output_path}")
'''


_TEMPLATES: dict[str, str] = {
    "bar_comparison": _TEMPLATE_BAR_COMPARISON,
    "ablation_grouped": _TEMPLATE_BAR_COMPARISON,  # Same template, different data
    "grouped_bar": _TEMPLATE_GROUPED_BAR,
    "training_curve": _TEMPLATE_TRAINING_CURVE,
    "loss_curve": _TEMPLATE_TRAINING_CURVE,
    "heatmap": _TEMPLATE_HEATMAP,
    "confusion_matrix": _TEMPLATE_HEATMAP,
    "line_multi": _TEMPLATE_LINE_MULTI,
    "scatter_plot": _TEMPLATE_SCATTER,
}


_LATEX_TEMPLATE_BAR_COMPARISON = r'''
\begin{{figure}}[htbp]
\centering
\begin{{tikzpicture}}
\begin{{axis}}[
    ybar,
    bar width=15pt,
    width={width}cm,
    height={height}cm,
    xlabel={{{x_label}}},
    ylabel={{{y_label}}},
    title={{{title}}},
    symbolic x coords={{{x_coords}}},
    xtick=data,
    x tick label style={{rotate=25, anchor=east, font=\small}},
    ymin=0,
    nodes near coords,
    nodes near coords align={{vertical}},
    every node near coord/.append style={{font=\tiny}},
    grid=major,
    grid style={{dashed, gray!30}},
]
\addplot[fill=blue!60, draw=blue!80] coordinates {{{coords}}};
\end{{axis}}
\end{{tikzpicture}}
\caption{{{caption}}}
\label{{fig:{figure_id}}}
\end{{figure}}
'''


_LATEX_TEMPLATE_LINE = r'''
\begin{{figure}}[htbp]
\centering
\begin{{tikzpicture}}
\begin{{axis}}[
    width={width}cm,
    height={height}cm,
    xlabel={{{x_label}}},
    ylabel={{{y_label}}},
    title={{{title}}},
    legend pos=north west,
    grid=major,
    grid style={{dashed, gray!30}},
    cycle list name=color list,
]
{plot_commands}
\end{{axis}}
\end{{tikzpicture}}
\caption{{{caption}}}
\label{{fig:{figure_id}}}
\end{{figure}}
'''


_LATEX_TEMPLATE_HEATMAP = r'''
\begin{{figure}}[htbp]
\centering
\begin{{tikzpicture}}
\begin{{axis}}[
    colormap/viridis,
    colorbar,
    width={width}cm,
    height={height}cm,
    xlabel={{{x_label}}},
    ylabel={{{y_label}}},
    title={{{title}}},
    point meta min={meta_min},
    point meta max={meta_max},
    xtick={{{xtick}}},
    ytick={{{ytick}}},
    xticklabels={{{xticklabels}}},
    yticklabels={{{yticklabels}}},
    x tick label style={{rotate=45, anchor=east, font=\small}},
]
\addplot[matrix plot*, mesh/cols={cols}, mesh/rows={rows},
    point meta=explicit] coordinates {{
{matrix_coords}
}};
\end{{axis}}
\end{{tikzpicture}}
\caption{{{caption}}}
\label{{fig:{figure_id}}}
\end{{figure}}
'''


_LATEX_TEMPLATES: dict[str, str] = {
    "bar_comparison": _LATEX_TEMPLATE_BAR_COMPARISON,
    "ablation_grouped": _LATEX_TEMPLATE_BAR_COMPARISON,
    "training_curve": _LATEX_TEMPLATE_LINE,
    "loss_curve": _LATEX_TEMPLATE_LINE,
    "line_multi": _LATEX_TEMPLATE_LINE,
    "heatmap": _LATEX_TEMPLATE_HEATMAP,
    "confusion_matrix": _LATEX_TEMPLATE_HEATMAP,
}


__all__ = [
    "_LATEX_TEMPLATES",
    "_LATEX_TEMPLATE_BAR_COMPARISON",
    "_LATEX_TEMPLATE_HEATMAP",
    "_LATEX_TEMPLATE_LINE",
    "_METRIC_DISPLAY_NAMES",
    "_TEMPLATES",
    "_TEMPLATE_BAR_COMPARISON",
    "_TEMPLATE_GROUPED_BAR",
    "_TEMPLATE_HEATMAP",
    "_TEMPLATE_LINE_MULTI",
    "_TEMPLATE_SCATTER",
    "_TEMPLATE_TRAINING_CURVE",
    "_esc",
    "_humanize_label",
    "_is_degenerate_data",
]
