from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf


def _format_published(raw_value: object) -> str | None:
    if raw_value is None:
        return None

    if isinstance(raw_value, (int, float)):
        try:
            return datetime.fromtimestamp(raw_value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(raw_value, str):
        value = raw_value.strip()
        return value or None

    return None


def fetch_latest_news(ticker: str, limit: int = 5) -> list[dict[str, str | None]]:
    """Fetch latest news headlines for a ticker from yfinance.

    Returns dictionaries with keys: title, publisher, link, published.
    """
    items: list[dict[str, str | None]] = []

    if not ticker:
        return items

    news_items = yf.Ticker(ticker).news or []
    for item in news_items:
        title = item.get("title")
        link = item.get("link") or item.get("url")
        if not title or not link:
            continue

        items.append(
            {
                "title": title,
                "publisher": item.get("publisher"),
                "link": link,
                "published": _format_published(item.get("providerPublishTime") or item.get("published")),
            }
        )

        if len(items) >= limit:
            break

    return items
