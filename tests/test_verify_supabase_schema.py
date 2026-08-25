import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from scripts.verify_supabase_schema import main


class _RpcCall:
    def __init__(self, response) -> None:
        self._response = response

    def execute(self):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeClient:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RpcCall(self.response)


ALL_PRESENT_ROW = {
    "event_strategy_approvals_table_exists": True,
    "approve_strategy_draft_function_exists": True,
    "insert_next_expectation_version_function_exists": True,
    "schema_version_matches": True,
    "calendar_events_table_exists": True,
    "upsert_calendar_candidate_function_exists": True,
    "transition_calendar_event_status_function_exists": True,
    "calendar_candidate_upsert_version_matches": True,
    "calendar_candidate_upsert_implementation_version": 2,
    "tracked_market_events_table_exists": True,
    "tracked_market_event_reactions_table_exists": True,
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
    "runtime_schema_version": 6,
}


class VerifySupabaseSchemaGateTests(unittest.TestCase):
    def _run(self, row):
        client = _FakeClient(SimpleNamespace(data=[row]))
        with patch.dict(
            "os.environ",
            {
                "MARKETAI_SUPABASE_URL": "https://example.supabase.co",
                "MARKETAI_SUPABASE_SECRET_KEY": "secret-key",
            },
            clear=True,
        ), patch("supabase.create_client", return_value=client):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main()
        return code, out.getvalue(), err.getvalue(), client.calls

    def test_passes_with_runtime_schema_six_and_validated_rpc(self) -> None:
        code, out, err, calls = self._run(dict(ALL_PRESENT_ROW))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("passed", out.lower())
        self.assertEqual(
            calls,
            [("verify_strategy_draft_schema", {}), ("verify_tracked_event_runtime_schema", {})],
        )

    def test_fails_closed_on_runtime_schema_five(self) -> None:
        row = dict(ALL_PRESENT_ROW, runtime_schema_version=5)
        code, _out, err, _calls = self._run(row)
        self.assertEqual(code, 1)
        self.assertIn("tracked-event runtime schema version 6", err)

    def test_fails_closed_without_validated_capture_rpc(self) -> None:
        row = dict(
            ALL_PRESENT_ROW,
            capture_tracked_market_event_pre_event_context_validated_function_exists=False,
        )
        code, _out, err, _calls = self._run(row)
        self.assertEqual(code, 1)
        self.assertIn("capture_tracked_market_event_pre_event_context_validated() function", err)

    def test_fails_closed_without_required_env(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main()
        self.assertEqual(code, 1)
        self.assertIn("MARKETAI_SUPABASE_URL", err.getvalue())

    def test_fails_closed_when_existing_dependency_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, arm_tracked_market_event_resolution_function_exists=False)
        code, _out, err, _calls = self._run(row)
        self.assertEqual(code, 1)
        self.assertIn("arm_tracked_market_event_resolution() function", err)


if __name__ == "__main__":
    unittest.main()
