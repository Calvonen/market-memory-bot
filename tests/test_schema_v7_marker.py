from scripts.verify_supabase_schema import REQUIRED_TRACKED_EVENT_RUNTIME_SCHEMA_VERSION


def test_runtime_schema_version_is_7():
    assert REQUIRED_TRACKED_EVENT_RUNTIME_SCHEMA_VERSION == 7
