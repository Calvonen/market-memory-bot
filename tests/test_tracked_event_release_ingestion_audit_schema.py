from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260903100000_tracked_event_release_ingestion_audit.sql"
)


def test_ingestion_audit_migration_extends_runtime_schema_gate() -> None:
    sql = MIGRATION.read_text()

    assert "select 14;" in sql
    assert "tracked_event_release_ingestion_audit_table_exists boolean" in sql
    assert (
        "record_tracked_event_release_ingestion_attempt_function_exists boolean" in sql
    )
    assert "to_regclass('public.tracked_event_release_ingestion_audit')" in sql
    assert (
        "to_regprocedure('public.record_tracked_event_release_ingestion_attempt"
        "(uuid,text,text,text)')"
        in sql
    )
