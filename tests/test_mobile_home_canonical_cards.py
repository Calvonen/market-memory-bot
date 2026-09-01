from pathlib import Path


HOME_SOURCE = Path("mobile/src/app/(tabs)/index.tsx")
TRACKED_SECTION_SOURCE = Path("mobile/src/components/TrackedEventsSection.tsx")
TRACKED_SERVICE_SOURCE = Path("mobile/src/services/tracked-events.ts")
TRACKED_RELEASE_API_SOURCE = Path("trading_system/tracked_event_release_source_api.py")
TRACKED_REPOSITORY_SOURCE = Path("trading_system/tracked_event_repository.py")


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


def test_omitted_shells_use_batched_canonical_activity_regardless_of_date():
    home = HOME_SOURCE.read_text(encoding="utf-8")
    service = TRACKED_SERVICE_SOURCE.read_text(encoding="utf-8")
    backend = TRACKED_RELEASE_API_SOURCE.read_text(encoding="utf-8")
    repository = TRACKED_REPOSITORY_SOURCE.read_text(encoding="utf-8")

    assert "getTrackedEventActivities" in home
    assert "event.scheduled_date < today" not in home
    assert "isTrackedExpectation(event.event_id)" in home
    assert "persistentCalendarEventIds" in home
    assert "getTrackedEventActivities(candidateIds)" in home
    assert "activityByOccurrenceId[eventId]?.active ? 'active' : 'inactive'" in home
    assert "if (isTrackedExpectation(event.event_id) && trackedActivity === 'inactive') return false;" in home
    assert "if (isTrackedExpectation(event.event_id)) return true;" in home

    assert "export async function getTrackedEventActivities(" in service
    assert "TRACKED_EVENT_ACTIVITY_BATCH_SIZE = 40" in service
    assert "TRACKED_EVENT_ACTIVITY_CONCURRENCY = 3" in service
    assert "Math.min(TRACKED_EVENT_ACTIVITY_CONCURRENCY, batches.length)" in service
    assert "const batchIndex = nextBatchIndex++;" in service
    assert "/api/v1/tracked-events/activity?occurrence_ids=" in service

    assert 'router.get("/api/v1/tracked-events/activity")' in backend
    assert 'prefix not in {"tracked", "calendar"}' in backend
    assert "get_tracked_event_repository().get_by_occurrences(" in backend
    assert "repository.client" not in backend
    assert ".client.table(" not in backend
    assert "def get_by_occurrences(" in repository
    assert 'self.client.table("tracked_market_events")' in repository


def test_future_tracked_shell_checks_activity_before_date_visibility():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    inactive_guard = source.index(
        "if (isTrackedExpectation(event.event_id) && trackedActivity === 'inactive') return false;"
    )
    future_guard = source.index("if (event.scheduled_date >= today) return true;")
    assert inactive_guard < future_guard


def test_inactive_calendar_occurrence_cannot_reappear_as_fallback_card():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "const canonicalOccurrenceId = `calendar:${event.calendar_event_id}`;" in source
    assert "trackedActivityByEventId[canonicalOccurrenceId] === 'inactive'" in source
    tracked_calendar_start = source.index("const trackedCalendarEvents = useMemo")
    focus_start = source.index("useFocusEffect(", tracked_calendar_start)
    tracked_calendar_source = source[tracked_calendar_start:focus_start]
    assert "trackedActivityByEventId" in tracked_calendar_source


def test_activity_batch_waits_for_snapshot_and_updates_state_by_batch():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "if (!events || trackedEventCount === null) return;" in source
    assert "!isExpectationBackedByLoadedTrackedEvent(" in source
    assert "setTrackedActivityByEventId(loadingState);" in source
    assert "Object.fromEntries(" in source
    assert "candidateIds.map((eventId)" in source
    assert "getTrackedEventActivity(trackedEventId)" not in source


def test_activity_error_clears_when_snapshot_leaves_no_candidates():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    zero_candidate_start = source.index("if (candidateIds.length === 0) {")
    lookup_start = source.index("void Promise.resolve()", zero_candidate_start + 1)
    zero_candidate_block = source[zero_candidate_start:lookup_start]
    assert "setTrackedActivityError(null);" in zero_candidate_block
    assert "setTrackedActivityByEventId({});" in zero_candidate_block


def test_calendar_backed_loaded_event_stays_on_merged_expectation_card():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "event.event_id.startsWith('calendar:')" in source
    assert "loadedCalendarEventIds.has(calendarEventId)" in source
    assert "if (loadedCalendarEventIds.has(calendarEventId)) return true;" in source


def test_tracked_activity_lookup_failure_is_visible_retryable_and_fail_open():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "Object.fromEntries(candidateIds.map((eventId) => [eventId, 'error']))" in source
    assert "trackedActivity === 'inactive'" in source
    assert "if (isTrackedExpectation(event.event_id)) return true;" in source
    assert "trackedActivityError" in source
    assert "Seurantatilaa ei juuri nyt saatu varmistettua." in source
    assert "activityRetryToken" in source
    assert "setActivityRetryToken((value) => value + 1)" in source
    assert "!trackedActivityError" in source


def test_uncertain_past_expectations_remain_visible_until_status_is_known():
    source = HOME_SOURCE.read_text(encoding="utf-8")

    assert "if (event.scheduled_date >= today) return true;" in source
    assert "if (!status || status.statusError) return true;" in source
    assert "return status.run?.status === 'waiting_confirmation';" in source
    assert "for (const event of visibleEvents ?? [])" in source


def test_canonical_tracked_card_confirms_expectation_before_navigation():
    tracked = TRACKED_SECTION_SOURCE.read_text(encoding="utf-8")

    assert "pathname: '/events/[eventId]'" in tracked
    assert "event.kind === 'earnings'" in tracked
    assert "? `calendar:${event.calendar_event_id}`" in tracked
    assert ": `tracked:${event.event_id}`" in tracked
    assert "getEvent(expectationCandidateId)" in tracked
    assert "expectation.event_id !== expectationCandidateId" in tracked
    assert "expectationLinkState.status === 'ready'" in tracked
    assert "params: { eventId: expectationLinkState.eventId }" in tracked
    assert "Odotukset ja strategia →" in tracked


def test_expectation_lookup_failure_is_distinct_from_confirmed_absence_and_retryable():
    tracked = TRACKED_SECTION_SOURCE.read_text(encoding="utf-8")

    assert "err instanceof Error && err.message === 'Event not found'" in tracked
    assert "setExpectationLinkState({ status: 'none' });" in tracked
    assert "setExpectationLinkState({ status: 'error' });" in tracked
    assert "expectationLinkState.status === 'error'" in tracked
    assert "Odotus- ja strategiatietoa ei juuri nyt saatu varmistettua." in tracked
    assert "setExpectationRetryToken((value) => value + 1)" in tracked


def test_confirmed_missing_expectation_is_rechecked_when_home_regains_focus():
    tracked = TRACKED_SECTION_SOURCE.read_text(encoding="utf-8")

    card_start = tracked.index("export function TrackedEventCard")
    details_start = tracked.index("export function TrackedEventDetails", card_start)
    card_source = tracked[card_start:details_start]

    assert "useFocusEffect(" in card_source
    assert "useCallback(() =>" in card_source
    assert "getEvent(expectationCandidateId)" in card_source
    assert "[expectationCandidateId, expectationRetryToken]" in card_source
    assert "useEffect(() =>" not in card_source
