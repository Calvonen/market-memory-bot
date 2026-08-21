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

    # -- fails closed on missing config --------------------------------------

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

    # -- fails closed when the RPC itself is unreachable/missing ------------

    def test_fails_closed_when_the_rpc_call_raises(self) -> None:
        # This is the state before migrations are applied: PostgREST
        # returns "could not find function" for an RPC that doesn't exist
        # yet, surfacing here as an exception, not a False result.
        error = RuntimeError('Could not find the function public.verify_strategy_draft_schema')
        exit_code, _out, err = self._run_with_client(_FakeClient(error))

        self.assertEqual(exit_code, 1)
        self.assertIn("SCHEMA GATE FAILED", err)
        self.assertIn("verify_strategy_draft_schema", err)

    def test_fails_closed_when_the_rpc_returns_no_rows(self) -> None:
        exit_code, _out, err = self._run_with_client(_FakeClient(SimpleNamespace(data=[])))

        self.assertEqual(exit_code, 1)
        self.assertIn("SCHEMA GATE FAILED", err)

    # -- fails closed on any missing object, names each one -----------------

    def test_fails_closed_when_the_table_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, event_strategy_approvals_table_exists=False)
        exit_code, _out, err = self._run_with_client(
            _FakeClient(SimpleNamespace(data=[row]))
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("event_strategy_approvals table", err)
        self.assertNotIn("approve_strategy_draft() function", err)

    def test_fails_closed_when_the_approve_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, approve_strategy_draft_function_exists=False)
        exit_code, _out, err = self._run_with_client(
            _FakeClient(SimpleNamespace(data=[row]))
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("approve_strategy_draft() function", err)

    def test_fails_closed_when_the_insert_rpc_is_missing(self) -> None:
        row = dict(ALL_PRESENT_ROW, insert_next_expectation_version_function_exists=False)
        exit_code, _out, err = self._run_with_client(
            _FakeClient(SimpleNamespace(data=[row]))
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("insert_next_expectation_version() function", err)

    def test_fails_closed_when_the_schema_version_does_not_match(self) -> None:
        # This is the case existence checks alone cannot catch: a Supabase
        # project where insert_next_expectation_version() exists under the
        # required signature, but its body is still an older, outdated
        # implementation - schema_version_matches is what verify_
        # strategy_draft_schema() reports false in that situation, since
        # to_regprocedure() has no way to tell an old function body apart
        # from a new one under the same signature.
        row = dict(ALL_PRESENT_ROW, schema_version_matches=False)
        exit_code, _out, err = self._run_with_client(
            _FakeClient(SimpleNamespace(data=[row]))
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("implementation version", err)

    def test_fails_closed_when_the_schema_version_key_is_absent(self) -> None:
        # An older verify_strategy_draft_schema() (from before the version
        # marker existed) simply has no schema_version_matches key in its
        # result at all - this must fail exactly like an explicit False,
        # not be treated as "nothing to check."
        row = {
            "event_strategy_approvals_table_exists": True,
            "approve_strategy_draft_function_exists": True,
            "insert_next_expectation_version_function_exists": True,
        }
        exit_code, _out, err = self._run_with_client(
            _FakeClient(SimpleNamespace(data=[row]))
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("implementation version", err)

    def test_reports_every_missing_object_at_once(self) -> None:
        row = {
            "event_strategy_approvals_table_exists": False,
            "approve_strategy_draft_function_exists": False,
            "insert_next_expectation_version_function_exists": False,
            "schema_version_matches": False,
        }
        exit_code, _out, err = self._run_with_client(
            _FakeClient(SimpleNamespace(data=[row]))
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("event_strategy_approvals table", err)
        self.assertIn("approve_strategy_draft() function", err)
        self.assertIn("insert_next_expectation_version() function", err)
        self.assertIn("implementation version", err)

    # -- passes only when every required object is present -------------------

    def test_passes_when_every_required_object_is_present(self) -> None:
        exit_code, out, err = self._run_with_client(
            _FakeClient(SimpleNamespace(data=[ALL_PRESENT_ROW]))
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(err, "")
        self.assertIn("passed", out.lower())

    def test_calls_the_expected_rpc_with_no_parameters(self) -> None:
        fake_client = _FakeClient(SimpleNamespace(data=[ALL_PRESENT_ROW]))
        self._run_with_client(fake_client)

        self.assertEqual(len(fake_client.calls), 1)
        name, params = fake_client.calls[0]
        self.assertEqual(name, "verify_strategy_draft_schema")
        self.assertEqual(params, {})


if __name__ == "__main__":
    unittest.main()
