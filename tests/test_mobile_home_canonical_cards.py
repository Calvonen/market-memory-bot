from pathlib import Path


HOME_SOURCE = Path("mobile/src/app/(tabs)/index.tsx")


def test_calendarless_tracked_expectations_do_not_render_duplicate_home_cards():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "if (event.event_id.startsWith('tracked:')) return false;" in source
    assert "visibleEvents?.map((event) =>" in source
    assert "events?.map((event) =>" not in source


def test_stale_past_expectations_stay_off_home_unless_waiting_for_confirmation():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "if (event.scheduled_date >= today) return true;" in source
    assert "return status?.run?.status === 'waiting_confirmation';" in source
    assert "for (const event of visibleEvents ?? [])" in source
