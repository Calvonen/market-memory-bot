from __future__ import annotations

from statistics import median
from typing import Iterable

import pandas as pd

from market_memory.similarity import MatchResult
from trading_system.models import ComponentAssessment, Direction


def build_technical_assessment(df: pd.DataFrame, direction: Direction) -> ComponentAssessment:
    """Translate existing indicator columns into a structured 0-20 technical score."""
    if direction not in {Direction.LONG, Direction.SHORT} or df.empty:
        return ComponentAssessment("technical", Direction.NO_TRADE, 0, 20, ("no directional hypothesis",))

    current = df.iloc[-1]
    previous = df.iloc[-2] if len(df) > 1 else current
    close = float(current["Close"])
    ema20 = close / (1.0 + float(current["dist_ema20"]))
    ema50 = close / (1.0 + float(current["dist_ema50"]))
    rsi = float(current["rsi"])
    macd = float(current["macd_hist"])
    prev_macd = float(previous["macd_hist"])
    volume_ratio = float(current["volume_ratio"])
    atr_pct = float(current["atr_pct"])

    score = 0
    reasons: list[str] = []

    trend_ok = close > ema20 > ema50 if direction is Direction.LONG else close < ema20 < ema50
    if trend_ok:
        score += 8
        reasons.append("trend aligned")
    else:
        reasons.append("trend not fully aligned")

    momentum_ok = (macd > 0 and macd >= prev_macd) if direction is Direction.LONG else (macd < 0 and macd <= prev_macd)
    if momentum_ok:
        score += 4
        reasons.append("MACD momentum aligned")

    rsi_ok = 40 <= rsi <= 70 if direction is Direction.LONG else 30 <= rsi <= 60
    if rsi_ok:
        score += 4
        reasons.append(f"RSI supportive ({rsi:.1f})")

    if volume_ratio >= 1.0:
        score += 2
        reasons.append(f"volume confirms ({volume_ratio:.2f}x)")

    if atr_pct <= 8.0:
        score += 2
        reasons.append(f"ATR within technical limit ({atr_pct:.2f}%)")

    assessment_direction = direction if score >= 8 else Direction.NO_TRADE
    return ComponentAssessment("technical", assessment_direction, score if assessment_direction is direction else 0, 20, tuple(reasons))


def build_market_memory_assessment(
    matches: Iterable[MatchResult],
    direction: Direction,
) -> ComponentAssessment:
    """Score historical analogues by similarity and directional forward outcomes."""
    match_list = list(matches)
    if direction not in {Direction.LONG, Direction.SHORT} or not match_list:
        return ComponentAssessment("market_memory", Direction.NO_TRADE, 0, 10, ("no usable historical matches",))

    usable: list[tuple[float, float]] = []
    for match in match_list:
        forward = match.return_plus_5d
        if forward is None:
            forward = match.historical_return_after_pivot
        usable.append((float(match.score), float(forward)))

    if not usable:
        return ComponentAssessment("market_memory", Direction.NO_TRADE, 0, 10, ("matches lack forward outcomes",))

    directional = [
        (similarity, forward)
        for similarity, forward in usable
        if (forward > 0 and direction is Direction.LONG) or (forward < 0 and direction is Direction.SHORT)
    ]
    consistency = len(directional) / len(usable)
    median_similarity = median(similarity for similarity, _ in usable)
    median_forward = median(forward for _, forward in usable)

    score = round(10 * max(0.0, min(1.0, median_similarity)) * consistency)
    if score < 4:
        return ComponentAssessment(
            "market_memory",
            Direction.NO_TRADE,
            0,
            10,
            (f"historical support too weak: consistency={consistency:.2f}, similarity={median_similarity:.2f}",),
        )

    return ComponentAssessment(
        "market_memory",
        direction,
        min(score, 10),
        10,
        (
            f"historical consistency={consistency:.2f}",
            f"median similarity={median_similarity:.2f}",
            f"median +5d/forward return={median_forward:+.2f}%",
        ),
    )
