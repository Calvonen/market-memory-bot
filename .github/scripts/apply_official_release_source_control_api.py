from pathlib import Path

api = Path("trading_system/api.py")
text = api.read_text()

import_needle = "from trading_system.paper_trade_repository import SupabasePaperTradeRepository\n"
import_block = """from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceEventNotFound,
    OfficialReleaseSourceVersionConflict,
    SupabaseOfficialReleaseSourceRepository,
)
"""
if import_block not in text:
    text = text.replace(import_needle, import_needle + import_block, 1)

protocol_needle = """class PaperStatusRepository(Protocol):
    def get_latest_for_event(self, event_id: str) -> dict[str, Any] | None: ...


"""
protocol_block = """class OfficialReleaseSourceRepository(Protocol):
    def get(self, event_id: str) -> OfficialReleaseSource | None: ...
    def get_version(self, event_id: str) -> int: ...
    def set(
        self, source: OfficialReleaseSource, *, expected_version: int
    ) -> OfficialReleaseSource: ...
    def clear(self, event_id: str, *, expected_version: int) -> int: ...


"""
if protocol_block not in text:
    text = text.replace(protocol_needle, protocol_needle + protocol_block, 1)

request_needle = "class ManualCalendarEventRequest(BaseModel):\n"
request_block = """class OfficialReleaseSourceSetRequest(BaseModel):
    source_kind: Literal["direct_url", "results_page"]
    source_url: str = Field(min_length=1, max_length=2000)
    source_title: str | None = Field(default=None, max_length=500)
    expected_version: int = Field(ge=0)


"""
if request_block not in text:
    text = text.replace(request_needle, request_block + request_needle, 1)

payload_needle = """def _tracked_event_latest_reaction_payload(
    reaction: TrackedEventLatestReaction,
) -> dict[str, Any]:
"""
payload_block = """def _official_release_source_payload(
    event_id: str,
    source: OfficialReleaseSource | None,
    *,
    version: int,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "active": source is not None,
        "version": version,
        "source_kind": source.source_kind if source is not None else None,
        "source_url": source.source_url if source is not None else None,
        "source_title": source.source_title if source is not None else None,
    }


"""
if payload_block not in text:
    text = text.replace(payload_needle, payload_block + payload_needle, 1)

arg_needle = """    tracked_event_repository: SupabaseTrackedEventRepository | None = None,
    admin_token: str | None = None,
"""
arg_repl = """    tracked_event_repository: SupabaseTrackedEventRepository | None = None,
    official_release_source_repository: OfficialReleaseSourceRepository | None = None,
    admin_token: str | None = None,
"""
if arg_repl not in text:
    if arg_needle not in text:
        raise SystemExit("create_app argument insertion point not found")
    text = text.replace(arg_needle, arg_repl, 1)

cache_needle = """    tracked_event_repo_cache: SupabaseTrackedEventRepository | None = tracked_event_repository
    configured_admin_token = admin_token or os.environ.get("MARKETAI_ADMIN_API_KEY")
"""
cache_repl = """    tracked_event_repo_cache: SupabaseTrackedEventRepository | None = tracked_event_repository
    official_release_source_repo_cache: OfficialReleaseSourceRepository | None = (
        official_release_source_repository
    )
    configured_admin_token = admin_token or os.environ.get("MARKETAI_ADMIN_API_KEY")
"""
if cache_repl not in text:
    if cache_needle not in text:
        raise SystemExit("repository cache insertion point not found")
    text = text.replace(cache_needle, cache_repl, 1)

getter_needle = "    def require_admin(x_admin_token: str | None) -> None:\n"
getter_block = """    def get_official_release_source_repository() -> OfficialReleaseSourceRepository:
        nonlocal official_release_source_repo_cache
        if official_release_source_repo_cache is None:
            official_release_source_repo_cache = (
                SupabaseOfficialReleaseSourceRepository.from_env()
            )
        return official_release_source_repo_cache

    def require_event_exists(event_id: str) -> None:
        try:
            event = get_repository().get(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")

"""
if getter_block not in text:
    if getter_needle not in text:
        raise SystemExit("repository getter insertion point not found")
    text = text.replace(getter_needle, getter_block + getter_needle, 1)

route_block = """
    @app.get("/api/v1/events/{event_id}/official-release-source")
    def get_official_release_source(
        event_id: str,
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> dict[str, Any]:
        require_read(x_marketai_key)
        require_event_exists(event_id)
        try:
            source_repository = get_official_release_source_repository()
            source = source_repository.get(event_id)
            version = (
                source.version
                if source is not None and source.version is not None
                else source_repository.get_version(event_id)
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _official_release_source_payload(event_id, source, version=version)

    @app.put("/api/v1/events/{event_id}/official-release-source")
    def set_official_release_source(
        event_id: str,
        request: OfficialReleaseSourceSetRequest,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
    ) -> dict[str, Any]:
        require_control(x_marketai_control_key)
        require_event_exists(event_id)
        try:
            source = OfficialReleaseSource(
                event_id=event_id,
                source_kind=request.source_kind,
                source_url=request.source_url,
                source_title=request.source_title,
            )
            saved = get_official_release_source_repository().set(
                source, expected_version=request.expected_version
            )
        except OfficialReleaseSourceVersionConflict as exc:
            raise HTTPException(
                status_code=409, detail="Official release source version conflict"
            ) from exc
        except OfficialReleaseSourceEventNotFound as exc:
            raise HTTPException(status_code=404, detail="Event not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        assert saved.version is not None
        return _official_release_source_payload(event_id, saved, version=saved.version)

    @app.delete("/api/v1/events/{event_id}/official-release-source")
    def clear_official_release_source(
        event_id: str,
        expected_version: int = Query(..., ge=1),
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
    ) -> dict[str, Any]:
        require_control(x_marketai_control_key)
        require_event_exists(event_id)
        try:
            new_version = get_official_release_source_repository().clear(
                event_id, expected_version=expected_version
            )
        except OfficialReleaseSourceVersionConflict as exc:
            raise HTTPException(
                status_code=409, detail="Official release source version conflict"
            ) from exc
        except OfficialReleaseSourceEventNotFound as exc:
            raise HTTPException(status_code=404, detail="Event not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _official_release_source_payload(event_id, None, version=new_version)
"""
return_needle = "\n    return app\n"
if route_block not in text:
    if return_needle not in text:
        raise SystemExit("return app insertion point not found")
    text = text.replace(return_needle, route_block + return_needle, 1)
api.write_text(text)

repo = Path("trading_system/official_release_source_repository.py")
text = repo.read_text()
exception_needle = '_ALLOWED_SOURCE_KINDS = {"direct_url", "results_page"}\n\n\n'
exception_block = """class OfficialReleaseSourceVersionConflict(RuntimeError):
    pass


class OfficialReleaseSourceEventNotFound(RuntimeError):
    pass


def _raise_official_release_source_write_error(
    exc: Exception, *, operation: str
) -> None:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    message_text = str(message) if message is not None else str(exc)
    if code == "40001" or "version_conflict:" in message_text:
        raise OfficialReleaseSourceVersionConflict(
            "official release source version conflict"
        ) from exc
    if code == "P0002" or "event_not_found:" in message_text:
        raise OfficialReleaseSourceEventNotFound(
            "official release source event not found"
        ) from exc
    raise RuntimeError(f"official release source {operation} failed") from exc


"""
if exception_block not in text:
    text = text.replace(exception_needle, exception_needle + exception_block, 1)

old_set = """        response = self.client.rpc(
            self.SET_RPC,
            {
                "input_event_id": source.event_id,
                "input_source_kind": source.source_kind,
                "input_source_url": source.source_url,
                "input_source_title": source.source_title,
                "input_expected_version": expected_version,
            },
        ).execute()
"""
new_set = """        try:
            response = self.client.rpc(
                self.SET_RPC,
                {
                    "input_event_id": source.event_id,
                    "input_source_kind": source.source_kind,
                    "input_source_url": source.source_url,
                    "input_source_title": source.source_title,
                    "input_expected_version": expected_version,
                },
            ).execute()
        except Exception as exc:
            _raise_official_release_source_write_error(exc, operation="write")
            raise AssertionError("unreachable")
"""
if new_set not in text:
    if old_set not in text:
        raise SystemExit("set RPC insertion point not found")
    text = text.replace(old_set, new_set, 1)

old_clear = """        response = self.client.rpc(
            self.CLEAR_RPC,
            {
                "input_event_id": canonical_event_id,
                "input_expected_version": expected_version,
            },
        ).execute()
"""
new_clear = """        try:
            response = self.client.rpc(
                self.CLEAR_RPC,
                {
                    "input_event_id": canonical_event_id,
                    "input_expected_version": expected_version,
                },
            ).execute()
        except Exception as exc:
            _raise_official_release_source_write_error(exc, operation="clear")
            raise AssertionError("unreachable")
"""
if new_clear not in text:
    if old_clear not in text:
        raise SystemExit("clear RPC insertion point not found")
    text = text.replace(old_clear, new_clear, 1)
repo.write_text(text)

for path in (api, repo):
    normalized = "\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n"
    path.write_text(normalized)
