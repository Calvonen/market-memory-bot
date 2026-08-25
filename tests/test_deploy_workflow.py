import re
import shutil
import subprocess
import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/deploy-seesam-hub.yml")
EVENT_STRATEGY_APPROVALS_MIGRATION = Path(
    "supabase/migrations/20260821090000_event_strategy_approvals.sql"
)
SHARED_LOCK_MIGRATION = Path(
    "supabase/migrations/20260821140000_shared_expectation_version_lock.sql"
)
# Superseded by EXPECTATION_WRITE_V2_MIGRATION below - kept only as the
# historical record of the gate's original three checks (see the
# SUPERSEDED note at the top of the file itself). Nothing in this test
# file reads from it for "what actually runs today" purposes.
VERIFY_MIGRATION = Path(
    "supabase/migrations/20260822090000_verify_strategy_draft_schema.sql"
)
# `create or replace`s both insert_next_expectation_version() (adding the
# full-row return, closing the write -> reread race) and
# verify_strategy_draft_schema() (adding schema_version_matches) on top of
# SHARED_LOCK_MIGRATION/VERIFY_MIGRATION above - this is the source of
# truth for what is actually deployed.
EXPECTATION_WRITE_V2_MIGRATION = Path(
    "supabase/migrations/20260823090000_expectation_write_atomic_response_and_schema_version.sql"
)
CALENDAR_MIGRATION = Path("supabase/migrations/20260824090000_calendar_watchlist_events.sql")
# create or replace`s verify_strategy_draft_schema() again (adding the
# calendar_events/upsert_calendar_candidate()/
# transition_calendar_event_status() checks) on top of everything above -
# this, not EXPECTATION_WRITE_V2_MIGRATION, is the source of truth for
# verify_strategy_draft_schema()'s live definition.
CALENDAR_SCHEMA_GATE_MIGRATION = Path(
    "supabase/migrations/20260825090000_calendar_schema_gate.sql"
)
CALENDAR_UPSERT_VERSION_GATE_MIGRATION = Path(
    "supabase/migrations/20260902099000_calendar_tracked_date_refresh_gate.sql"
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
        self.assertIn("source /home/marko/marketai/.env", body)

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
        cls.write_v2_source = EXPECTATION_WRITE_V2_MIGRATION.read_text(encoding="utf-8")
        cls.calendar_source = CALENDAR_MIGRATION.read_text(encoding="utf-8")
        cls.calendar_gate_source = CALENDAR_UPSERT_VERSION_GATE_MIGRATION.read_text(
            encoding="utf-8"
        )
        verify_start = cls.calendar_gate_source.index(
            "create function public.verify_strategy_draft_schema()"
        )
        verify_end = cls.calendar_gate_source.index("$$;", verify_start) + len("$$;")
        cls.verify_source = cls.calendar_gate_source[verify_start:verify_end]
        cls.verify_script_source = VERIFY_SCRIPT.read_text(encoding="utf-8")

    @staticmethod
    def _declared_param_types(source: str, function_name: str) -> list[str]:
        match = re.search(
            rf"create (?:or replace )?function public\.{re.escape(function_name)}\(\s*(.*?)\)\s*\n",
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
            self.write_v2_source, "insert_next_expectation_version"
        )
        expected_signature = (
            "public.insert_next_expectation_version(" + ", ".join(declared_types) + ")"
        )
        self.assertIn(expected_signature, self.verify_source)
        self.assertEqual(
            declared_types,
            self._declared_param_types(self.shared_lock_source, "insert_next_expectation_version"),
        )

    def test_verify_function_checks_the_real_upsert_calendar_candidate_signature(
        self,
    ) -> None:
        declared_types = self._declared_param_types(
            self.calendar_source, "upsert_calendar_candidate"
        )
        expected_signature = (
            "public.upsert_calendar_candidate(" + ", ".join(declared_types) + ")"
        )
        self.assertIn(expected_signature, self.verify_source)

    def test_verify_function_checks_the_real_transition_calendar_event_status_signature(
        self,
    ) -> None:
        declared_types = self._declared_param_types(
            self.calendar_source, "transition_calendar_event_status"
        )
        expected_signature = (
            "public.transition_calendar_event_status(" + ", ".join(declared_types) + ")"
        )
        self.assertIn(expected_signature, self.verify_source)

    def test_calendar_schema_gate_drops_verify_function_before_recreating(self) -> None:
        drop_statement = "drop function if exists public.verify_strategy_draft_schema();"
        self.assertIn(drop_statement, self.calendar_gate_source)
        drop_index = self.calendar_gate_source.index(drop_statement)
        create_index = self.calendar_gate_source.index(
            "create function public.verify_strategy_draft_schema()", drop_index
        )
        between = self.calendar_gate_source[drop_index + len(drop_statement) : create_index]
        self.assertNotIn("drop function", between)
        self.assertNotIn("create function", between)
        self.assertNotIn(
            "create or replace function public.verify_strategy_draft_schema(",
            self.calendar_gate_source,
        )

    def test_calendar_schema_gate_runs_as_one_explicit_transaction(self) -> None:
        stripped = "\n".join(
            line
            for line in self.calendar_gate_source.splitlines()
            if not line.strip().startswith("--")
        )
        self.assertTrue(stripped.strip().lower().startswith("begin;"))
        self.assertTrue(stripped.strip().lower().endswith("commit;"))

    def test_verify_function_only_performs_catalog_lookups(self) -> None:
        self.assertIn("to_regclass(", self.verify_source)
        self.assertIn("to_regprocedure(", self.verify_source)
        self.assertNotIn("insert into", self.verify_source.lower())
        self.assertNotIn("update ", self.verify_source.lower())
        self.assertNotIn("delete from", self.verify_source.lower())

    def test_verify_script_checks_all_required_objects(self) -> None:
        for key in (
            "event_strategy_approvals_table_exists",
            "approve_strategy_draft_function_exists",
            "insert_next_expectation_version_function_exists",
            "schema_version_matches",
            "calendar_events_table_exists",
            "upsert_calendar_candidate_function_exists",
            "transition_calendar_event_status_function_exists",
            "calendar_candidate_upsert_version_matches",
        ):
            self.assertIn(key, self.verify_script_source)
            self.assertIn(key, self.verify_source)

    def test_calendar_upsert_marker_version_is_required_explicitly(self) -> None:
        marker = re.search(
            r"create or replace function public\.calendar_candidate_upsert_version\(\).*?"
            r"select\s+(\d+)\s*;",
            self.calendar_gate_source,
            re.DOTALL,
        )
        self.assertIsNotNone(marker)
        version = marker.group(1)
        self.assertIn(
            f"public.calendar_candidate_upsert_version() = {version}",
            self.verify_source,
        )
        self.assertIn("immutable", marker.group(0))
        self.assertIn(
            f"REQUIRED_CALENDAR_CANDIDATE_UPSERT_VERSION = {version}",
            self.verify_script_source,
        )
        self.assertIn("calendar_candidate_upsert_implementation_version", self.verify_source)
        self.assertIn("calendar_candidate_upsert_implementation_version", self.verify_script_source)

    def _declared_schema_version_constant(self) -> str:
        match = re.search(
            r"create or replace function public\.strategy_draft_schema_version\(\).*?"
            r"select\s+(\d+)\s*;",
            self.write_v2_source,
            re.DOTALL,
        )
        assert match is not None, "strategy_draft_schema_version() declaration not found"
        return match.group(1)

    def test_verify_function_checks_schema_version_against_the_declared_marker(
        self,
    ) -> None:
        version = self._declared_schema_version_constant()
        self.assertIn(
            f"public.strategy_draft_schema_version() = {version}", self.verify_source
        )

    def test_schema_version_marker_is_immutable_and_takes_no_lock(self) -> None:
        marker_start = self.write_v2_source.index(
            "create or replace function public.strategy_draft_schema_version()"
        )
        marker_end = self.write_v2_source.index("$$;", marker_start)
        marker_body = self.write_v2_source[marker_start:marker_end]
        self.assertIn("immutable", marker_body)
        self.assertNotIn("pg_advisory", marker_body)

    def test_insert_next_expectation_version_returns_the_full_written_row(
        self,
    ) -> None:
        declare_start = self.write_v2_source.index(
            "create function public.insert_next_expectation_version("
        )
        returns_start = self.write_v2_source.index("returns table (", declare_start)
        returns_end = self.write_v2_source.index(")", returns_start)
        returns_block = self.write_v2_source[returns_start:returns_end]
        for column in (
            "out_version",
            "out_created_at",
            "out_instrument",
            "out_event_name",
            "out_scheduled_date",
            "out_source_name",
            "out_source_url",
            "out_source_as_of",
            "out_consensus",
            "out_important_kpis",
            "out_bull_case",
            "out_base_case",
            "out_bear_case",
            "out_triggers",
            "out_invalidation_conditions",
        ):
            self.assertIn(column, returns_block)

    def test_insert_next_expectation_version_return_values_are_not_re_selected(
        self,
    ) -> None:
        return_start = self.write_v2_source.index("return query select")
        return_end = self.write_v2_source.index(";", return_start)
        return_block = self.write_v2_source[return_start:return_end]
        self.assertNotIn("select *", return_block.lower())
        self.assertNotIn(" from ", return_block.lower())
        self.assertIn("merged_consensus", return_block)
        self.assertIn("event_row.instrument", return_block)

    def test_functions_with_a_changed_return_shape_are_dropped_before_recreating(
        self,
    ) -> None:
        for function_name, signature in (
            (
                "insert_next_expectation_version",
                "text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text",
            ),
            ("verify_strategy_draft_schema", ""),
        ):
            with self.subTest(function=function_name):
                drop_statement = f"drop function if exists public.{function_name}("
                self.assertIn(drop_statement, self.write_v2_source)
                drop_index = self.write_v2_source.index(drop_statement)
                create_index = self.write_v2_source.index(
                    f"create function public.{function_name}(", drop_index
                )
                between = self.write_v2_source[drop_index + len(drop_statement) : create_index]
                self.assertNotIn("drop function", between)
                self.assertNotIn("create function", between)
                self.assertNotIn(
                    f"create or replace function public.{function_name}(",
                    self.write_v2_source,
                )

    def test_migration_runs_as_one_explicit_transaction(self) -> None:
        stripped = "\n".join(
            line
            for line in self.write_v2_source.splitlines()
            if not line.strip().startswith("--")
        )
        self.assertTrue(stripped.strip().lower().startswith("begin;"))
        self.assertTrue(stripped.strip().lower().endswith("commit;"))

    def test_schema_version_marker_is_created_after_the_rpc_it_gates(self) -> None:
        insert_index = self.write_v2_source.index(
            "create function public.insert_next_expectation_version("
        )
        marker_index = self.write_v2_source.index(
            "create or replace function public.strategy_draft_schema_version()"
        )
        self.assertLess(insert_index, marker_index)


CALENDAR_SYNC_SERVICE_UNIT = Path("deploy/systemd/marketai-calendar-sync.service")
CALENDAR_SYNC_TIMER_UNIT = Path("deploy/systemd/marketai-calendar-sync.timer")


class CalendarSyncSchedulingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_source = WORKFLOW.read_text(encoding="utf-8")
        cls.service_source = CALENDAR_SYNC_SERVICE_UNIT.read_text(encoding="utf-8")
        cls.timer_source = CALENDAR_SYNC_TIMER_UNIT.read_text(encoding="utf-8")

    def _deploy_step_body(self) -> str:
        start = self.workflow_source.index("Deploy backend to seesam-hub (locked)")
        next_step = self.workflow_source.index("\n  publish-ota:", start)
        return self.workflow_source[start:next_step]

    def test_service_unit_is_a_one_shot_not_a_long_running_worker(self) -> None:
        self.assertIn("Type=oneshot", self.service_source)
        for forbidden in ("Type=simple", "Type=notify", "Type=forking", "Restart=always"):
            self.assertNotIn(forbidden, self.service_source)

    def test_service_unit_invokes_the_real_calendar_sync_worker_module(self) -> None:
        self.assertIn(
            "ExecStart=/home/marko/marketai-repo/.venv/bin/python -m trading_system.calendar_sync_worker",
            self.service_source,
        )
        self.assertIn("WorkingDirectory=/home/marko/marketai-repo", self.service_source)
        self.assertIn("EnvironmentFile=/home/marko/marketai/.env", self.service_source)

    def test_timer_unit_fires_on_a_schedule_and_survives_downtime(self) -> None:
        self.assertIn("OnCalendar=", self.timer_source)
        self.assertIn("Persistent=true", self.timer_source)
        self.assertIn("WantedBy=timers.target", self.timer_source)

    def test_timer_fires_at_most_a_few_times_a_day(self) -> None:
        match = re.search(r"OnCalendar=(.+)", self.timer_source)
        assert match is not None
        schedule = match.group(1).strip()
        self.assertNotIn("/", schedule, f"schedule {schedule!r} looks like a sub-daily repeat")
        self.assertIn("06", schedule)
        self.assertIn("18", schedule)

    def test_timer_schedule_is_syntactically_valid(self) -> None:
        systemd_analyze = shutil.which("systemd-analyze")
        if systemd_analyze is None:
            self.skipTest("systemd-analyze is not available in this environment")
        match = re.search(r"OnCalendar=(.+)", self.timer_source)
        assert match is not None
        result = subprocess.run(
            [systemd_analyze, "calendar", match.group(1).strip()],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Next elapse", result.stdout)

    def test_deploy_step_installs_and_enables_the_calendar_sync_timer(self) -> None:
        body = self._deploy_step_body()
        self.assertIn(
            "install -m 0644 deploy/systemd/marketai-calendar-sync.service "
            "/etc/systemd/system/marketai-calendar-sync.service",
            body,
        )
        self.assertIn(
            "install -m 0644 deploy/systemd/marketai-calendar-sync.timer "
            "/etc/systemd/system/marketai-calendar-sync.timer",
            body,
        )
        self.assertIn("systemctl daemon-reload", body)
        self.assertIn("systemctl enable --now marketai-calendar-sync.timer", body)
        self.assertNotIn("enable --now marketai-calendar-sync.service", body)
        self.assertNotIn("enable marketai-calendar-sync.service", body)

    def test_calendar_sync_wiring_runs_after_the_schema_gate_and_before_restarts(self) -> None:
        body = self._deploy_step_body()
        gate_index = body.index("scripts/verify_supabase_schema.py")
        install_index = body.index("systemctl daemon-reload")
        enable_index = body.index("systemctl enable --now marketai-calendar-sync.timer")
        api_restart_index = body.index("systemctl restart marketai-api.service")
        self.assertLess(gate_index, install_index)
        self.assertLess(install_index, enable_index)
        self.assertLess(enable_index, api_restart_index)

    def test_calendar_sync_wiring_is_not_silently_ignorable_on_failure(self) -> None:
        body = self._deploy_step_body()
        install_index = body.index("systemctl daemon-reload")
        enable_index = body.index("systemctl enable --now marketai-calendar-sync.timer")
        segment = body[install_index : enable_index + len("systemctl enable --now marketai-calendar-sync.timer")]
        self.assertNotIn("|| true", segment)
        self.assertNotIn("set +e", segment)


if __name__ == "__main__":
    unittest.main()
