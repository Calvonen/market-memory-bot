from pathlib import Path

api_path = Path('trading_system/api.py')
text = api_path.read_text()

old = '''from trading_system.official_release_source_repository import (\n    OfficialReleaseSource,\n    OfficialReleaseSourceEventNotFound,\n    OfficialReleaseSourceVersionConflict,\n    SupabaseOfficialReleaseSourceRepository,\n)'''
new = '''from trading_system.official_release_source_repository import (\n    OfficialReleaseSource,\n    OfficialReleaseSourceEventNotFound,\n    OfficialReleaseSourceState,\n    OfficialReleaseSourceVersionConflict,\n    SupabaseOfficialReleaseSourceRepository,\n)'''
assert old in text
text = text.replace(old, new, 1)

old = '''class OfficialReleaseSourceRepository(Protocol):\n    def get(self, event_id: str) -> OfficialReleaseSource | None: ...\n    def get_version(self, event_id: str) -> int: ...\n    def set(\n        self, source: OfficialReleaseSource, *, expected_version: int\n    ) -> OfficialReleaseSource: ...\n    def clear(self, event_id: str, *, expected_version: int) -> int: ...'''
new = '''class OfficialReleaseSourceRepository(Protocol):\n    def get_state(self, event_id: str) -> OfficialReleaseSourceState: ...\n    def set(\n        self, source: OfficialReleaseSource, *, expected_version: int, actor: str\n    ) -> OfficialReleaseSource: ...\n    def clear(\n        self, event_id: str, *, expected_version: int, actor: str\n    ) -> int: ...'''
assert old in text
text = text.replace(old, new, 1)

needle = '''def _require_valid_tracked_event_id(event_id: str) -> str:\n    if _POSTGRES_UUID_TEXT.fullmatch(event_id) is None:\n        raise HTTPException(\n            status_code=400, detail="event_id must be a valid UUID"\n        )\n    return event_id\n\n\nclass OfficialReleaseSourceSetRequest(BaseModel):'''
replacement = '''def _require_valid_tracked_event_id(event_id: str) -> str:\n    if _POSTGRES_UUID_TEXT.fullmatch(event_id) is None:\n        raise HTTPException(\n            status_code=400, detail="event_id must be a valid UUID"\n        )\n    return event_id\n\n\ndef _require_approval_actor(actor: str | None) -> str:\n    canonical_actor = actor.strip() if actor is not None else ""\n    if not canonical_actor:\n        raise HTTPException(status_code=422, detail="X-MarketAI-Actor is required")\n    if len(canonical_actor) > 200:\n        raise HTTPException(status_code=422, detail="X-MarketAI-Actor is too long")\n    return canonical_actor\n\n\nclass OfficialReleaseSourceSetRequest(BaseModel):'''
assert needle in text
text = text.replace(needle, replacement, 1)

old = '''        try:\n            source_repository = get_official_release_source_repository()\n            source = source_repository.get(event_id)\n            version = (\n                source.version\n                if source is not None and source.version is not None\n                else source_repository.get_version(event_id)\n            )\n        except RuntimeError as exc:\n            raise HTTPException(status_code=503, detail=str(exc)) from exc\n        return _official_release_source_payload(event_id, source, version=version)'''
new = '''        try:\n            state = get_official_release_source_repository().get_state(event_id)\n        except RuntimeError as exc:\n            raise HTTPException(status_code=503, detail=str(exc)) from exc\n        return _official_release_source_payload(\n            event_id, state.source, version=state.version\n        )'''
assert old in text
text = text.replace(old, new, 1)

old = '''    def set_official_release_source(\n        event_id: str,\n        request: OfficialReleaseSourceSetRequest,\n        x_marketai_control_key: str | None = Header(\n            default=None, alias="X-MarketAI-Control-Key"\n        ),\n    ) -> dict[str, Any]:\n        require_control(x_marketai_control_key)\n        require_event_exists(event_id)'''
new = '''    def set_official_release_source(\n        event_id: str,\n        request: OfficialReleaseSourceSetRequest,\n        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),\n        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),\n    ) -> dict[str, Any]:\n        require_admin(x_admin_token)\n        actor = _require_approval_actor(x_marketai_actor)\n        require_event_exists(event_id)'''
assert old in text
text = text.replace(old, new, 1)

old = '''            saved = get_official_release_source_repository().set(\n                source, expected_version=request.expected_version\n            )'''
new = '''            saved = get_official_release_source_repository().set(\n                source, expected_version=request.expected_version, actor=actor\n            )'''
assert old in text
text = text.replace(old, new, 1)

old = '''    def clear_official_release_source(\n        event_id: str,\n        expected_version: int = Query(..., ge=1),\n        x_marketai_control_key: str | None = Header(\n            default=None, alias="X-MarketAI-Control-Key"\n        ),\n    ) -> dict[str, Any]:\n        require_control(x_marketai_control_key)\n        require_event_exists(event_id)'''
new = '''    def clear_official_release_source(\n        event_id: str,\n        expected_version: int = Query(..., ge=1),\n        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),\n        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),\n    ) -> dict[str, Any]:\n        require_admin(x_admin_token)\n        actor = _require_approval_actor(x_marketai_actor)\n        require_event_exists(event_id)'''
assert old in text
text = text.replace(old, new, 1)

old = '''            new_version = get_official_release_source_repository().clear(\n                event_id, expected_version=expected_version\n            )'''
new = '''            new_version = get_official_release_source_repository().clear(\n                event_id, expected_version=expected_version, actor=actor\n            )'''
assert old in text
text = text.replace(old, new, 1)

api_path.write_text(text)

Path('tests/test_official_release_source_control_api.py').write_text(r'''from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceEventNotFound,
    OfficialReleaseSourceState,
    OfficialReleaseSourceVersionConflict,
    SupabaseOfficialReleaseSourceRepository,
)

EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"
READ_KEY = "read-key"
CONTROL_KEY = "control-key"
ADMIN_TOKEN = "admin-token"
ACTOR = "marko"


class _EventRepository:
    def __init__(self, exists: bool = True):
        self.exists = exists

    def get(self, event_id: str):
        return object() if self.exists and event_id == EVENT_ID else None


class _SourceRepository:
    def __init__(self):
        self.source = None
        self.version = 0
        self.state_calls = []
        self.set_calls = []
        self.clear_calls = []
        self.set_error = None

    def get_state(self, event_id: str):
        self.state_calls.append(event_id)
        return OfficialReleaseSourceState(self.source, self.version)

    def set(self, source: OfficialReleaseSource, *, expected_version: int, actor: str):
        self.set_calls.append((source, expected_version, actor))
        if self.set_error is not None:
            raise self.set_error
        if expected_version != self.version:
            raise OfficialReleaseSourceVersionConflict()
        self.version += 1
        self.source = OfficialReleaseSource(
            event_id=source.event_id,
            source_kind=source.source_kind,
            source_url=source.source_url,
            source_title=source.source_title,
            version=self.version,
        )
        return self.source

    def clear(self, event_id: str, *, expected_version: int, actor: str):
        self.clear_calls.append((event_id, expected_version, actor))
        if expected_version != self.version:
            raise OfficialReleaseSourceVersionConflict()
        self.version += 1
        self.source = None
        return self.version


def _client(source_repository=None, event_repository=None):
    return TestClient(
        create_app(
            repository=event_repository or _EventRepository(),
            official_release_source_repository=source_repository or _SourceRepository(),
            read_api_key=READ_KEY,
            control_api_key=CONTROL_KEY,
            admin_token=ADMIN_TOKEN,
        )
    )


class OfficialReleaseSourceControlApiTests(unittest.TestCase):
    def test_get_requires_read_auth_and_reads_state_once(self):
        source_repository = _SourceRepository()
        source_repository.version = 4
        client = _client(source_repository)
        unauthorized = client.get(f"/api/v1/events/{EVENT_ID}/official-release-source")
        self.assertEqual(unauthorized.status_code, 401)
        response = client.get(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Key": READ_KEY},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(source_repository.state_calls, [EVENT_ID])
        self.assertEqual(response.json()["active"], False)
        self.assertEqual(response.json()["version"], 4)

    def test_put_rejects_mobile_control_key_and_requires_admin_actor(self):
        source_repository = _SourceRepository()
        client = _client(source_repository)
        body = {
            "source_kind": "results_page",
            "source_url": "https://investor.example.com/results",
            "source_title": " Results ",
            "expected_version": 0,
        }
        control_only = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY, "X-MarketAI-Actor": ACTOR},
            json=body,
        )
        self.assertEqual(control_only.status_code, 401)
        missing_actor = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN},
            json=body,
        )
        self.assertEqual(missing_actor.status_code, 422)
        self.assertEqual(source_repository.set_calls, [])
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": "  marko  "},
            json=body,
        )
        self.assertEqual(response.status_code, 200)
        saved, expected, actor = source_repository.set_calls[-1]
        self.assertEqual((expected, actor, saved.source_title), (0, ACTOR, "Results"))

    def test_put_rejects_invalid_url_before_repository_write(self):
        source_repository = _SourceRepository()
        client = _client(source_repository)
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
            json={
                "source_kind": "direct_url",
                "source_url": "http://investor.example.com/results.pdf",
                "expected_version": 0,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(source_repository.set_calls, [])

    def test_put_version_conflict_is_409(self):
        source_repository = _SourceRepository()
        source_repository.version = 2
        client = _client(source_repository)
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
            json={
                "source_kind": "direct_url",
                "source_url": "https://investor.example.com/results.pdf",
                "expected_version": 1,
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_delete_requires_admin_actor_and_records_actor(self):
        source_repository = _SourceRepository()
        source_repository.version = 3
        source_repository.source = OfficialReleaseSource(
            EVENT_ID, "direct_url", "https://investor.example.com/results.pdf", version=3
        )
        client = _client(source_repository)
        control_only = client.delete(
            f"/api/v1/events/{EVENT_ID}/official-release-source?expected_version=3",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY, "X-MarketAI-Actor": ACTOR},
        )
        self.assertEqual(control_only.status_code, 401)
        response = client.delete(
            f"/api/v1/events/{EVENT_ID}/official-release-source?expected_version=3",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(source_repository.clear_calls[-1], (EVENT_ID, 3, ACTOR))
        self.assertEqual(response.json()["version"], 4)

    def test_unknown_event_is_404_before_source_repository_access(self):
        source_repository = _SourceRepository()
        client = _client(source_repository, _EventRepository(exists=False))
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
            json={
                "source_kind": "results_page",
                "source_url": "https://investor.example.com/results",
                "expected_version": 0,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(source_repository.set_calls, [])

    def test_repository_failure_is_503(self):
        source_repository = _SourceRepository()
        source_repository.set_error = RuntimeError("source backend unavailable")
        client = _client(source_repository)
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
            json={
                "source_kind": "results_page",
                "source_url": "https://investor.example.com/results",
                "expected_version": 0,
            },
        )
        self.assertEqual(response.status_code, 503)


class _ApiError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _ErrorRpc:
    def __init__(self, error): self.error = error
    def execute(self): raise self.error


class _ErrorClient:
    def __init__(self, error): self.error = error
    def rpc(self, name, payload): return _ErrorRpc(self.error)


class OfficialReleaseSourceRepositoryErrorTranslationTests(unittest.TestCase):
    def _source(self):
        return OfficialReleaseSource(EVENT_ID, "direct_url", "https://investor.example.com/results.pdf")

    def test_set_translates_marked_sqlstate_version_conflict(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("40001", "version_conflict: expected 1, current 2"))
        )
        with self.assertRaises(OfficialReleaseSourceVersionConflict):
            repository.set(self._source(), expected_version=1, actor=ACTOR)

    def test_bare_serialization_failure_stays_service_failure(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("40001", "could not serialize access due to concurrent update"))
        )
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            repository.set(self._source(), expected_version=1, actor=ACTOR)

    def test_set_translates_missing_event(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("P0002", f"event_not_found: {EVENT_ID}"))
        )
        with self.assertRaises(OfficialReleaseSourceEventNotFound):
            repository.set(self._source(), expected_version=0, actor=ACTOR)

    def test_unknown_rpc_error_stays_service_failure(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("08006", "connection failure"))
        )
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            repository.set(self._source(), expected_version=0, actor=ACTOR)


if __name__ == "__main__":
    unittest.main()
''')

Path('tests/test_official_release_source_approval_audit_sql.py').write_text(r'''from pathlib import Path
import unittest

MIGRATION = Path("supabase/migrations/20260902104000_official_release_source_approval_audit.sql")


class OfficialReleaseSourceApprovalAuditSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text()

    def test_audit_is_append_only_service_readable(self):
        self.assertIn("create table public.event_official_release_source_audit", self.sql)
        self.assertIn("revoke all on table public.event_official_release_source_audit", self.sql)
        self.assertIn("grant select on table public.event_official_release_source_audit to service_role", self.sql)

    def test_old_unaudited_rpcs_are_revoked_from_service_role(self):
        self.assertIn("revoke all on function public.set_event_official_release_source(text, text, text, text, integer)\n  from service_role", self.sql)
        self.assertIn("revoke all on function public.clear_event_official_release_source(text, integer)\n  from service_role", self.sql)

    def test_approved_rpcs_require_actor_and_write_audit(self):
        self.assertIn("set_event_official_release_source_approved", self.sql)
        self.assertIn("clear_event_official_release_source_approved", self.sql)
        self.assertGreaterEqual(self.sql.count("input_actor text"), 2)
        self.assertGreaterEqual(self.sql.count("insert into public.event_official_release_source_audit"), 2)

    def test_schema_gate_requires_audit_contract_without_shape_change(self):
        self.assertIn("create or replace function public.verify_official_release_source_schema()", self.sql)
        self.assertIn("event_official_release_sources_table_exists boolean", self.sql)
        self.assertIn("event_official_release_source_audit", self.sql)
        self.assertIn("set_event_official_release_source_approved", self.sql)
        self.assertIn("clear_event_official_release_source_approved", self.sql)


if __name__ == "__main__":
    unittest.main()
''')
