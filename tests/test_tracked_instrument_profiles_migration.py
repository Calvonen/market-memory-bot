import re
from pathlib import Path


MIGRATION_PATH = Path(
    "supabase/migrations/20260901123000_tracked_instrument_profiles.sql"
)


def test_profiles_attach_to_canonical_tracked_instrument() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "create table public.tracked_instrument_profiles" in sql
    assert "references public.tracked_instruments(id) on delete cascade" in sql
    assert "unique (tracked_instrument_id, profile_type)" in sql


def test_initial_profile_types_and_specs_are_deliberately_bounded() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "profile_type in ('earnings', 'trend', 'future_tech')" in sql
    assert "length(specs) <= 4000" in sql
    assert "enabled boolean not null default true" in sql


def test_profiles_are_not_a_direct_client_write_surface() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "alter table public.tracked_instrument_profiles enable row level security" in sql
    assert "grant select on table public.tracked_instrument_profiles to service_role" in sql
    assert "grant insert" not in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql


def test_profile_migration_does_not_write_downstream_event_or_trading_state() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    dml_targets = [
        target
        for _verb, target in re.findall(
            r"(?im)^\s*(insert\s+into|update|delete\s+from|truncate(?:\s+table)?)\s+([a-z_][a-z0-9_.]*)",
            sql,
        )
    ]
    assert dml_targets == []

    for forbidden in (
        "create trigger",
        "strategyengine",
        "riskengine",
        "paperbroker",
        "etorodemobroker",
    ):
        assert forbidden not in sql
