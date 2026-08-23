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
    "capture_tracked_market_event_reference_function_exists": True,
    "capture_tracked_market_event_reaction_anchor_function_exists": True,
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

    def test_fails_closed_when_calendar_upsert_version_key_is_absent(self) -> None:
        row = dict(ALL_PRESENT_ROW)
        del row["calendar_candidate_upsert_version_matches"]
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("placeholder-preserving implementation version", err)

    def test_fails_closed_on_the_older_atomic_upsert_marker_version(self) -> None:
        row = dict(ALL_PRESENT_ROW, calendar_candidate_upsert_implementation_version=1)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("atomic implementation version 2", err)

    def test_fails_closed_when_explicit_upsert_version_is_absent(self) -> None:
        row = dict(ALL_PRESENT_ROW)
        del row["calendar_candidate_upsert_implementation_version"]
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("deployed: None", err)

    def test_fails_closed_when_the_calendar_schema_keys_are_absent_entirely(self) -> None:
        row = dict(ALL_PRESENT_ROW)
        del row["calendar_events_table_exists"]
        del row["upsert_calendar_candidate_function_exists"]
        del row["transition_calendar_event_status_function_exists"]
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("calendar_events table", err)
        self.assertIn("upsert_calendar_candidate() function", err)
        self.assertIn("transition_calendar_event_status() function", err)

    def test_fails_closed_when_the_schema_version_key_is_absent(self) -> None:
        row = {
            "event_strategy_approvals_table_exists": True,
            "approve_strategy_draft_function_exists": True,
            "insert_next_expectation_version_function_exists": True,
        }
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("implementation version", err)

    def test_reports_every_missing_object_at_once(self) -> None:
        row = {
            "event_strategy_approvals_table_exists": False,
            "approve_strategy_draft_function_exists": False,
            "insert_next_expectation_version_function_exists": False,
            "schema_version_matches": False,
            "calendar_events_table_exists": False,
            "upsert_calendar_candidate_function_exists": False,
            "transition_calendar_event_status_function_exists": False,
            "calendar_candidate_upsert_version_matches": False,
        }
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("event_strategy_approvals table", err)
        self.assertIn("approve_strategy_draft() function", err)
        self.assertIn("insert_next_expectation_version() function", err)
        self.assertIn("implementation version", err)
        self.assertIn("calendar_events table", err)
        self.assertIn("upsert_calendar_candidate() function", err)
        self.assertIn("transition_calendar_event_status() function", err)
        self.assertIn("placeholder-preserving implementation version", err)

    def test_fails_closed_when_tracked_event_runtime_object_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, capture_tracked_market_event_reaction_anchor_function_exists=False)
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[row])))
        self.assertEqual(exit_code, 1)
        self.assertIn("capture_tracked_market_event_reaction_anchor() function", err)

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
