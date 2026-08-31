from pathlib import Path


HOME_SOURCE = Path("mobile/src/app/(tabs)/index.tsx")


def test_calendarless_tracked_expectations_hide_only_with_complete_snapshot():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "const TRACKED_EVENT_LIST_LIMIT = 20;" in source
    assert "if (event.event_id.startsWith('tracked:'))" in source
    assert "return !suppressTrackedShells;" in source
    assert "trackedEventCount < TRACKED_EVENT_LIST_LIMIT" in source
    assert "visibleEvents?.map((event) =>" in source
    assert "events?.map((event) =>" not in source


def test_uncertain_past_expectations_remain_visible_until_status_is_known():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "if (event.scheduled_date >= today) return true;" in source
    assert "if (!status || status.statusError) return true;" in source
    assert "return status.run?.status === 'waiting_confirmation';" in source
    assert "for (const event of visibleEvents ?? [])" in source
