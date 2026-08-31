from pathlib import Path


HOME_SOURCE = Path("mobile/src/app/(tabs)/index.tsx")
TRACKED_SECTION_SOURCE = Path("mobile/src/components/TrackedEventsSection.tsx")
TRACKED_SERVICE_SOURCE = Path("mobile/src/services/tracked-events.ts")
TRACKED_RELEASE_API_SOURCE = Path("trading_system/tracked_event_release_source_api.py")


def test_loaded_tracked_event_ids_suppress_only_matching_shells():
    home = HOME_SOURCE.read_text(encoding="utf-8")
    tracked = TRACKED_SECTION_SOURCE.read_text(encoding="utf-8")

    assert "eventIds: string[];" in home
    assert "setPersistentEventIds(new Set(snapshot.eventIds));" in home
    assert "const trackedEventId = event.event_id.slice('tracked:'.length);" in home
    assert "if (loadedTrackedEventIds.has(trackedEventId)) return false;" in home
    assert "eventIds: list.map((event) => event.event_id)," in tracked
    assert "visibleEvents?.map((event) =>" in home
    assert "events?.map((event) =>" not in home


def test_canonical_card_list_stays_bounded_to_service_default():
    tracked = TRACKED_SECTION_SOURCE.read_text(encoding="utf-8")

    assert "return getTrackedEvents()" in tracked
    assert "getTrackedEvents('active', 100)" not in tracked


def test_past_unloaded_tracked_shell_uses_exact_canonical_activity():
    home = HOME_SOURCE.read_text(encoding="utf-8")
    service = TRACKED_SERVICE_SOURCE.read_text(encoding="utf-8")
    backend = TRACKED_RELEASE_API_SOURCE.read_text(encoding="utf-8")

    assert "getTrackedEventActivity" in home
    assert "event.scheduled_date < today" in home
    assert "getTrackedEventActivity(trackedEventId)" in home
    assert "activity.active ? 'active' : 'inactive'" in home
    assert "if (trackedActivity !== 'inactive') return true;" in home
    assert "export function getTrackedEventActivity(" in service
    assert 'router.get("/api/v1/tracked-events/{event_id}/activity")' in backend
    assert '"exists": False, "active": False' in backend


def test_tracked_activity_lookup_fails_open_for_active_workflow_safety():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "[event.event_id]: 'error'" in source
    assert "if (trackedActivity !== 'inactive') return true;" in source


def test_uncertain_past_expectations_remain_visible_until_status_is_known():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "if (event.scheduled_date >= today) return true;" in source
    assert "if (!status || status.statusError) return true;" in source
    assert "return status.run?.status === 'waiting_confirmation';" in source
    assert "for (const event of visibleEvents ?? [])" in source


def test_canonical_tracked_card_preserves_expectation_navigation_only_for_earnings():
    tracked = TRACKED_SECTION_SOURCE.read_text(encoding="utf-8")

    assert "pathname: '/events/[eventId]'" in tracked
    assert "event.kind === 'earnings'" in tracked
    assert "? `calendar:${event.calendar_event_id}`" in tracked
    assert ": `tracked:${event.event_id}`" in tracked
    assert "{expectationEventId ? (" in tracked
    assert "params: { eventId: expectationEventId }" in tracked
    assert "Odotukset ja strategia →" in tracked
