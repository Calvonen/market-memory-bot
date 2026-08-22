from __future__ import annotations

import asyncio
import json
import os
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


def parse_etoro_stream_message(raw: str | bytes) -> tuple[EtoroMarketUpdate, ...]:
    """Parse one eToro WebSocket frame into zero or more market updates.

    eToro sends a binary NUL heartbeat (`b"\\x00"`) between JSON frames. It is
    intentionally ignored here. Malformed/non-market frames are also ignored;
    authentication/subscription acknowledgements are handled by the provider.
    """
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
            continue

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
    """Backend-only eToro quote/WebSocket adapter.

    This provider is intentionally not wired into Strategy, Risk, PaperBroker,
    Market Memory, or the scanner in this PR. It only establishes a small,
    testable transport boundary for later market-reaction work.
    """

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
                    "data": {"topics": [f"instrument:{instrument_id}"], "snapshot": True},
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
        """Yield eToro snapshot/update frames for one instrument.

        Connection loss is retried with a short delay by default. Each retry
        authenticates and subscribes again with `snapshot=True`, so consumers
        re-enter from a fresh provider snapshot instead of assuming continuity.
        """
        while True:
            try:
                async with self._websocket_connect(self.websocket_url) as websocket:
                    await self._authenticate(websocket)
                    buffered = await self._subscribe(websocket, instrument_id)
                    for raw in buffered:
                        for update in parse_etoro_stream_message(raw):
                            if update.instrument_id == instrument_id:
                                yield update

                    async for raw in websocket:
                        for update in parse_etoro_stream_message(raw):
                            if update.instrument_id == instrument_id:
                                yield update
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, TimeoutError) as exc:
                if not reconnect:
                    raise RuntimeError(f"eToro WebSocket connection failed: {exc}") from exc
            if not reconnect:
                return
            await self._sleep(self.reconnect_delay_seconds)
