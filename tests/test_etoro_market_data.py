import asyncio
import json
import os
import unittest
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketDataProvider, parse_etoro_stream_message


class _FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeWebSocket:
    def __init__(self, responses: list[str | bytes]) -> None:
        self.responses = list(responses)
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self):
        if not self.responses:
            raise RuntimeError("No fake WebSocket response left")
        return self.responses.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.responses:
            raise StopAsyncIteration
        return self.responses.pop(0)


class _FakeConnection:
    def __init__(self, websocket: _FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FailingConnection:
    async def __aenter__(self):
        raise OSError("connection dropped")

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _ack(operation: str, request_id: str) -> str:
    return json.dumps({"id": request_id, "success": True, "operation": operation})


def _market_message(*, message_type: str = "Snapshot") -> str:
    content = {
        "InstrumentID": "2619",
        "Bid": "71.35",
        "Ask": "71.55",
        "LastExecution": "71.35",
        "Date": "2026-08-21T15:25:17.0204678Z",
        "IsMarketOpen": "false",
        "IsExchangeOpen": "false",
    }
    return json.dumps(
        {
            "messages": [
                {
                    "topic": "instrument:2619",
                    "content": json.dumps(content),
                    "id": "message-id",
                    "type": message_type,
                }
            ]
        }
    )


class EtoroMarketDataProviderTests(unittest.TestCase):
    def test_missing_credentials_fail_closed(self) -> None:
        previous_api = os.environ.pop("ETORO_API_KEY", None)
        previous_user = os.environ.pop("ETORO_USER_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                EtoroMarketDataProvider()
        finally:
            if previous_api is not None:
                os.environ["ETORO_API_KEY"] = previous_api
            if previous_user is not None:
                os.environ["ETORO_USER_KEY"] = previous_user

    def test_fetch_quote_uses_backend_credentials_and_maps_hays_quote(self) -> None:
        captured: dict = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured.update(url=url, params=params, headers=headers, timeout=timeout)
            return _FakeResponse(
                {
                    "rates": [
                        {
                            "instrumentID": 2619,
                            "ask": 71.55,
                            "bid": 71.35,
                            "lastExecution": 71.35,
                            "date": "2026-08-21T15:28:41.3124054Z",
                        }
                    ]
                }
            )

        provider = EtoroMarketDataProvider(
            api_key="api-secret",
            user_key="user-secret",
            http_get=fake_get,
        )
        quote = provider.fetch_quote(2619)

        self.assertTrue(captured["url"].endswith("/instruments/rates"))
        self.assertEqual(captured["params"], {"instrumentIds": "2619"})
        self.assertEqual(captured["headers"]["x-api-key"], "api-secret")
        self.assertEqual(captured["headers"]["x-user-key"], "user-secret")
        self.assertTrue(captured["headers"]["x-request-id"])
        self.assertEqual(quote.instrument_id, 2619)
        self.assertEqual(quote.bid, Decimal("71.35"))
        self.assertEqual(quote.ask, Decimal("71.55"))
        self.assertEqual(quote.last_execution, Decimal("71.35"))
        self.assertEqual(quote.timestamp.isoformat(), "2026-08-21T15:28:41.312405+00:00")

    def test_fetch_quote_rejects_non_ok_response(self) -> None:
        provider = EtoroMarketDataProvider(
            api_key="api-secret",
            user_key="user-secret",
            http_get=lambda *a, **k: _FakeResponse({"error": "nope"}, status_code=403),
        )
        with self.assertRaises(RuntimeError):
            provider.fetch_quote(2619)

    def test_parse_stream_message_ignores_heartbeat_and_maps_snapshot(self) -> None:
        self.assertEqual(parse_etoro_stream_message(b"\x00"), ())

        updates = parse_etoro_stream_message(_market_message())

        self.assertEqual(len(updates), 1)
        update = updates[0]
        self.assertEqual(update.instrument_id, 2619)
        self.assertEqual(update.bid, Decimal("71.35"))
        self.assertEqual(update.ask, Decimal("71.55"))
        self.assertEqual(update.last_execution, Decimal("71.35"))
        self.assertFalse(update.is_market_open)
        self.assertFalse(update.is_exchange_open)
        self.assertEqual(update.message_type, "Snapshot")


class EtoroMarketDataProviderAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_authenticates_subscribes_and_yields_snapshot(self) -> None:
        websocket = _FakeWebSocket([])

        def connect(_url):
            return _FakeConnection(websocket)

        provider = EtoroMarketDataProvider(
            api_key="api-secret",
            user_key="user-secret",
            websocket_connect=connect,
        )

        original_send = websocket.send

        async def send_and_queue_ack(raw: str) -> None:
            await original_send(raw)
            request = websocket.sent[-1]
            websocket.responses.append(_ack(request["operation"], request["id"]))
            if request["operation"] == "Subscribe":
                websocket.responses.append(_market_message())

        websocket.send = send_and_queue_ack

        updates = [update async for update in provider.stream_instrument(2619, reconnect=False)]

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].instrument_id, 2619)
        self.assertEqual([message["operation"] for message in websocket.sent], ["Authenticate", "Subscribe"])
        self.assertEqual(websocket.sent[0]["data"]["apiKey"], "api-secret")
        self.assertEqual(websocket.sent[0]["data"]["userKey"], "user-secret")
        self.assertEqual(websocket.sent[1]["data"]["topics"], ["instrument:2619"])
        self.assertTrue(websocket.sent[1]["data"]["snapshot"])
        self.assertTrue(websocket.sent[0]["id"])
        self.assertTrue(websocket.sent[1]["id"])

    async def test_stream_reconnects_and_resubscribes_after_connection_failure(self) -> None:
        websocket = _FakeWebSocket([])
        attempts = 0
        sleeps: list[float] = []

        def connect(_url):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return _FailingConnection()
            return _FakeConnection(websocket)

        async def no_sleep(delay: float) -> None:
            sleeps.append(delay)

        provider = EtoroMarketDataProvider(
            api_key="api-secret",
            user_key="user-secret",
            websocket_connect=connect,
            sleep=no_sleep,
            reconnect_delay_seconds=0.25,
        )

        original_send = websocket.send

        async def send_and_queue_ack(raw: str) -> None:
            await original_send(raw)
            request = websocket.sent[-1]
            websocket.responses.append(_ack(request["operation"], request["id"]))
            if request["operation"] == "Subscribe":
                websocket.responses.append(_market_message(message_type="Update"))

        websocket.send = send_and_queue_ack

        stream = provider.stream_instrument(2619)
        update = await anext(stream)
        await stream.aclose()

        self.assertEqual(update.instrument_id, 2619)
        self.assertEqual(update.message_type, "Update")
        self.assertEqual(attempts, 2)
        self.assertEqual(sleeps, [0.25])

    async def test_stream_buffers_heartbeat_and_out_of_order_snapshot_before_subscribe_ack(self) -> None:
        websocket = _FakeWebSocket([])

        def connect(_url):
            return _FakeConnection(websocket)

        provider = EtoroMarketDataProvider(
            api_key="api-secret",
            user_key="user-secret",
            websocket_connect=connect,
        )

        original_send = websocket.send

        async def send_and_queue_ack(raw: str) -> None:
            await original_send(raw)
            request = websocket.sent[-1]
            if request["operation"] == "Authenticate":
                websocket.responses.append(_ack(request["operation"], request["id"]))
            else:
                # Server sends a heartbeat and the snapshot before the Subscribe ack.
                websocket.responses.append(b"\x00")
                websocket.responses.append(_market_message())
                websocket.responses.append(_ack(request["operation"], request["id"]))

        websocket.send = send_and_queue_ack

        updates = [update async for update in provider.stream_instrument(2619, reconnect=False)]

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].instrument_id, 2619)
        self.assertEqual([message["operation"] for message in websocket.sent], ["Authenticate", "Subscribe"])


if __name__ == "__main__":
    unittest.main()
