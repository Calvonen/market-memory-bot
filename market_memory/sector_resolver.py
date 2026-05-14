from __future__ import annotations

import yfinance as yf


def _map_sector(text: str) -> str:
    normalized = text.lower()

    mapping: list[tuple[tuple[str, ...], str]] = [
        (("technology", "consumer electronics", "software"), "teknologia"),
        (("industrials", "machinery", "specialty industrial machinery"), "teollisuus"),
        (("banks", "financial services"), "pankit"),
        (("paper", "packaging", "forest products", "pulp"), "paperiteollisuus"),
    ]

    for keywords, sector_name in mapping:
        if any(keyword in normalized for keyword in keywords):
            return sector_name

    return "yleinen"


def resolve_sector(ticker: str) -> tuple[str, str]:
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return "yleinen", ""

    source_sector = str(info.get("sector") or "").strip()
    source_industry = str(info.get("industry") or "").strip()
    source_text = " / ".join(part for part in [source_sector, source_industry] if part)

    lookup_text = " ".join(part for part in [source_sector, source_industry] if part)
    if not lookup_text:
        return "yleinen", source_text

    return _map_sector(lookup_text), source_text
