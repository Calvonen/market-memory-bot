from pathlib import Path

MIGRATION = Path(
    "supabase/migrations/20260903110000_tracked_event_release_skip_audit.sql"
)
SERVICE = Path("trading_system/tracked_event_release_skip.py")


def test_skip_audit_is_append_only_and_runtime_gated() -> None:
    sql = MIGRATION.read_text()
    assert "select 15;" in sql
    assert "tracked_event_release_skip_audit_table_exists boolean" in sql
    assert "record_tracked_event_release_skip_function_exists boolean" in sql
    assert "security definer" in sql
    assert "grant execute on function public.record_tracked_event_release_skip" in sql
    assert "to service_role" in sql
    assert "grant insert" not in sql


def test_skip_audit_atomically_revalidates_complete_existing_binding() -> None:
    sql = MIGRATION.read_text()
    lowered = sql.lower()

    assert "for share" in lowered
    assert "calendar_row.event_type is distinct from tracked_row.kind" in sql
    assert "calendar_row.scheduled_date is distinct from tracked_row.event_date" in sql
    assert "calendar_row.source is distinct from tracked_row.source" in sql
    assert (
        "tracked_row.external_key is distinct from ('calendar:' || calendar_row.id::text)"
        in sql
    )
    assert (
        "existing_market_event.scheduled_date is distinct from tracked_row.event_date"
        in sql
    )
    assert "select e.version into locked_expectation_version" in sql
    assert "order by e.version desc" in sql
    assert "limit 1" in sql
    assert "if locked_expectation_version is null then" in sql
    assert "tracked_release_expectation_missing" in sql


def test_skip_service_never_calls_mutating_release_shell_ensure() -> None:
    service = SERVICE.read_text()
    assert "ensure_release_shell" not in service
    assert "ensure_tracked_event_release_shell_with_blocker" not in service
