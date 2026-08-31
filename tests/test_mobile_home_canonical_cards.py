from pathlib import Path


HOME_SOURCE = Path("mobile/src/app/(tabs)/index.tsx")
TRACKED_SECTION_SOURCE = Path("mobile/src/components/TrackedEventsSection.tsx")


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


def test_unloaded_tracked_shells_still_pass_normal_stale_filtering():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    tracked_guard = source.index("if (event.event_id.startsWith('tracked:'))")
    today_guard = source.index("const today = formatLocalDate(new Date());", tracked_guard)
    loaded_return = source.index("if (loadedTrackedEventIds.has(trackedEventId)) return false;", tracked_guard)
    assert loaded_return < today_guard
    assert "return !loadedTrackedEventIds.has(trackedEventId);" not in source


def test_uncertain_past_expectations_remain_visible_until_status_is_known():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "if (event.scheduled_date >= today) return true;" in source
    assert "if (!status || status.statusError) return true;" in source
    assert "return status.run?.status === 'waiting_confirmation';" in source
    assert "for (const event of visibleEvents ?? [])" in source


def test_canonical_tracked_card_preserves_expectation_navigation():
    tracked = TRACKED_SECTION_SOURCE.read_text(encoding="utf-8")

    assert "pathname: '/events/[eventId]'" in tracked
    assert "const expectationEventId = event.calendar_event_id" in tracked
    assert "? `calendar:${event.calendar_event_id}`" in tracked
    assert ": `tracked:${event.event_id}`;" in tracked
    assert "params: { eventId: expectationEventId }" in tracked
    assert "Odotukset ja strategia →" in tracked
