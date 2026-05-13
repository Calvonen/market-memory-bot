from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

PivotType = Literal["peak", "bottom"]


@dataclass(frozen=True)
class Pivot:
    index: pd.Timestamp
    pivot_type: PivotType


def detect_pivots(df: pd.DataFrame, window: int = 5) -> list[Pivot]:
    """Detect local peaks/bottoms using symmetric rolling windows."""
    pivots: list[Pivot] = []
    highs = df["High"]
    lows = df["Low"]

    for i in range(window, len(df) - window):
        sl = slice(i - window, i + window + 1)
        center_idx = df.index[i]
        if highs.iloc[i] == highs.iloc[sl].max():
            pivots.append(Pivot(index=center_idx, pivot_type="peak"))
        if lows.iloc[i] == lows.iloc[sl].min():
            pivots.append(Pivot(index=center_idx, pivot_type="bottom"))

    return sorted(pivots, key=lambda p: p.index)
