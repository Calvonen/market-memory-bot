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
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, params: dict):
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
    "validate_tracked_market_event_pre_event_context_if_current_function_exists": True,
    "fail_tracked_market_event_pre_event_deadline_if_current_function_exists": True,
    "runtime_schema_version": 6,
}


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
            with patch("supabase.create_client", return_value=fake_client) as create_client:
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    exit_code = main()
        self.assertEqual(create_client.call_count, 1)
        return exit_code, out.getvalue(), err.getvalue()

    def test_fails_closed_when_both_env_vars_are_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            err = io.StringIO()
            with redirect_stderr(err):
                exit_code = main()
        self.assertEqual(exit_code, 1)
        self.assertIn("MARKETAI_SUPABASE_URL", err.getvalue())
        self.assertIn("MARKETAI_SUPABASE_SECRET_KEY", err.getvalue())

    def test_fails_closed_when_only_the_url_is_set(self) -> None:
        with patch.dict(
            "os.environ",
            {"MARKETAI_SUPABASE_URL": "https://example.supabase.co"},
            clear=True,
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                exit_code = main()
        self.assertEqual(exit_code, 1)

    def test_fails_closed_when_only_the_secret_key_is_set(self) -> None:
        with patch.dict(
            "os.environ", {"MARKETAI_SUPABASE_SECRET_KEY": "secret-key"}, clear=True
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                exit_code = main()
        self.assertEqual(exit_code, 1)

    def test_fails_closed_when_the_rpc_call_raises(self) -> None:
        error = RuntimeError('Could not find the function public.verify_strategy_draft_schema')
        exit_code, _out, err = self._run_with_client(_FakeClient(error))
        self.assertEqual(exit_code, 1)
        self.assertIn("SCHEMA GATE FAILED", err)
        self.assertIn("verify_strategy_draft_schema", err)

    def test_fails_closed_when_the_rpc_returns_no_rows(self) -> None:
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[])))
        self.assertEqual(exit_code, 1)
        self.assertIn("SCHEMA GATE FAILED", err)

    def test_fails_closed_when_the_table_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, event_strategy_approvals_table_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("event_strategy_approvals table", err)
        self.assertNotIn("approve_strategy_draft() function", err)

    def test_fails_closed_when_the_approve_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, approve_strategy_draft_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("approve_strategy_draft() function", err)

    def test_fails_closed_when_the_insert_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, insert_next_expectation_version_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("insert_next_expectation_version() function", err)

    def test_fails_closed_when_the_schema_version_does_not_match(self) -> None:
        row = dict(ALL_PRESENT_ROW, schema_version_matches=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("implementation version", err)

    def test_fails_closed_when_the_calendar_events_table_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, calendar_events_table_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("calendar_events table", err)

    def test_fails_closed_when_the_upsert_calendar_candidate_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, upsert_calendar_candidate_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("upsert_calendar_candidate() function", err)

    def test_fails_closed_when_the_transition_calendar_event_status_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, transition_calendar_event_status_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("transition_calendar_event_status() function", err)

    def test_fails_closed_when_calendar_upsert_version_does_not_match(self) -> None:
        row = dict(ALL_PRESENT_ROW, calendar_candidate_upsert_version_matches=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("placeholder-preserving implementation version", err)

    def test_fails_closed_when_explicit_upsert_version_is_absent(self) -> None:
        row = dict(ALL_PRESENT_ROW)
        del row["calendar_candidate_upsert_implementation_version"]
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("deployed: None", err)

    def test_fails_closed_when_tracked_event_runtime_object_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, capture_tracked_market_event_reaction_anchor_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("capture_tracked_market_event_reaction_anchor() function", err)

    def test_fails_closed_when_resolution_preflight_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, arm_tracked_market_event_resolution_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("arm_tracked_market_event_resolution() function", err)

    def test_fails_closed_on_old_tracked_event_runtime_schema_version(self) -> None:
        row = dict(ALL_PRESENT_ROW, runtime_schema_version=5)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("tracked-event runtime schema version 6", err)

    def test_fails_closed_when_pre_event_deadline_fail_rpc_is_missing(self) -> None:
        row = dict(
            ALL_PRESENT_ROW,
            fail_tracked_market_event_pre_event_deadline_if_current_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("fail_tracked_market_event_pre_event_deadline_if_current() function", err)

    def test_fails_closed_when_config_snapshot_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, capture_tracked_market_event_config_snapshot_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("capture_tracked_market_event_config_snapshot() function", err)

    def test_fails_closed_when_pre_event_context_capture_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, capture_tracked_market_event_pre_event_context_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("capture_tracked_market_event_pre_event_context() function", err)

    def test_fails_closed_when_pre_event_context_cas_capture_rpc_is_missing(self) -> None:
        row = dict(
            ALL_PRESENT_ROW,
            capture_tracked_market_event_pre_event_context_if_current_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("capture_tracked_market_event_pre_event_context_if_current() function", err)

    def test_fails_closed_when_pre_event_context_revalidation_rpc_is_missing(self) -> None:
        row = dict(
            ALL_PRESENT_ROW,
            validate_tracked_market_event_pre_event_context_if_current_function_exists=False,
        )
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("validate_tracked_market_event_pre_event_context_if_current() function", err)

    def test_passes_when_every_required_object_is_present(self) -> None:
        exit_code, out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[ALL_PRESENT_ROW])))
        self.assertEqual(exit_code, 0)
        self.assertEqual(err, "")
        self.assertIn("passed", out.lower())

    def test_calls_the_expected_rpcs_with_no_parameters(self) -> None:
        fake_client = _FakeClient(SimpleNamespace(data=[ALL_PRESENT_ROW]))
        self._run_with_client(fake_client)
        self.assertEqual(
            fake_client.calls,
            [
                ("verify_strategy_draft_schema", {}),
                ("verify_tracked_event_runtime_schema", {}),
            ],
        )


if __name__ == "__main__":
    unittest.main()