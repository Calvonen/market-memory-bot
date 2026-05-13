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
            x=list(range(len(current))),
            y=normalize_close(current),
            mode="lines",
            name="Current",
            line=dict(color="black", width=3),
        )
    )

    for m in matches:
        color = "red" if m.pivot.pivot_type == "peak" else "green"
        label = f"{m.pivot.pivot_type} {m.pivot.index.date()} ({m.score:.3f})"
        fig.add_trace(
            go.Scatter(
                x=list(range(len(m.historical_window))),
                y=normalize_close(m.historical_window),
                mode="lines",
                name=label,
                line=dict(color=color, width=1.5),
                opacity=0.75,
            )
        )

    fig.update_layout(
        title="Market Memory: Current vs Historical Pre-Turning-Point Windows",
        xaxis_title="Trading days (window)",
        yaxis_title="Normalized Close (start=100)",
        template="plotly_white",
    )
    return fig
