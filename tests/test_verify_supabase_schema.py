import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from scripts.verify_supabase_schema import (
    REQUIRED_TRACKED_EVENT_CHECKS,
    _postgres_response_key,
    main,
)


class _RpcCall:
    def __init__(self, response) -> None:
        self._response = response

    def execute(self):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, params: dict):
        self.calls.append((name, params))
        if name not in self.responses:
            raise AssertionError(f"unexpected RPC call: {name}")
        return _RpcCall(self.responses[name])


STRATEGY_PRESENT_ROW = {
    "event_strategy_approvals_table_exists": True,
    "approve_strategy_draft_function_exists": True,
    "insert_next_expectation_version_function_exists": True,
    "schema_version_matches": True,
    "calendar_events_table_exists": True,
    "upsert_calendar_candidate_function_exists": True,
    "transition_calendar_event_status_function_exists": True,
    "calendar_candidate_upsert_version_matches": True,
    "calendar_candidate_upsert_implementation_version": 3,
}

OFFICIAL_SOURCE_PRESENT_ROW = {
    "event_official_release_sources_table_exists": True,
    "set_event_official_release_source_function_exists": True,
    "clear_event_official_release_source_function_exists": True,
    "official_release_source_schema_version": 8,
}

TRACKED_PRESENT_ROW = {
    "tracked_market_events_table_exists": True,
    "tracked_event_release_ingestion_audit_table_exists": True,
    "record_tracked_event_release_ingestion_attempt_function_exists": True,
    "tracked_market_event_reactions_table_exists": True,
    "tracked_market_event_event_date_column_exists": True,
    "upsert_tracked_market_event_function_exists": True,
    "arm_tracked_market_event_resolution_function_exists": True,
    "capture_tracked_market_event_reference_function_exists": True,
    "capture_tracked_market_event_reaction_anchor_function_exists": True,
    "capture_tracked_market_event_config_snapshot_function_exists": True,
    "capture_tracked_market_event_pre_event_context_function_exists": True,
    "capture_tracked_market_event_pre_event_context_if_current_function_exists": True,
    "capture_tracked_market_event_pre_event_context_validated_function_exists": True,
    "validate_tracked_market_event_pre_event_context_if_current_function_exists": True,
    "fail_tracked_market_event_pre_event_deadline_if_current_function_exists": True,
    "fail_tracked_market_event_stale_context_if_current_function_exists": True,
    "promote_calendar_event_to_tracked_runtime_function_exists": True,
    "calendar_runtime_untrack_guard_version_matches": True,
    "ensure_calendar_release_shell_function_exists": True,
    "calendar_release_shell_version_matches": True,
    "ensure_tracked_event_release_shell_function_exists": True,
    "tracked_event_workflow_blockers_table_exists": True,
    "ensure_tracked_event_release_shell_with_blocker_function_exists": True,
    "calendarless_release_shell_trigger_exists": True,
    "runtime_schema_version": 14,
}


def _responses(
    *,
    strategy_row=STRATEGY_PRESENT_ROW,
    official_source_row=OFFICIAL_SOURCE_PRESENT_ROW,
    tracked_row=TRACKED_PRESENT_ROW,
) -> dict[str, object]:
    return {
        "verify_strategy_draft_schema": SimpleNamespace(data=[strategy_row]),
        "verify_official_release_source_schema": SimpleNamespace(data=[official_source_row]),
        "verify_tracked_event_runtime_schema": SimpleNamespace(data=[tracked_row]),
    }


def _as_postgres_rpc_row(row):
    """Mirror PostgreSQL's 63-byte output-column identifier truncation."""
    return {_postgres_response_key(key): value for key, value in row.items()}


class VerifySupabaseSchemaGateTests(unittest.TestCase):
    def _run_with_client(self, fake_client) -> tuple[int, str, str]:
        with patch.dict(
            "os.environ",
            {
                "MARKETAI_SUPABASE_URL": "https://example.supabase.co",
                "MARKETAI_SUPABASE_SECRET_KEY": "secret-key",
            },
            clear=True,
        ):
            with patch("supabase.create_client", return_value=fake_client):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    exit_code = main()
        return exit_code, out.getvalue(), err.getvalue()

    def test_fails_closed_when_env_is_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            err = io.StringIO()
            with redirect_stderr(err):
                exit_code = main()
        self.assertEqual(exit_code, 1)
        self.assertIn("MARKETAI_SUPABASE_URL", err.getvalue())
        self.assertIn("MARKETAI_SUPABASE_SECRET_KEY", err.getvalue())

    def test_fails_closed_when_schema_rpc_raises(self) -> None:
        responses = _responses()
        responses["verify_strategy_draft_schema"] = RuntimeError("schema rpc unavailable")
        exit_code, _out, err = self._run_with_client(_FakeClient(responses))
        self.assertEqual(exit_code, 1)
        self.assertIn("SCHEMA GATE FAILED", err)

    def test_fails_closed_on_old_official_source_schema_version(self) -> None:
        legacy_row = {
            "event_official_release_sources_table_exists": True,
            "set_event_official_release_source_function_exists": True,
            "clear_event_official_release_source_function_exists": True,
            "official_release_source_schema_version": 7,
        }
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(official_source_row=legacy_row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("official-release-source schema version 8", err)
        self.assertIn("deployed: 7", err)

    def test_fails_closed_on_old_runtime_schema_version(self) -> None:
        row = dict(TRACKED_PRESENT_ROW, runtime_schema_version=13)
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("tracked-event runtime schema version 14", err)

    def test_fails_closed_when_ingestion_audit_table_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            tracked_event_release_ingestion_audit_table_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("tracked_event_release_ingestion_audit table", err)

    def test_fails_closed_when_ingestion_audit_rpc_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            record_tracked_event_release_ingestion_attempt_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("record_tracked_event_release_ingestion_attempt", err)

    def test_fails_closed_when_event_date_column_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            tracked_market_event_event_date_column_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("tracked_market_events.event_date date column", err)

    def test_fails_closed_when_stale_context_fail_rpc_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            fail_tracked_market_event_stale_context_if_current_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("fail_tracked_market_event_stale_context_if_current() function", err)

    def test_fails_closed_when_calendar_promotion_rpc_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            promote_calendar_event_to_tracked_runtime_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("promote_calendar_event_to_tracked_runtime() function", err)

    def test_fails_closed_when_runtime_untrack_guard_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            calendar_runtime_untrack_guard_version_matches=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("calendar runtime-bound untrack guard", err)

    def test_fails_closed_when_release_shell_function_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            ensure_calendar_release_shell_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("ensure_calendar_release_shell() function", err)

    def test_fails_closed_when_release_shell_version_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            calendar_release_shell_version_matches=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("calendar release-pipeline shell", err)

    def test_fails_closed_when_canonical_release_shell_function_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            ensure_tracked_event_release_shell_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("ensure_tracked_event_release_shell() function", err)

    def test_fails_closed_when_workflow_blocker_table_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            tracked_event_workflow_blockers_table_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("tracked_event_workflow_blockers table", err)

    def test_fails_closed_when_blocker_wrapper_rpc_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            ensure_tracked_event_release_shell_with_blocker_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("ensure_tracked_event_release_shell_with_blocker() function", err)

    def test_fails_closed_when_calendarless_release_shell_trigger_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            calendarless_release_shell_trigger_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("calendar-less tracked-event release-shell trigger", err)

    def test_fails_closed_when_existing_required_object_is_missing(self) -> None:
        row = dict(
            TRACKED_PRESENT_ROW,
            capture_tracked_market_event_reference_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("capture_tracked_market_event_reference() function", err)

    def test_passes_when_every_required_object_is_present(self) -> None:
        fake_client = _FakeClient(_responses())
        exit_code, out, err = self._run_with_client(fake_client)
        self.assertEqual(exit_code, 0)
        self.assertEqual(err, "")
        self.assertIn("passed", out.lower())
        self.assertEqual(
            fake_client.calls,
            [
                ("verify_strategy_draft_schema", {}),
                ("verify_official_release_source_schema", {}),
                ("verify_tracked_event_runtime_schema", {}),
            ],
        )

    def test_passes_with_real_postgres_truncated_rpc_column_names(self) -> None:
        truncated_row = _as_postgres_rpc_row(TRACKED_PRESENT_ROW)
        long_keys = [key for key, _label in REQUIRED_TRACKED_EVENT_CHECKS if len(key.encode()) > 63]

        self.assertTrue(long_keys)
        self.assertTrue(all(key not in truncated_row for key in long_keys))

        exit_code, out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=truncated_row))
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(err, "")
        self.assertIn("passed", out.lower())

    def test_truncated_false_value_still_fails_closed(self) -> None:
        row = _as_postgres_rpc_row(TRACKED_PRESENT_ROW)
        key = "fail_tracked_market_event_stale_context_if_current_function_exists"
        row[_postgres_response_key(key)] = False

        exit_code, _out, err = self._run_with_client(
            _FakeClient(_responses(tracked_row=row))
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("fail_tracked_market_event_stale_context_if_current() function", err)


if __name__ == "__main__":
    unittest.main()
