import re
import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/deploy-seesam-hub.yml")
EVENT_STRATEGY_APPROVALS_MIGRATION = Path(
    "supabase/migrations/20260821090000_event_strategy_approvals.sql"
)
SHARED_LOCK_MIGRATION = Path(
    "supabase/migrations/20260821140000_shared_expectation_version_lock.sql"
)
VERIFY_MIGRATION = Path(
    "supabase/migrations/20260822090000_verify_strategy_draft_schema.sql"
)
VERIFY_SCRIPT = Path("scripts/verify_supabase_schema.py")


class DeployWorkflowGateTests(unittest.TestCase):
    """The backend restart must never happen before the Supabase schema
    gate succeeds - see docs/event_configuration_storage.md, "Deploy gate:
    Supabase schema verification". These are structural (source-text)
    assertions on the workflow YAML, since the deploy job only runs on a
    self-hosted runner this test suite cannot execute."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_source = WORKFLOW.read_text(encoding="utf-8")

    def _deploy_step_body(self) -> str:
        start = self.workflow_source.index("Deploy backend to seesam-hub (locked)")
        # The next top-level step (two-space-indented "- name:") ends this
        # step's body; publish-ota's job header is further down still.
        next_step = self.workflow_source.index("\n  publish-ota:", start)
        return self.workflow_source[start:next_step]

    def test_gate_script_runs_before_either_service_restart(self) -> None:
        body = self._deploy_step_body()

        self.assertIn("scripts/verify_supabase_schema.py", body)
        gate_index = body.index("scripts/verify_supabase_schema.py")

        for restart_command in (
            "systemctl restart marketai-api.service",
            "systemctl restart marketai-hays-release.service",
        ):
            self.assertIn(restart_command, body)
            restart_index = body.index(restart_command)
            self.assertLess(
                gate_index,
                restart_index,
                f"{restart_command} appears before the schema gate script",
            )

    def test_gate_runs_after_checkout_is_merged_to_the_deploy_sha(self) -> None:
        # The gate script must run against the just-merged commit's own
        # scripts/ directory, not a stale prior checkout.
        body = self._deploy_step_body()

        merge_index = body.index('git merge --ff-only "$DEPLOY_SHA"')
        gate_index = body.index("scripts/verify_supabase_schema.py")
        self.assertLess(merge_index, gate_index)

    def test_gate_failure_stops_the_step_before_any_restart(self) -> None:
        # GitHub Actions `run:` steps default to `bash -eo pipefail`, so a
        # non-zero exit from the gate script aborts the rest of this step's
        # script immediately - this test pins that the workflow doesn't
        # locally override that behavior (e.g. `set +e`) anywhere in this
        # step, which would silently defeat the gate.
        body = self._deploy_step_body()
        self.assertNotIn("set +e", body)
        self.assertNotIn("|| true", body.split("verify_supabase_schema.py")[0][-40:])

    def test_workflow_never_hardcodes_a_supabase_secret(self) -> None:
        # The gate reuses the already-deployed backend's own
        # MARKETAI_SUPABASE_URL/MARKETAI_SUPABASE_SECRET_KEY, sourced from
        # the host's own env file - never a literal value in this workflow,
        # and never a `secrets.*` reference either (this is a self-hosted
        # deploy job, not something that should receive that credential via
        # GitHub Secrets).
        self.assertNotIn("MARKETAI_SUPABASE_SECRET_KEY:", self.workflow_source)
        self.assertNotIn("secrets.MARKETAI_SUPABASE_SECRET_KEY", self.workflow_source)
        self.assertNotIn("secrets.SUPABASE", self.workflow_source)
        self.assertNotIn("service_role", self.workflow_source)

    def test_gate_sources_env_from_the_backend_env_file_not_a_literal(self) -> None:
        body = self._deploy_step_body()
        self.assertIn("source /home/marko/marketai-repo/.env", body)

    def test_compile_step_covers_the_new_scripts_directory(self) -> None:
        compile_start = self.workflow_source.index("Compile Python")
        compile_end = self.workflow_source.index("\n\n", compile_start)
        self.assertIn("scripts/*.py", self.workflow_source[compile_start:compile_end])

    def test_publish_ota_job_still_never_receives_supabase_credentials(self) -> None:
        # publish-ota only ever needs EXPO_TOKEN - confirms this change
        # didn't leak the schema-gate's env sourcing into the OTA job.
        ota_start = self.workflow_source.index("publish-ota:")
        ota_body = self.workflow_source[ota_start:]
        self.assertNotIn("MARKETAI_SUPABASE", ota_body)
        self.assertNotIn("verify_supabase_schema.py", ota_body)


class SchemaGateFileConsistencyTests(unittest.TestCase):
    """The verify_strategy_draft_schema() RPC (in the newest migration)
    must check the exact same function signatures that
    approve_strategy_draft()/insert_next_expectation_version() actually
    declare in their own migrations - otherwise the gate could silently
    drift out of sync and rubber-stamp a schema that doesn't really match
    what the backend calls."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.approvals_source = EVENT_STRATEGY_APPROVALS_MIGRATION.read_text(encoding="utf-8")
        cls.shared_lock_source = SHARED_LOCK_MIGRATION.read_text(encoding="utf-8")
        cls.verify_source = VERIFY_MIGRATION.read_text(encoding="utf-8")
        cls.verify_script_source = VERIFY_SCRIPT.read_text(encoding="utf-8")

    @staticmethod
    def _declared_param_types(source: str, function_name: str) -> list[str]:
        match = re.search(
            rf"create or replace function public\.{re.escape(function_name)}\(\s*(.*?)\)\s*\n",
            source,
            re.DOTALL,
        )
        assert match is not None, f"{function_name} declaration not found"
        params_block = match.group(1)
        param_types = []
        for line in params_block.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            # "input_event_id text" -> "text"; "input_source_as_of date" -> "date"
            param_types.append(line.split()[-1])
        return param_types

    def test_verify_function_checks_the_real_approve_strategy_draft_signature(self) -> None:
        declared_types = self._declared_param_types(
            self.approvals_source, "approve_strategy_draft"
        )
        expected_signature = (
            "public.approve_strategy_draft(" + ", ".join(declared_types) + ")"
        )

        self.assertIn(expected_signature, self.verify_source)

    def test_verify_function_checks_the_real_insert_next_expectation_version_signature(
        self,
    ) -> None:
        declared_types = self._declared_param_types(
            self.shared_lock_source, "insert_next_expectation_version"
        )
        expected_signature = (
            "public.insert_next_expectation_version(" + ", ".join(declared_types) + ")"
        )

        self.assertIn(expected_signature, self.verify_source)

    def test_verify_function_only_performs_catalog_lookups(self) -> None:
        # No data read/write of any kind - safe to call from an
        # unauthenticated-by-RLS-bypass service-role context on every
        # deploy without side effects.
        self.assertIn("to_regclass(", self.verify_source)
        self.assertIn("to_regprocedure(", self.verify_source)
        self.assertNotIn("insert into", self.verify_source.lower())
        self.assertNotIn("update ", self.verify_source.lower())
        self.assertNotIn("delete from", self.verify_source.lower())

    def test_verify_script_checks_all_three_required_objects(self) -> None:
        for key in (
            "event_strategy_approvals_table_exists",
            "approve_strategy_draft_function_exists",
            "insert_next_expectation_version_function_exists",
        ):
            self.assertIn(key, self.verify_script_source)
            self.assertIn(key, self.verify_source)


if __name__ == "__main__":
    unittest.main()
