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


def _fetch_from_google_news(query: str, limit: int) -> tuple[list[dict[str, str | datetime | None]], dict[str, str | int]]:
    items: list[dict[str, str | datetime | None]] = []
    encoded_query = quote_plus(query)
    rss_url = GOOGLE_NEWS_RSS_URL.format(query=encoded_query)
    debug: dict[str, str | int] = {"query": query, "rss_url": rss_url, "entry_count": 0}

    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return items, debug

    if getattr(feed, "bozo", False):
        return items, debug

    entries = list(getattr(feed, "entries", []))
    debug["entry_count"] = len(entries)

    for entry in entries:
        normalized = _normalize_rss_entry(entry)
        if normalized is None:
            continue
        items.append(normalized)
        if len(items) >= limit:
            break

    return items, debug


def fetch_latest_news_with_debug(
    ticker: str,
    company_name: str | None = None,
    limit: int = 10,
    max_age_days: int = 90,
) -> tuple[list[dict[str, str | None]], dict[str, list[object]]]:
    """Fetch latest news headlines from Google News RSS.

    Returns dictionaries with keys: title, publisher, link, published, source.
    """
    debug_info: dict[str, list[object]] = {
        "queries": [],
        "rss_urls": [],
        "results_before_filter": [],
        "results_after_filter": [],
        "errors": [],
    }

    if not ticker:
        debug_info["errors"].append("No ticker provided.")
        return [], debug_info

    normalized_limit = min(5, max(1, int(limit)))
    normalized_company = (company_name or "").strip()

    search_limit = max(normalized_limit * 3, normalized_limit)

    queries: list[str] = []
    if normalized_company:
        queries.append(f"{ticker} {normalized_company} stock")
        queries.append(f"{normalized_company} stock")
        queries.append(f"{ticker} stock")
        queries.append(f"{normalized_company} earnings")
    else:
        queries.append(f"{ticker} stock")

    total_found_before_filter = 0

    for query in queries:
        try:
            rss_items, debug = _fetch_from_google_news(query=query, limit=search_limit)
            before_filter_count = int(debug.get("entry_count", 0))
            after_filter_items = _apply_freshness_filter(rss_items, max_age_days=max_age_days, limit=normalized_limit)
            after_filter_count = len(after_filter_items)

            debug_info["queries"].append(query)
            debug_info["rss_urls"].append(str(debug.get("rss_url", "")))
            debug_info["results_before_filter"].append(before_filter_count)
            debug_info["results_after_filter"].append(after_filter_count)

            total_found_before_filter += before_filter_count

            if after_filter_items:
                return after_filter_items, debug_info
        except Exception as exc:
            debug_info["queries"].append(query)
            debug_info["rss_urls"].append(GOOGLE_NEWS_RSS_URL.format(query=quote_plus(query)))
            debug_info["results_before_filter"].append(0)
            debug_info["results_after_filter"].append(0)
            debug_info["errors"].append(f"{query}: {exc}")

    if total_found_before_filter == 0:
        debug_info["errors"].append("No RSS entries found.")

    return [], debug_info


def fetch_latest_news(
    ticker: str,
    company_name: str | None = None,
    limit: int = 10,
    max_age_days: int = 90,
 ) -> tuple[list[dict[str, str | None]], dict[str, list[object]]]:
    news, debug_info = fetch_latest_news_with_debug(
        ticker=ticker,
        company_name=company_name,
        limit=limit,
        max_age_days=max_age_days,
    )
    return news, debug_info
