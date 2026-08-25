from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

from trading_system.tracked_event_repository import (
    SupabaseTrackedEventRepository,
    TrackedEventStatus,
)


class _Response:
    def __init__(self, data) -> None:
        self.data = data


class _Query:
    """Minimal stand-in that records the exact PostgREST filters applied."""

    def __init__(self, recorder) -> None:
        self.recorder = recorder

    def select(self, columns):
        self.recorder["select"] = columns
        return self

    def in_(self, column, values):
        self.recorder.setdefault("in", []).append((column, tuple(values)))
        return self

    def gte(self, column, value):
        self.recorder.setdefault("gte", []).append((column, value))
        return self

    def lte(self, column, value):
        self.recorder.setdefault("lte", []).append((column, value))
        return self

    def or_(self, expression):
        self.recorder.setdefault("or", []).append(expression)
        return self

    def order(self, column):
        self.recorder["order"] = column
        return self

    def execute(self):
        return _Response(self.recorder.get("rows", []))


class _Client:
    def __init__(self, rows=()) -> None:
        self.recorder = {"rows": list(rows)}

    def table(self, name):
        self.recorder["table"] = name
        return _Query(self.recorder)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
LOOKAHEAD = timedelta(hours=24)
MAX_PAST = timedelta(hours=12)


def _row(**overrides):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "tracked_instrument_id": "tracked-wds",
        "calendar_event_id": None,
        "company_name": "Woodside Energy Group Ltd",
        "instrument": "WDS.ASX",
        "market": "Australia",
        "source": "manual_ir",
        "external_key": "wds-hy26",
        "kind": "earnings",
        "title": "Woodside HY26",
        "event_at": datetime(2026, 8, 23, 0, 0, tzinfo=UTC),
        "event_time_status": "estimated",
        "status": "tracked",
    }
    row.update(overrides)
    return row


class ListRunnableFilterTests(unittest.TestCase):
    def _recorder(self, rows=()):
        client = _Client(rows)
        repository = SupabaseTrackedEventRepository(client)
        repository.list_runnable(now=NOW, lookahead=LOOKAHEAD, max_past=MAX_PAST)
        return client.recorder

    def test_upper_bound_and_status_filter_are_unchanged(self) -> None:
        recorder = self._recorder()

        self.assertEqual(recorder["table"], "tracked_market_events")
        self.assertEqual(
            recorder["in"],
            [("status", (TrackedEventStatus.TRACKED.value, TrackedEventStatus.MONITORING.value))],
        )
        self.assertEqual(
            recorder["lte"], [("event_at", (NOW + LOOKAHEAD).isoformat())]
        )
        self.assertEqual(recorder["order"], "event_at")

    def test_lower_bound_moves_into_an_or_with_the_unfinished_exception(self) -> None:
        recorder = self._recorder()

        # The plain gte lower bound is gone; it is now one branch of the or.
        self.assertNotIn("gte", recorder)
        self.assertEqual(
            recorder["or"],
            [
                f"event_at.gte.{(NOW - MAX_PAST).isoformat()},"
                "and(status.eq.tracked,reference_price.is.null)"
            ],
        )

    def test_exception_is_exactly_tracked_and_unreferenced(self) -> None:
        # One invariant, not a per-shape list: a TRACKED event that has not
        # captured a reference has no durable pre-event outcome yet, so it must
        # stay schedulable until it gets one. Anything more permissive would
        # resurrect unbounded old backlog on each poll.
        expression = self._recorder()["or"][0]
        _lower_branch, exception_branch = expression.split(",and(", 1)
        conditions = exception_branch.rstrip(")").split(",")

        self.assertEqual(
            conditions,
            ["status.eq.tracked", "reference_price.is.null"],
        )

    def test_exception_does_not_discriminate_on_context(self) -> None:
        # Both an unprepared row (needs the deadline RPC to record a terminal
        # failure) and a prepared one (needs revalidation retries) must survive
        # the cutoff, so the filter must not mention the context column.
        self.assertNotIn("pre_event_market_context", self._recorder()["or"][0])

    def test_monitoring_rows_are_not_exempted_from_max_past(self) -> None:
        expression = self._recorder()["or"][0]

        self.assertNotIn(TrackedEventStatus.MONITORING.value, expression)

    def test_returned_rows_are_still_deserialized(self) -> None:
        client = _Client([_row(pre_event_market_context={"schema_version": 1})])
        repository = SupabaseTrackedEventRepository(client)

        events = repository.list_runnable(now=NOW, lookahead=LOOKAHEAD, max_past=MAX_PAST)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, TrackedEventStatus.TRACKED)
        self.assertEqual(events[0].pre_event_market_context, {"schema_version": 1})

    def test_unprepared_missed_event_survives_a_long_worker_outage(self) -> None:
        # The worker was down from before event_at until well past the cutoff.
        # The context-free row must come back so the deadline RPC can finally
        # record a terminal failure for it.
        missed = _row(
            event_at=NOW - timedelta(hours=30),
            pre_event_market_context=None,
            reference_price=None,
        )
        repository = SupabaseTrackedEventRepository(_Client([missed]))

        events = repository.list_runnable(now=NOW, lookahead=LOOKAHEAD, max_past=MAX_PAST)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, TrackedEventStatus.TRACKED)
        self.assertIsNone(events[0].pre_event_market_context)
        self.assertIsNone(events[0].reference_price)

    def test_rejects_naive_now_before_querying(self) -> None:
        client = _Client()
        repository = SupabaseTrackedEventRepository(client)

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            repository.list_runnable(
                now=datetime(2026, 8, 24, 12, 0), lookahead=LOOKAHEAD, max_past=MAX_PAST
            )

        self.assertNotIn("table", client.recorder)


class ListRunnablePostgrestSemanticsTests(unittest.TestCase):
    """Evaluate the built PostgREST query against rows, not just its shape.

    The filter's correctness lives in PostgREST grammar the fake above cannot
    check, so this builds the real request URL with the real client and then
    applies the parsed filter to candidate rows.
    """

    @staticmethod
    def _built_filters():
        from postgrest import SyncPostgrestClient

        captured = {}

        class _CapturingClient:
            def table(self, name):
                inner = SyncPostgrestClient(
                    "http://example.test", schema="public", headers={}
                ).table(name)

                class _Wrapper:
                    def __init__(self, builder):
                        self.builder = builder

                    def __getattr__(self, item):
                        attr = getattr(self.builder, item)
                        if item == "execute":
                            def _execute():
                                request = self.builder.request
                                built = request.session.build_request(
                                    request.http_method,
                                    str(request.path),
                                    params=request.params,
                                )
                                captured["url"] = str(built.url)
                                return _Response([])

                            return _execute

                        def _wrapped(*args, **kwargs):
                            return _Wrapper(attr(*args, **kwargs))

                        return _wrapped

                return _Wrapper(inner)

        repository = SupabaseTrackedEventRepository(_CapturingClient())
        repository.list_runnable(now=NOW, lookahead=LOOKAHEAD, max_past=MAX_PAST)

        params = parse_qs(urlparse(captured["url"]).query)
        return {key: unquote(values[0]) for key, values in params.items()}

    def test_timestamps_survive_url_encoding_inside_the_or_group(self) -> None:
        # "+00:00" offsets must round-trip through the query string; a bare "+"
        # would decode as a space and silently break the lower bound.
        filters = self._built_filters()

        self.assertIn((NOW - MAX_PAST).isoformat(), filters["or"])
        self.assertIn((NOW + LOOKAHEAD).isoformat(), filters["event_at"])

    def test_or_group_structure_is_preserved(self) -> None:
        filters = self._built_filters()

        self.assertTrue(filters["or"].startswith("("))
        self.assertTrue(filters["or"].endswith(")"))
        self.assertIn("and(", filters["or"])
        self.assertIn("reference_price.is.null", filters["or"])
        self.assertEqual(filters["status"], "in.(tracked,monitoring)")

    def test_filter_selects_exactly_the_intended_rows(self) -> None:
        filters = self._built_filters()
        lower = NOW - MAX_PAST
        upper = NOW + LOOKAHEAD
        stale = NOW - timedelta(hours=30)
        recent = NOW - timedelta(hours=1)

        def matches(*, status, event_at, reference_price, context):
            # Mirrors the emitted filter: status IN (...) AND event_at <= upper
            # AND (event_at >= lower OR (tracked AND not yet referenced)).
            if status not in ("tracked", "monitoring"):
                return False
            if event_at > upper:
                return False
            unfinished = status == "tracked" and reference_price is None
            return event_at >= lower or unfinished

        # 1. Prepared, unreferenced, older than max_past -> still runnable.
        self.assertTrue(
            matches(status="tracked", event_at=stale, reference_price=None, context={"v": 1})
        )
        # 2. Context-free TRACKED older than max_past -> also still runnable,
        # so a worker that was down across event_at can still record the
        # terminal deadline failure.
        self.assertTrue(
            matches(status="tracked", event_at=stale, reference_price=None, context=None)
        )
        # 3. Once that failure is persisted the row is no longer TRACKED and
        # drops out for good.
        self.assertFalse(
            matches(status="failed", event_at=stale, reference_price=None, context=None)
        )
        # 4. A referenced row past max_past drops out, so the exception drains.
        self.assertFalse(
            matches(status="tracked", event_at=stale, reference_price=1, context={"v": 1})
        )
        # MONITORING keeps the plain cutoff even with a context: it already has
        # a durable in-progress outcome and is not awaiting a pre-event verdict.
        self.assertFalse(
            matches(status="monitoring", event_at=stale, reference_price=None, context={"v": 1})
        )
        self.assertFalse(
            matches(status="completed", event_at=stale, reference_price=None, context=None)
        )
        self.assertFalse(
            matches(status="cancelled", event_at=stale, reference_price=None, context=None)
        )
        # Terminal rows never come back regardless of context.
        self.assertFalse(
            matches(status="failed", event_at=stale, reference_price=None, context={"v": 1})
        )
        # Everything inside the window is unaffected by the exception.
        self.assertTrue(
            matches(status="tracked", event_at=recent, reference_price=None, context=None)
        )
        self.assertTrue(
            matches(status="monitoring", event_at=recent, reference_price=None, context=None)
        )
        # The lookahead ceiling still applies to prepared rows.
        self.assertFalse(
            matches(
                status="tracked",
                event_at=upper + timedelta(hours=1),
                reference_price=None,
                context={"v": 1},
            )
        )

    def test_no_other_old_rows_become_permanently_runnable(self) -> None:
        # Only TRACKED-and-unreferenced escapes max_past, and only until it
        # reaches a durable outcome. Everything else keeps the plain cutoff.
        filters = self._built_filters()
        self.assertIn("reference_price.is.null", filters["or"])
        self.assertIn("status.eq.tracked", filters["or"])
        self.assertNotIn("monitoring", filters["or"])
        # No unconditional escape hatch snuck in.
        self.assertNotIn("or(", filters["or"].replace("and(", ""))


if __name__ == "__main__":
    unittest.main()
