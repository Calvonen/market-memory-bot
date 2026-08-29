from pathlib import Path

MIGRATION = Path(
    "supabase/migrations/20260903110000_tracked_event_release_skip_audit.sql"
)


def test_skip_audit_is_append_only_and_runtime_gated() -> None:
    sql = MIGRATION.read_text()
    assert "select 15;" in sql
    assert "tracked_event_release_skip_audit_table_exists boolean" in sql
    assert "record_tracked_event_release_skip_function_exists boolean" in sql
    assert "security definer" in sql
    assert "grant execute on function public.record_tracked_event_release_skip" in sql
    assert "to service_role" in sql
    assert "grant insert" not in sql
