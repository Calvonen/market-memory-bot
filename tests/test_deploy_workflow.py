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
        # The live verify_strategy_draft_schema() is the one (re)declared
        # by EXPECTATION_WRITE_V2_MIGRATION, not the one originally
        # declared in VERIFY_MIGRATION - but unlike that original file
        # (which held nothing else), this one also declares
        # insert_next_expectation_version() alongside it, so this must be
        # sliced down to just verify_strategy_draft_schema()'s own
        # definition, not the whole file. It's declared with a plain
        # `create function` here (not `create or replace`), since Postgres
        # rejects `create or replace` for a same-signature RETURNS TABLE
        # shape change - see the migration's own top-of-file note.
        verify_start = cls.write_v2_source.index(
            "create function public.verify_strategy_draft_schema()"
        )
        verify_end = cls.write_v2_source.index("$$;", verify_start) + len("$$;")
        cls.verify_source = cls.write_v2_source[verify_start:verify_end]
        cls.verify_script_source = VERIFY_SCRIPT.read_text(encoding="utf-8")

    @staticmethod
    def _declared_param_types(source: str, function_name: str) -> list[str]:
        # Matches both `create or replace function ...` (used where the
        # return shape is unchanged) and a plain `create function ...`
        # (used, after an explicit `drop function`, where it isn't - see
        # 20260823090000_expectation_write_atomic_response_and_schema_version.sql).
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
        # Checked against the live declaration (EXPECTATION_WRITE_V2_MIGRATION),
        # not the original one in SHARED_LOCK_MIGRATION - the two happen to
        # declare the same parameter list today (only the body/return type
        # changed), but this test's job is to confirm the gate matches
        # whatever is actually live, not merely whatever the function was
        # first declared as.
        declared_types = self._declared_param_types(
            self.write_v2_source, "insert_next_expectation_version"
        )
        expected_signature = (
            "public.insert_next_expectation_version(" + ", ".join(declared_types) + ")"
        )

        self.assertIn(expected_signature, self.verify_source)

        # And the two files must not have silently drifted apart on the
        # parameter list either - if they ever did, that would itself be a
        # sign this same-signature "no new migration needed" assumption
        # had quietly broken.
        self.assertEqual(
            declared_types,
            self._declared_param_types(self.shared_lock_source, "insert_next_expectation_version"),
        )

    def test_verify_function_only_performs_catalog_lookups(self) -> None:
        # No data read/write of any kind - safe to call from an
        # unauthenticated-by-RLS-bypass service-role context on every
        # deploy without side effects.
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
        ):
            self.assertIn(key, self.verify_script_source)
            self.assertIn(key, self.verify_source)

    # -- implementation-version marker: same signature, different body ----

    def _declared_schema_version_constant(self) -> str:
        # strategy_draft_schema_version() body is exactly `select N;` -
        # pull N out so the two places that must agree on it (the marker
        # function itself, and verify_strategy_draft_schema()'s comparison
        # against it) can be checked against each other dynamically,
        # rather than as two independently hand-typed literals that could
        # drift apart.
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
        # Called on every deploy, so it must be cheap and side-effect-free -
        # exactly like verify_strategy_draft_schema() itself.
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
        # Regression guard for the write -> reread race: the function's
        # return table must carry every field a caller needs to build a
        # complete EventExpectation - including the event's own identity,
        # which lives on market_events, not event_expectation_versions -
        # so nothing calling this RPC ever needs a separate, unlocked
        # follow-up read to get a full result back.
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
        # The `return query select ...` must use the same merged_* local
        # variables the insert itself used, never a fresh `select ... from
        # event_expectation_versions`/`market_events` - a fresh select
        # here is exactly the reread race this migration exists to close.
        return_start = self.write_v2_source.index("return query select")
        return_end = self.write_v2_source.index(";", return_start)
        return_block = self.write_v2_source[return_start:return_end]
        self.assertNotIn("select *", return_block.lower())
        self.assertNotIn(" from ", return_block.lower())
        self.assertIn("merged_consensus", return_block)
        self.assertIn("event_row.instrument", return_block)

    # -- same-signature return-shape change: drop-then-create, one transaction --

    def test_functions_with_a_changed_return_shape_are_dropped_before_recreating(
        self,
    ) -> None:
        # `create or replace function` cannot change an existing
        # function's return type, and a RETURNS TABLE(...)/OUT-parameter
        # shape change counts as one - Postgres rejects it outright
        # ("cannot change return type of existing function"). Both
        # functions below change shape versus their prior migration
        # (insert_next_expectation_version: 2 columns -> 15;
        # verify_strategy_draft_schema: 3 columns -> 4), so both need an
        # explicit drop for their exact prior signature immediately before
        # being recreated - a bare `create or replace` for either would
        # fail this migration outright when actually applied.
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
                # The create must immediately follow its own drop, not
                # some unrelated statement in between (and not the drop
                # for the *other* function landing between them either).
                between = self.write_v2_source[drop_index + len(drop_statement) : create_index]
                self.assertNotIn("drop function", between)
                self.assertNotIn("create function", between)
                self.assertNotIn(
                    f"create or replace function public.{function_name}(",
                    self.write_v2_source,
                )

    def test_migration_runs_as_one_explicit_transaction(self) -> None:
        # The drop-then-create pattern above is only safe from a
        # concurrent caller's perspective because Postgres DDL is
        # transactional - reproduced directly in manual testing: run
        # without this wrapper, a failure partway through this file left
        # strategy_draft_schema_version() (the schema-gate marker) created
        # and committed even though insert_next_expectation_version()
        # itself was never actually updated, which is exactly the
        # half-applied state the marker exists to make impossible.
        stripped = "\n".join(
            line
            for line in self.write_v2_source.splitlines()
            if not line.strip().startswith("--")
        )
        self.assertTrue(stripped.strip().lower().startswith("begin;"))
        self.assertTrue(stripped.strip().lower().endswith("commit;"))

    def test_schema_version_marker_is_created_after_the_rpc_it_gates(self) -> None:
        # Defense in depth on top of the transaction wrapper above: even
        # statement order alone should reflect "the marker means both RPCs
        # already changed," not the reverse - so a marker created before
        # insert_next_expectation_version() would misdescribe the intent
        # even though the transaction wrapper is what actually enforces
        # atomicity.
        insert_index = self.write_v2_source.index(
            "create function public.insert_next_expectation_version("
        )
        marker_index = self.write_v2_source.index(
            "create or replace function public.strategy_draft_schema_version()"
        )
        self.assertLess(insert_index, marker_index)


if __name__ == "__main__":
    unittest.main()
