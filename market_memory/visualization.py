from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from market_memory.similarity import MatchResult


def normalize_close(window: pd.DataFrame) -> pd.Series:
    base = window["Close"].iloc[0]
    return (window["Close"] / base) * 100


def plot_overlay(current: pd.DataFrame, matches: list[MatchResult]) -> go.Figure:
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=list(range(-len(current), 0)),
            y=normalize_close(current),
            mode="lines",
            name="Current",
            line=dict(color="black", width=3),
        )
    )

    for m in matches:
        color = "red" if m.pivot.pivot_type == "peak" else "green"
        label = f"{m.pivot.pivot_type} {m.pivot.index.date()} ({m.score:.3f})"

        centered = m.historical_window.copy()
        pivot_idx = len(m.historical_pre_window)
        base = centered["Close"].iloc[0]
        normalized = (centered["Close"] / base) * 100

        pre_x = list(range(-len(m.historical_pre_window), 0))
        post_x = list(range(1, len(m.historical_post_window) + 1))

        fig.add_trace(
            go.Scatter(
                x=pre_x,
                y=normalized.iloc[:pivot_idx],
                mode="lines",
                name=f"{label} pre",
                line=dict(color=color, width=2, dash="dash"),
                opacity=0.75,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[0],
                y=[normalized.iloc[pivot_idx]],
                mode="markers",
                name=f"{label} pivot",
                marker=dict(color=color, size=8),
                opacity=0.9,
                showlegend=False,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=post_x,
                y=normalized.iloc[pivot_idx + 1 :],
                mode="lines",
                name=f"{label} post",
                line=dict(color=color, width=3),
                opacity=0.95,
            )
        )

    fig.add_vline(x=0, line_width=2, line_dash="dot", line_color="gray")

    fig.update_layout(
        title="Market Memory: Pivot-centered overlay (-15 ... 0 ... +15)",
        xaxis_title="Trading days relative to pivot",
        yaxis_title="Normalized Close (start=100)",
        template="plotly_white",
    )
    return fig


def plot_5y_pivot_map(history: pd.DataFrame, matches: list[MatchResult]) -> go.Figure:
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=history.index,
            y=history["Close"],
            mode="lines",
            name="Close (5y)",
            line=dict(color="#1f2937", width=2),
            hovertemplate="Date: %{x|%Y-%m-%d}<br>Close: %{y:.2f}<extra></extra>",
        )
    )

    if matches:
        top_pivot_index = matches[0].pivot.index
        for pivot_type, color in (("bottom", "green"), ("peak", "red")):
            group = [m for m in matches if m.pivot.pivot_type == pivot_type]
            if not group:
                continue

            customdata = [
                [
                    m.pivot.index.date().isoformat(),
                    m.score,
                    m.pivot.pivot_type,
                    float("nan") if m.return_plus_5d is None else m.return_plus_5d,
                    float("nan") if m.return_plus_15d is None else m.return_plus_15d,
                ]
                for m in group
            ]
            sizes = [14 if m.pivot.index == top_pivot_index else 9 for m in group]

            fig.add_trace(
                go.Scatter(
                    x=[m.pivot.index for m in group],
                    y=[history.loc[m.pivot.index, "Close"] for m in group],
                    mode="markers",
                    name=f"{pivot_type} pivots",
                    marker=dict(color=color, size=sizes, line=dict(color="white", width=1)),
                    customdata=customdata,
                    hovertemplate=(
                        "Pivot date: %{customdata[0]}<br>"
                        "Similarity: %{customdata[1]:.3f}<br>"
                        "Pivot type: %{customdata[2]}<br>"
                        "Return +5d: %{customdata[3]:+.2f}%<br>"
                        "Return +15d: %{customdata[4]:+.2f}%<extra></extra>"
                    ),
                )
            )

    fig.update_layout(
        title="5y pivot map",
        xaxis_title="Date",
        yaxis_title="Close",
        template="plotly_white",
    )
    return fig
