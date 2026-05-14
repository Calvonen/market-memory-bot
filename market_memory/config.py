from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SectorMode = Literal["all", "bottom", "peak"]


@dataclass(frozen=True)
class SectorSettings:
    name: str
    rsi_low: float
    rsi_high: float
    dip_threshold_pct: float
    peak_threshold_pct: float
    min_atr_pct: float
    max_atr_pct: float


SECTOR_SETTINGS: dict[str, SectorSettings] = {
    "teknologia": SectorSettings("teknologia", rsi_low=35, rsi_high=72, dip_threshold_pct=3.5, peak_threshold_pct=4.0, min_atr_pct=1.2, max_atr_pct=6.5),
    "paperiteollisuus": SectorSettings("paperiteollisuus", rsi_low=32, rsi_high=70, dip_threshold_pct=2.8, peak_threshold_pct=3.2, min_atr_pct=0.9, max_atr_pct=4.8),
    "pankit": SectorSettings("pankit", rsi_low=30, rsi_high=68, dip_threshold_pct=2.2, peak_threshold_pct=2.6, min_atr_pct=0.7, max_atr_pct=3.8),
    "teollisuus": SectorSettings("teollisuus", rsi_low=33, rsi_high=70, dip_threshold_pct=2.6, peak_threshold_pct=3.0, min_atr_pct=0.8, max_atr_pct=4.4),
    "yleinen": SectorSettings("yleinen", rsi_low=33, rsi_high=70, dip_threshold_pct=3.0, peak_threshold_pct=3.4, min_atr_pct=0.9, max_atr_pct=4.8),
}

