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
