from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterator, Awaitable, Callable

import requests
import websockets
from websockets.exceptions import ConnectionClosed


@dataclass(frozen=True)
class EtoroQuote:
    instrument_id: int
    bid: Decimal
    ask: Decimal
    last_execution: Decimal
    timestamp: datetime | None


@dataclass(frozen=True)
class EtoroInstrumentCandidate:
    instrument_id: int
    symbol: str | None
    display_name: str
    market: str | None = None


@dataclass(frozen=True)
class EtoroMarketUpdate:
    instrument_id: int
    bid: Decimal | None
    ask: Decimal | None
    last_execution: Decimal | None
    timestamp: datetime | None
    is_market_open: bool | None
    is_exchange_open: bool | None
    message_type: str


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def _normalise_search_text(value: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", value.upper()).split())


def _normalise_search_symbol(value: str) -> str:
    return re.sub(r"\s+", "", value.upper())


def _base_search_symbol(value: str) -> str:
    return _normalise_search_symbol(value).split(".", 1)[0]


def _topic_instrument_id(message: dict[str, Any]) -> int | None:
    topic = message.get("topic")
    if not isinstance(topic, str) or not topic.startswith("instrument:"):
        return None
    try:
        return int(topic.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def _search_candidate(row: dict[str, Any]) -> EtoroInstrumentCandidate | None:
    raw_id = row.get("instrumentId", row.get("internalInstrumentId", row.get("instrumentID")))
    try:
        instrument_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    symbol_raw = row.get("internalSymbolFull", row.get("symbol"))
    symbol = str(symbol_raw).strip() if symbol_raw not in (None, "") else None
    name_raw = row.get(
        "internalInstrumentDisplayName",
        row.get("displayName", row.get("instrumentDisplayName", row.get("name"))),
    )
    display_name = str(name_raw or symbol or "").strip()
    if not display_name:
        return None
    market_raw = row.get(
        "internalExchangeName",
        row.get("market", row.get("exchangeName", row.get("exchange"))),
    )
    market = str(market_raw).strip() if market_raw not in (None, "") else None
    return EtoroInstrumentCandidate(
        instrument_id=instrument_id,
        symbol=symbol,
        display_name=display_name,
        market=market,
    )


def _candidate_matches_search_term(candidate: EtoroInstrumentCandidate, term: str) -> bool:
    requested_symbol = _normalise_search_symbol(term)
    if candidate.symbol:
        candidate_symbol = _normalise_search_symbol(candidate.symbol)
        if candidate_symbol == requested_symbol:
            return True
        if _base_search_symbol(candidate.symbol) == _base_search_symbol(term):
            return True
    return _normalise_search_text(candidate.display_name) == _normalise_search_text(term)


def parse_etoro_stream_message(raw: str | bytes) -> tuple[EtoroMarketUpdate, ...]:
    """Parse one eToro WebSocket frame into zero or more market updates."""
    if raw in (b"\x00", "\x00"):
        return ()
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ()
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ()

    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return ()

    updates: list[EtoroMarketUpdate] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                continue
        if not isinstance(content, dict):
            continue

        raw_instrument_id = content.get("InstrumentID")
        try:
            instrument_id = int(raw_instrument_id)
        except (TypeError, ValueError):
            topic_instrument_id = _topic_instrument_id(message)
            if topic_instrument_id is None:
                continue
            instrument_id = topic_instrument_id

        updates.append(
            EtoroMarketUpdate(
                instrument_id=instrument_id,
                bid=_decimal(content.get("Bid")),
                ask=_decimal(content.get("Ask")),
                last_execution=_decimal(content.get("LastExecution")),
                timestamp=_datetime(content.get("Date")),
                is_market_open=_bool(content.get("IsMarketOpen")),
                is_exchange_open=_bool(content.get("IsExchangeOpen")),
                message_type=str(message.get("type") or "Update"),
            )
        )
    return tuple(updates)


class EtoroMarketDataProvider:
    """Backend-only eToro search, quote and WebSocket adapter."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        user_key: str | None = None,
        base_url: str = "https://public-api.etoro.com/api/v1/market-data",
        websocket_url: str = "wss://ws.etoro.com/ws",
        timeout_seconds: float = 15.0,
        reconnect_delay_seconds: float = 2.0,
        http_get: Callable[..., Any] | None = None,
        websocket_connect: Callable[..., Any] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ETORO_API_KEY") or ""
        self.user_key = user_key or os.environ.get("ETORO_USER_KEY") or ""
        if not self.api_key or not self.user_key:
            raise RuntimeError("ETORO_API_KEY and ETORO_USER_KEY are required")

        self.base_url = base_url.rstrip("/")
        self.websocket_url = websocket_url
        self.timeout_seconds = timeout_seconds
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self._http_get = http_get or requests.get
        self._websocket_connect = websocket_connect or websockets.connect
        self._sleep = sleep or asyncio.sleep

    @classmethod
    def from_env(cls) -> "EtoroMarketDataProvider":
        return cls()

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "x-user-key": self.user_key,
            "x-request-id": str(uuid.uuid4()),
        }

    def search_instruments(self, term: str) -> tuple[EtoroInstrumentCandidate, ...]:
        """Search eToro instruments conservatively across the live paged contract.

        The current live endpoint returns ``items`` and pagination metadata, and
        may ignore the supplied search term. For that shape we page through the
        full returned result set and apply exact symbol/base-symbol/display-name
        filtering locally. The older ``data`` shape remains supported as a
        single server-filtered response for backwards compatibility.
        """
        search_term = term.strip()
        if not search_term:
            return ()

        candidates: list[EtoroInstrumentCandidate] = []
        seen: set[int] = set()
        page = 1
        requested_page_size = 1000
        last_reported_page = 0

        while True:
            try:
                response = self._http_get(
                    f"{self.base_url}/search",
                    params={"search": search_term, "page": page, "pageSize": requested_page_size},
                    headers=self._headers(),
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"eToro instrument search failed: {exc}") from exc

            if not response.ok:
                raise RuntimeError(f"eToro instrument search HTTP {response.status_code}: {response.text[:500]}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("eToro instrument search returned invalid JSON") from exc

            if not isinstance(payload, dict):
                raise RuntimeError("eToro instrument search response is invalid")

            if isinstance(payload.get("data"), list):
                for row in payload["data"]:
                    if not isinstance(row, dict):
                        continue
                    candidate = _search_candidate(row)
                    if candidate is None or candidate.instrument_id in seen:
                        continue
                    candidates.append(candidate)
                    seen.add(candidate.instrument_id)
                break

            rows = payload.get("items")
            if not isinstance(rows, list):
                raise RuntimeError("eToro instrument search response is missing items")

            try:
                current_page = int(payload.get("page", page))
                current_page_size = int(payload.get("pageSize", requested_page_size))
                total_items = int(payload["totalItems"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("eToro instrument search response has invalid pagination") from exc
            if current_page < 1 or current_page_size < 1 or total_items < 0:
                raise RuntimeError("eToro instrument search response has invalid pagination")
            if current_page <= last_reported_page:
                raise RuntimeError("eToro instrument search pagination did not advance")
            last_reported_page = current_page

            for row in rows:
                if not isinstance(row, dict):
                    continue
                candidate = _search_candidate(row)
                if candidate is None or candidate.instrument_id in seen:
                    continue
                if _candidate_matches_search_term(candidate, search_term):
                    candidates.append(candidate)
                    seen.add(candidate.instrument_id)

            if current_page * current_page_size >= total_items:
                break
            if not rows:
                raise RuntimeError("eToro instrument search pagination ended before totalItems")
            page = current_page + 1

        return tuple(candidates)

    def fetch_quote(self, instrument_id: int) -> EtoroQuote:
        try:
            response = self._http_get(
                f"{self.base_url}/instruments/rates",
                params={"instrumentIds": str(instrument_id)},
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"eToro quote request failed: {exc}") from exc

        if not response.ok:
            raise RuntimeError(f"eToro quote HTTP {response.status_code}: {response.text[:500]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("eToro quote returned invalid JSON") from exc

        rates = payload.get("rates") if isinstance(payload, dict) else None
        if not isinstance(rates, list):
            raise RuntimeError("eToro quote response is missing rates")

        row = next(
            (
                item
                for item in rates
                if isinstance(item, dict) and str(item.get("instrumentID")) == str(instrument_id)
            ),
            None,
        )
        if row is None:
            raise RuntimeError(f"eToro quote response did not contain instrument {instrument_id}")

        bid = _decimal(row.get("bid"))
        ask = _decimal(row.get("ask"))
        last_execution = _decimal(row.get("lastExecution"))
        if bid is None or ask is None or last_execution is None:
            raise RuntimeError("eToro quote response is missing bid/ask/lastExecution")

        return EtoroQuote(
            instrument_id=instrument_id,
            bid=bid,
            ask=ask,
            last_execution=last_execution,
            timestamp=_datetime(row.get("date")),
        )

    async def _await_ack(self, websocket: Any, request_id: str, operation: str) -> list[str | bytes]:
        buffered: list[str | bytes] = []
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=self.timeout_seconds)
            if raw in (b"\x00", "\x00"):
                continue

            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            try:
                payload = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                buffered.append(raw)
                continue

            if not isinstance(payload, dict) or payload.get("id") != request_id:
                buffered.append(raw)
                continue
            if payload.get("operation") != operation:
                raise RuntimeError(f"eToro WebSocket returned unexpected operation for {operation}")
            if payload.get("success") is not True:
                message = str(payload.get("errorMessage") or "unknown error")
                raise RuntimeError(f"eToro WebSocket {operation} failed: {message}")
            return buffered

    async def _authenticate(self, websocket: Any) -> None:
        request_id = str(uuid.uuid4())
        await websocket.send(
            json.dumps(
                {
                    "id": request_id,
                    "operation": "Authenticate",
                    "data": {"userKey": self.user_key, "apiKey": self.api_key},
                }
            )
        )
        await self._await_ack(websocket, request_id, "Authenticate")

    async def _subscribe(self, websocket: Any, instrument_id: int) -> list[str | bytes]:
        request_id = str(uuid.uuid4())
        await websocket.send(
            json.dumps(
                {
                    "id": request_id,
                    "operation": "Subscribe",
                    "data": {"topics": [f"instrument:{instrument_id}"], "snapshot": False},
                }
            )
        )
        return await self._await_ack(websocket, request_id, "Subscribe")

    async def stream_instrument(
        self,
        instrument_id: int,
        *,
        reconnect: bool = True,
    ) -> AsyncIterator[EtoroMarketUpdate]:
        """Yield eToro live rate updates for one instrument.

        The live Rate feed may emit a fully priced update followed by sparse
        timestamp-only updates. Within one WebSocket connection, retain the
        latest complete price-bearing message so timestamp-only updates can
        advance event-time candle construction without inventing a new price.
        Partial price-bearing updates replace the cache as a whole, preventing
        stale fields from an older message from overriding fresher bid/ask data.
        """
        while True:
            last_bid: Decimal | None = None
            last_ask: Decimal | None = None
            last_execution: Decimal | None = None

            def hydrate(update: EtoroMarketUpdate) -> EtoroMarketUpdate:
                nonlocal last_bid, last_ask, last_execution
                has_price = any(
                    value is not None
                    for value in (update.bid, update.ask, update.last_execution)
                )
                if has_price:
                    last_bid = update.bid
                    last_ask = update.ask
                    last_execution = update.last_execution
                    return update
                return EtoroMarketUpdate(
                    instrument_id=update.instrument_id,
                    bid=last_bid,
                    ask=last_ask,
                    last_execution=last_execution,
                    timestamp=update.timestamp,
                    is_market_open=update.is_market_open,
                    is_exchange_open=update.is_exchange_open,
                    message_type=update.message_type,
                )

            try:
                async with self._websocket_connect(self.websocket_url) as websocket:
                    await self._authenticate(websocket)
                    buffered = await self._subscribe(websocket, instrument_id)
                    for raw in buffered:
                        for update in parse_etoro_stream_message(raw):
                            if update.instrument_id == instrument_id:
                                yield hydrate(update)

                    async for raw in websocket:
                        for update in parse_etoro_stream_message(raw):
                            if update.instrument_id == instrument_id:
                                yield hydrate(update)
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, TimeoutError) as exc:
                if not reconnect:
                    raise RuntimeError(f"eToro WebSocket connection failed: {exc}") from exc
            if not reconnect:
                return
            await self._sleep(self.reconnect_delay_seconds)
