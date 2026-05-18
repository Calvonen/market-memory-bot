from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

import feedparser

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


def _as_utc_datetime(value: object) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    return None


def _published_from_struct_time(value: object) -> datetime | None:
    if value is None:
        return None

    try:
        return datetime.fromtimestamp(__import__("calendar").timegm(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _apply_freshness_filter(
    items: list[dict[str, str | datetime | None]],
    max_age_days: int,
    limit: int,
) -> list[dict[str, str | None]]:
    safe_max_age_days = max(1, int(max_age_days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=safe_max_age_days)

    fresh_items: list[dict[str, str | datetime | None]] = []
    for item in items:
        published_dt = _as_utc_datetime(item.get("published_dt"))
        if published_dt is None:
            continue
        if published_dt >= cutoff:
            normalized_item = dict(item)
            normalized_item["published_dt"] = published_dt
            normalized_item["published"] = _format_published(published_dt.timestamp())
            fresh_items.append(normalized_item)

    fresh_items.sort(key=lambda item: item.get("published_dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    result: list[dict[str, str | None]] = []
    for item in fresh_items[:limit]:
        payload = dict(item)
        payload.pop("published_dt", None)
        result.append(payload)

    return result


def _normalize_rss_entry(entry: Any) -> dict[str, str | datetime | None] | None:
    title = getattr(entry, "title", None)
    link = getattr(entry, "link", None)
    if not title or not link:
        return None

    publisher = None
    source = getattr(entry, "source", None)
    if isinstance(source, dict):
        publisher = source.get("title")

    published = getattr(entry, "published", None) or getattr(entry, "updated", None)
    published_dt = _published_from_struct_time(getattr(entry, "published_parsed", None))
    if published_dt is None:
        published_dt = _published_from_struct_time(getattr(entry, "updated_parsed", None))

    return {
        "title": str(title),
        "publisher": str(publisher) if publisher else None,
        "link": str(link),
        "published": _format_published(published_dt.timestamp()) if published_dt else _format_published(published),
        "published_dt": published_dt,
        "source": "Google News RSS",
    }


def _fetch_from_google_news(query: str, limit: int) -> list[dict[str, str | datetime | None]]:
    items: list[dict[str, str | datetime | None]] = []
    encoded_query = quote_plus(query)
    rss_url = GOOGLE_NEWS_RSS_URL.format(query=encoded_query)

    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return items

    if getattr(feed, "bozo", False):
        return items

    for entry in getattr(feed, "entries", []):
        normalized = _normalize_rss_entry(entry)
        if normalized is None:
            continue
        items.append(normalized)
        if len(items) >= limit:
            break

    return items


def fetch_latest_news(
    ticker: str,
    company_name: str | None = None,
    limit: int = 10,
    max_age_days: int = 90,
) -> list[dict[str, str | None]]:
    """Fetch latest news headlines from Google News RSS.

    Returns dictionaries with keys: title, publisher, link, published, source.
    """
    if not ticker:
        return []

    normalized_limit = min(10, max(1, int(limit)))
    normalized_company = (company_name or "").strip()

    search_limit = max(normalized_limit * 3, normalized_limit)

    queries: list[str] = []
    if normalized_company:
        queries.append(f"{normalized_company} stock OR {ticker}")
    else:
        queries.append(f"{ticker} stock")

    # Finnish listings often benefit from explicit Helsinki context.
    if normalized_company and ticker.upper().endswith(".HE"):
        queries.append(f"{normalized_company} Helsinki stock")

    if normalized_company:
        queries.append(normalized_company)

    for query in queries:
        try:
            rss_items = _fetch_from_google_news(query=query, limit=search_limit)
            fresh_rss_items = _apply_freshness_filter(rss_items, max_age_days=max_age_days, limit=normalized_limit)
            if fresh_rss_items:
                return fresh_rss_items
        except Exception:
            continue

    return []
