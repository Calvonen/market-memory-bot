from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import feedparser
import yfinance as yf

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


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


def _normalize_yfinance_item(item: dict[str, Any]) -> dict[str, str | None] | None:
    title = item.get("title")
    link = item.get("link") or item.get("url")
    if not title or not link:
        return None

    return {
        "title": str(title),
        "publisher": item.get("publisher"),
        "link": str(link),
        "published": _format_published(item.get("providerPublishTime") or item.get("published")),
        "source": "yfinance",
    }


def _normalize_rss_entry(entry: Any) -> dict[str, str | None] | None:
    title = getattr(entry, "title", None)
    link = getattr(entry, "link", None)
    if not title or not link:
        return None

    publisher = None
    source = getattr(entry, "source", None)
    if isinstance(source, dict):
        publisher = source.get("title")

    published = getattr(entry, "published", None) or getattr(entry, "updated", None)
    return {
        "title": str(title),
        "publisher": str(publisher) if publisher else None,
        "link": str(link),
        "published": _format_published(published),
        "source": "Google News RSS fallback",
    }


def _fetch_from_yfinance(ticker: str, limit: int) -> list[dict[str, str | None]]:
    items: list[dict[str, str | None]] = []
    try:
        news_items = yf.Ticker(ticker).news or []
    except Exception:
        return items

    for raw_item in news_items:
        if not isinstance(raw_item, dict):
            continue
        normalized = _normalize_yfinance_item(raw_item)
        if normalized is None:
            continue
        items.append(normalized)
        if len(items) >= limit:
            break
    return items


def _fetch_from_google_news(query: str, limit: int) -> list[dict[str, str | None]]:
    items: list[dict[str, str | None]] = []
    encoded_query = quote_plus(query)
    rss_url = GOOGLE_NEWS_RSS_URL.format(query=encoded_query)

    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return items

    for entry in getattr(feed, "entries", []):
        normalized = _normalize_rss_entry(entry)
        if normalized is None:
            continue
        items.append(normalized)
        if len(items) >= limit:
            break

    return items


def fetch_latest_news(ticker: str, company_name: str | None = None, limit: int = 5) -> list[dict[str, str | None]]:
    """Fetch latest news headlines.

    Uses yfinance first and falls back to Google News RSS when needed.
    Returns dictionaries with keys: title, publisher, link, published, source.
    """
    if not ticker:
        return []

    limit = max(1, int(limit))

    yf_items = _fetch_from_yfinance(ticker=ticker, limit=limit)
    if yf_items:
        return yf_items

    query_name = (company_name or "").strip() or ticker
    search_query = f"{query_name} stock"
    return _fetch_from_google_news(query=search_query, limit=limit)
