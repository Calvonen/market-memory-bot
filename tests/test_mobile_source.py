import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HOME_SCREEN = Path("mobile/src/app/(tabs)/index.tsx")
EVENT_DETAIL_SCREEN = Path("mobile/src/app/events/[eventId].tsx")
EVENT_EDIT_SCREEN = Path("mobile/src/app/events/[eventId]/edit.tsx")
UPCOMING_SCREEN = Path("mobile/src/app/events/upcoming.tsx")
API_SERVICE = Path("mobile/src/services/api.ts")
NATIVE_TABS = Path("mobile/src/components/app-tabs.tsx")
BACK_BUTTON = Path("mobile/src/components/back-button.tsx")
MOBILE_SRC_ROOT = Path("mobile/src")

STRATEGY_LAYOUT = Path("mobile/src/app/events/[eventId]/strategy/_layout.tsx")
STRATEGY_SUMMARY_SCREEN = Path("mobile/src/app/events/[eventId]/strategy/index.tsx")
STRATEGY_EDIT_SCREEN = Path("mobile/src/app/events/[eventId]/strategy/edit.tsx")
STRATEGY_CONFIRM_SCREEN = Path("mobile/src/app/events/[eventId]/strategy/confirm.tsx")
STRATEGY_DRAFT_FORMAT_UTIL = Path("mobile/src/utils/strategy-draft-format.ts")
RESET_ON_KEY_CHANGE_UTIL = Path("mobile/src/utils/use-reset-on-key-change.ts")


class MobileSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home_source = HOME_SCREEN.read_text(encoding="utf-8")
        cls.detail_source = EVENT_DETAIL_SCREEN.read_text(encoding="utf-8")
        cls.edit_source = EVENT_EDIT_SCREEN.read_text(encoding="utf-8")
        cls.upcoming_source = UPCOMING_SCREEN.read_text(encoding="utf-8")
        cls.api_source = API_SERVICE.read_text(encoding="utf-8")
        cls.back_button_source = BACK_BUTTON.read_text(encoding="utf-8")

    # -- regression: detail page keeps the previous Hays dashboard info ----

    def test_rejected_risk_reasons_remain_visible_with_quantity_and_reward_risk(self) -> None:
        self.assertIn("risk.reasons.join(' • ')", self.detail_source)
        self.assertIn("risk?.status === 'REJECT'", self.detail_source)
        self.assertIn("Enimmäismäärä ${risk.max_quantity", self.detail_source)
        self.assertIn("Tuotto/riski ${risk.reward_risk", self.detail_source)

    def test_waiting_confirmation_preserves_the_persisted_reason(self) -> None:
        self.assertIn(
            "run?.status === 'waiting_confirmation' ? run.message : null",
            self.detail_source,
        )
        self.assertIn("{confirmationReason}", self.detail_source)

    def test_unified_navigation_preserves_event_dashboard(self) -> None:
        tabs = NATIVE_TABS.read_text(encoding="utf-8")
        for route in ('name="index"', 'name="memory"', 'name="scanner"', 'name="trades"', 'name="settings"'):
            self.assertIn(route, tabs)
        self.assertIn("paper-status", self.api_source)

    # -- home screen: generic tracked-events list, not hardcoded to Hays ---

    def test_home_screen_uses_the_events_list_endpoint(self) -> None:
        self.assertIn("getEvents", self.home_source)
        self.assertIn("/api/v1/events", self.api_source)
        self.assertIn("export function getEvents(", self.api_source)

    def test_hays_is_no_longer_hardcoded_as_the_only_event(self) -> None:
        self.assertNotIn("hays-fy2026-results", self.home_source)
        self.assertNotIn("Hays plc", self.home_source)
        # The home screen must render whatever /api/v1/events returns.
        self.assertIn("events?.map", self.home_source)

    def test_home_screen_does_not_build_its_own_fetch_or_auth_logic(self) -> None:
        self.assertNotIn("fetch(", self.home_source)
        self.assertNotIn("X-MarketAI-Key", self.home_source)
        self.assertNotIn("EXPO_PUBLIC_MARKETAI_READ_API_KEY", self.home_source)

    def test_home_screen_links_to_upcoming_events(self) -> None:
        self.assertIn("/events/upcoming", self.home_source)

    def test_home_screen_renders_the_list_before_all_statuses_resolve(self) -> None:
        # The event list must render from setEvents(list) as soon as
        # getEvents() resolves, with each event's paper-status fetched and
        # applied independently afterwards - never gated behind a single
        # Promise.all over N per-event status requests, which would block
        # every card on the slowest (or a failing) one as the tracked list
        # grows unbounded (see the P1 fix that made list_upcoming() return
        # full history).
        self.assertNotIn("Promise.all", self.home_source)

        set_events_index = self.home_source.index("setEvents(list)")
        for_each_index = self.home_source.index("list.forEach((event) => {")
        get_status_index = self.home_source.index("getPaperStatus(event.event_id)")
        self.assertLess(set_events_index, for_each_index)
        self.assertLess(for_each_index, get_status_index)

        # Each card looks up its own status by event id and must render
        # with an explicit "still loading" state rather than nothing/undefined
        # while its own request is still in flight.
        self.assertIn("status={statuses[event.event_id]}", self.home_source)
        self.assertIn("!status\n    ? 'Ladataan...'", self.home_source)

    # -- event card navigates to the detail route with the event id --------

    def test_event_card_navigates_to_detail_with_event_id(self) -> None:
        self.assertIn("pathname: '/events/[eventId]'", self.home_source)
        self.assertIn("params: { eventId: event.event_id }", self.home_source)

    def test_home_card_marks_a_run_computed_against_an_older_expectation(self) -> None:
        # Same isStaleRun check as the detail screen, applied to the
        # compact card too - the card is what the user sees first, and it
        # must not present an outdated run's status/direction/confidence as
        # current just because the detail screen's warning hasn't been seen
        # yet.
        card_start = self.home_source.index("function EventCard(")
        card_end = self.home_source.index("\n}\n", card_start)
        card_body = self.home_source[card_start:card_end]

        self.assertIn(
            "run?.expectation_version !== undefined && run.expectation_version !== event.version",
            card_body,
        )
        self.assertIn("'Vanhentunut analyysi'", card_body)
        # Both the status text and the direction/confidence badge must be
        # suppressed/marked when stale, not just one of them.
        status_text_index = card_body.index("const statusText =")
        strategy_index = card_body.index("const strategy =")
        self.assertIn("isStale", card_body[status_text_index:strategy_index])
        self.assertIn("isStale ? null", card_body[strategy_index:])

    # -- detail screen fetches the event and its paper status --------------

    def test_detail_screen_fetches_event_and_paper_status(self) -> None:
        self.assertIn("getEvent(eventId)", self.detail_source)
        self.assertIn("getPaperStatus(eventId)", self.detail_source)
        self.assertIn("export function getEvent(", self.api_source)
        self.assertIn("export function getPaperStatus(", self.api_source)
        self.assertIn("/api/v1/events/${encodeURIComponent(eventId)}`", self.api_source)
        self.assertIn("/paper-status`", self.api_source)

    def test_detail_screen_shows_pre_release_expectation_fields(self) -> None:
        for field in (
            "event.consensus",
            "event.important_kpis",
            "event.bull_case",
            "event.base_case",
            "event.bear_case",
            "event.triggers",
            "event.invalidation_conditions",
            "event.source_name",
            "event.source_as_of",
        ):
            self.assertIn(field, self.detail_source)

    def test_detail_screen_is_not_hardcoded_to_hays(self) -> None:
        self.assertNotIn("hays-fy2026-results", self.detail_source)
        self.assertIn("useLocalSearchParams", self.detail_source)

    def test_detail_screen_links_to_the_strategy_flow(self) -> None:
        # The primary action on the detail screen opens the strategy
        # draft -> preview -> approve flow, not the raw field editor
        # directly - that stays reachable only as an advanced/debug link
        # from within the strategy summary screen.
        self.assertIn("pathname: '/events/[eventId]/strategy'", self.detail_source)

    # -- navigation: headerless events/* routes must never be dead ends ----

    def test_back_button_falls_back_to_home_when_there_is_no_history(self) -> None:
        # A deep link, a shared URL, or a web reload can open one of the
        # events/* routes with no in-app history to pop - router.back()
        # alone would be a dead end there, since the root Stack renders
        # these headerless (see app/_layout.tsx).
        self.assertIn("router.canGoBack()", self.back_button_source)
        self.assertIn("router.back();", self.back_button_source)
        self.assertIn("router.replace('/');", self.back_button_source)

    def test_events_screens_always_offer_a_way_back(self) -> None:
        # Every render path of every events/* screen - loading, error, and
        # loaded - must include the shared BackButton, not just the happy
        # path, since a deep link can land directly on any of these states.
        self.assertIn("import { BackButton }", self.detail_source)
        self.assertIn("import { BackButton }", self.edit_source)
        self.assertIn("import { BackButton }", self.upcoming_source)

        # detail and edit each have three render branches (loading, error,
        # loaded) and must show the back control in all three.
        self.assertEqual(self.detail_source.count("<BackButton"), 3)
        self.assertEqual(self.edit_source.count("<BackButton"), 3)
        # upcoming.tsx renders loading/error inline within one screen, so
        # one BackButton at the top covers every state.
        self.assertGreaterEqual(self.upcoming_source.count("<BackButton"), 1)

    def test_initial_load_failure_offers_a_retry_instead_of_a_dead_end(self) -> None:
        # When the very first getEvent() fails (no event fetched yet), the
        # screen used to render a plain static error view with no
        # RefreshControl and no way to retry - the user had to navigate
        # away and reopen the event. A retry action must be reachable
        # directly from that error state and must call load() again.
        error_only_start = self.detail_source.index("if (error && !event) {")
        error_only_end = self.detail_source.index("\n  }", error_only_start)
        error_only_block = self.detail_source[error_only_start:error_only_end]
        self.assertIn("onPress={() => void load()}", error_only_block)
        self.assertIn("Yritä uudelleen", error_only_block)

    def test_paper_status_failure_does_not_block_pre_release_expectation_render(self) -> None:
        # getEvent() and getPaperStatus() must be awaited independently: a
        # failing/unavailable paper-status lookup (e.g. before release) must
        # still let the fetched event's expectation data render, instead of
        # rejecting a combined Promise.all and leaving the screen error-only.
        self.assertNotIn("Promise.all", self.detail_source)
        set_event_index = self.detail_source.index("const eventDetail = await getEvent(eventId);")
        get_status_index = self.detail_source.index("getPaperStatus(eventId)")
        self.assertLess(set_event_index, get_status_index)
        status_try_start = self.detail_source.rindex("try {", 0, get_status_index)
        status_catch_start = self.detail_source.index("} catch (err) {", status_try_start)
        status_catch_end = self.detail_source.index("}", status_catch_start + len("} catch (err) {"))
        catch_body = self.detail_source[status_catch_start:status_catch_end]
        # The failure must be recorded separately from "not released yet"
        # (run === null with no error), not silently swallowed - so the
        # analysis section can say the status specifically failed to load.
        self.assertIn("setRun(null)", catch_body)
        self.assertIn("setStatusError(", catch_body)

    def test_detail_screen_ignores_stale_overlapping_load_responses(self) -> None:
        # If pull-to-refresh fires load() again while a previous call's
        # getPaperStatus() is still pending, the two responses can resolve
        # out of order. Every state update that follows an await must check
        # a load-id ref (the same pattern already used on the home screen)
        # first, so an older response can never overwrite a newer one -
        # e.g. silently regressing a run from paper_executed back to
        # waiting_confirmation.
        load_start = self.detail_source.index("const load = useCallback(async () => {")
        load_end = self.detail_source.index("}, [eventId]);", load_start)
        load_body = self.detail_source[load_start:load_end]

        self.assertIn("const loadId = ++latestLoadId.current;", load_body)
        # Each state update that depends on an awaited response is guarded
        # by the staleness check immediately beforehand - not just present
        # somewhere in the function, but directly gating that specific
        # setter.
        guarded_pairs = [
            ("if (loadId !== latestLoadId.current) return;\n      setEvent(eventDetail);", "setEvent after getEvent()"),
            (
                "if (loadId !== latestLoadId.current) return;\n      setError(err instanceof Error ? err.message : 'Tuntematon virhe');",
                "setError in the getEvent() catch block",
            ),
            ("if (loadId !== latestLoadId.current) return;\n      setRun(status.paper_run);", "setRun after getPaperStatus()"),
            ("if (loadId !== latestLoadId.current) return;\n      setRun(null);", "setRun in the getPaperStatus() catch block"),
        ]
        for snippet, description in guarded_pairs:
            self.assertIn(snippet, load_body, f"missing staleness guard for {description}")

    def test_detail_screen_clears_stale_event_and_run_when_event_id_changes(self) -> None:
        # If eventId changes while this screen stays mounted and the new
        # getEvent() fails, the previous event/run must not keep rendering
        # (or stay editable via a now-wrong edit link) under the new URL.
        # An ordinary pull-to-refresh of the *same* event must not trigger
        # this - it should keep showing the last known good data if the
        # refresh itself fails - so the clear must be conditional on the
        # event id actually having changed, not unconditional on every
        # load() call.
        load_start = self.detail_source.index("const load = useCallback(async () => {")
        load_end = self.detail_source.index("}, [eventId]);", load_start)
        load_body = self.detail_source[load_start:load_end]

        self.assertIn("const loadedEventIdRef = useRef<string | null>(null);", self.detail_source)
        self.assertIn("if (loadedEventIdRef.current !== eventId) {", load_body)

        guard_start = load_body.index("if (loadedEventIdRef.current !== eventId) {")
        guard_end = load_body.index("}", guard_start)
        guard_body = load_body[guard_start:guard_end]
        self.assertIn("setEvent(null);", guard_body)
        self.assertIn("setRun(null);", guard_body)
        self.assertIn("setStatusError(null);", guard_body)

        # The guard must run before loadedEventIdRef is updated to the new
        # id and before the fetch starts, otherwise the comparison is
        # meaningless.
        update_ref_index = load_body.index("loadedEventIdRef.current = eventId;")
        fetch_index = load_body.index("await getEvent(eventId)")
        self.assertLess(guard_start, update_ref_index)
        self.assertLess(update_ref_index, fetch_index)

    def test_detail_screen_shows_loading_instead_of_a_false_pre_release_state(self) -> None:
        # Between getEvent() resolving and getPaperStatus() resolving, both
        # run and statusError are still null - indistinguishable from "not
        # released yet" - so an already-released event would otherwise
        # flash the wrong status text and lose its analysis section for the
        # duration of that request. statusLoading must gate the status
        # text, default true, be reset on an actual eventId change (not a
        # same-event refresh, which should keep showing stale-but-known
        # data), and clear once the paper-status request settles either way.
        self.assertIn("useState(true);", self.detail_source[: self.detail_source.index("const load = useCallback")])
        self.assertIn(
            "statusLoading ? 'Ladataan tilaa...' : describeStatus(run, statusError)",
            self.detail_source,
        )

        load_start = self.detail_source.index("const load = useCallback(async () => {")
        load_end = self.detail_source.index("}, [eventId]);", load_start)
        load_body = self.detail_source[load_start:load_end]

        guard_start = load_body.index("if (loadedEventIdRef.current !== eventId) {")
        guard_end = load_body.index("}", guard_start)
        self.assertIn("setStatusLoading(true);", load_body[guard_start:guard_end])

        # Cleared once getPaperStatus() settles, on both the success and
        # failure paths - a finally block covers both without duplicating
        # the loadId staleness check.
        finally_start = load_body.rindex("} finally {")
        finally_end = load_body.index("}", finally_start + len("} finally {"))
        finally_body = load_body[finally_start:finally_end]
        self.assertIn("if (loadId === latestLoadId.current) setStatusLoading(false);", finally_body)

    def test_paper_status_failure_shows_an_explicit_error_in_the_analysis_section(self) -> None:
        # Requirement: pre-release expectation data (consensus/KPI/bull/
        # base/bear/triggers/invalidation/source) stays visible regardless,
        # while the analysis/paper section shows a distinct "not available"
        # message instead of silently looking like the event just hasn't
        # released yet.
        self.assertIn("statusError ? (", self.detail_source)
        analysis_error_start = self.detail_source.index("statusError ? (")
        analysis_error_end = self.detail_source.index(") : null}", analysis_error_start)
        analysis_error_block = self.detail_source[analysis_error_start:analysis_error_end]
        self.assertIn("Tila ei saatavilla", analysis_error_block)
        self.assertIn("{statusError}", analysis_error_block)

        # And that block must be the sibling "else" of the isReleased dashboard,
        # not a replacement for it - the full dashboard still wins once a real
        # paper run is present.
        is_released_index = self.detail_source.index("{isReleased ? (")
        self.assertLess(is_released_index, analysis_error_start)

    def test_detail_screen_flags_a_paper_run_computed_against_an_older_expectation(self) -> None:
        # A paper run's analysis/strategy/risk/paper-order decision is
        # computed against a specific expectation version. If the
        # expectation is edited afterwards (event.version moves on), the run
        # no longer reflects the consensus/KPIs/triggers shown above it -
        # the mismatch must be surfaced, not silently hidden.
        self.assertIn("PaperRun", self.api_source)
        self.assertIn("expectation_version?: number", self.api_source)

        self.assertIn("run.expectation_version !== event.version", self.detail_source)
        self.assertIn("isStaleRun", self.detail_source)
        self.assertIn("VANHENTUNUT ANALYYSI", self.detail_source)

        # The warning must render inside the released/isReleased branch,
        # ahead of the score grid, so it sits directly above the dashboard
        # it's warning about rather than replacing it.
        is_released_index = self.detail_source.index("{isReleased ? (")
        stale_notice_index = self.detail_source.index("isStaleRun ? (", is_released_index)
        grid_index = self.detail_source.index("styles.grid", stale_notice_index)
        self.assertLess(is_released_index, stale_notice_index)
        self.assertLess(stale_notice_index, grid_index)

    # -- settings/editor draft never bypasses admin auth with the read key -

    def test_edit_screen_never_calls_the_write_endpoint(self) -> None:
        self.assertNotIn("expectation-versions", self.edit_source)
        self.assertNotIn("X-Admin-Token", self.edit_source)
        self.assertNotIn("MARKETAI_ADMIN_API_KEY", self.edit_source)

    def test_edit_screen_initial_load_failure_offers_a_retry(self) -> None:
        # Same dead-end the detail screen used to have: a failed initial
        # getEvent() must not strand the user on a static error view with
        # no way back in short of leaving and reopening the editor.
        error_only_start = self.edit_source.index("if (error && !event) {")
        error_only_end = self.edit_source.index("\n  }", error_only_start)
        error_only_block = self.edit_source[error_only_start:error_only_end]
        self.assertIn("onPress={() => void load()}", error_only_block)
        self.assertIn("Yritä uudelleen", error_only_block)

    def test_edit_screen_ignores_stale_overlapping_load_responses(self) -> None:
        # Same overlapping-load race already fixed on the home/detail/
        # upcoming screens: an older getEvent() response resolving after a
        # newer one (e.g. eventId changing quickly, or load() re-invoked)
        # must not overwrite state with stale field values.
        load_start = self.edit_source.index("const load = useCallback(async () => {")
        load_end = self.edit_source.index("}, [eventId]);", load_start)
        load_body = self.edit_source[load_start:load_end]

        self.assertIn("const loadId = ++latestLoadId.current;", load_body)
        self.assertIn(
            "if (loadId !== latestLoadId.current) return;\n      setEvent(detail);", load_body
        )
        self.assertIn(
            "if (loadId !== latestLoadId.current) return;\n      setError(err instanceof Error ? err.message : 'Tuntematon virhe');",
            load_body,
        )

    def test_edit_screen_clears_the_previous_event_before_loading_a_new_one(self) -> None:
        # If eventId changes while this screen stays mounted and the new
        # getEvent() fails, the previous event (and its draft fields) must
        # not keep rendering as if it were current: the error+retry branch
        # only fires once `event` is null, so load() must clear it before
        # starting the new fetch - not only on success.
        load_start = self.edit_source.index("const load = useCallback(async () => {")
        load_end = self.edit_source.index("}, [eventId]);", load_start)
        load_body = self.edit_source[load_start:load_end]

        load_id_index = load_body.index("const loadId = ++latestLoadId.current;")
        clear_event_index = load_body.index("setEvent(null);")
        try_index = load_body.index("try {")
        self.assertLess(load_id_index, clear_event_index)
        self.assertLess(clear_event_index, try_index)

    def test_edit_screen_exposes_the_editable_fields(self) -> None:
        for label in (
            "Konsensusmetriikat",
            "Tärkeimmät KPI",
            "Bull-skenaario",
            "Base-skenaario",
            "Bear-skenaario",
            "Triggerit",
            "Mitätöintiehdot",
            "Lähde",
        ):
            self.assertIn(label, self.edit_source)

    # -- upcoming/calendar foundation page: real events + calendar data -----

    def test_upcoming_screen_reads_both_the_events_and_calendar_endpoints(self) -> None:
        self.assertIn("getEvents", self.upcoming_source)
        self.assertIn("getUpcomingCalendarEvents", self.upcoming_source)
        self.assertIn("export function getUpcomingCalendarEvents(", self.api_source)
        self.assertIn("/api/v1/calendar/upcoming", self.api_source)
        self.assertNotIn("fetch(", self.upcoming_source)

    def test_upcoming_screen_ignores_stale_overlapping_load_responses(self) -> None:
        # Same race as the home/detail screens: pull-to-refresh firing
        # load() again while the first requests are still pending must not
        # let an older response replace an already-refreshed list or set an
        # obsolete error over a successful refresh. Both getEvents() and
        # getUpcomingCalendarEvents() are fetched independently (own
        # .then()/.catch() chain each) so one failing/being slow never
        # blocks the other's state update.
        load_start = self.upcoming_source.index("const load = useCallback(() => {")
        load_end = self.upcoming_source.index("\n  }, []);", load_start)
        load_body = self.upcoming_source[load_start:load_end]

        # Separate generation counters (P2 regression: a track mutation
        # must only ever invalidate the calendar generation - see below) -
        # both still bumped together by load() itself, so two overlapping
        # load() calls still invalidate each other's responses on both
        # sources.
        self.assertIn("const eventsLoadId = ++latestEventsLoadId.current;", load_body)
        self.assertIn("const calendarLoadId = ++latestCalendarLoadId.current;", load_body)
        self.assertIn(
            "if (eventsLoadId !== latestEventsLoadId.current) return;\n        setEvents(list);",
            load_body,
        )
        self.assertIn(
            "if (eventsLoadId !== latestEventsLoadId.current) return;\n        setError(err instanceof Error ? err.message : 'Tuntematon virhe');",
            load_body,
        )
        self.assertIn(
            "if (calendarLoadId !== latestCalendarLoadId.current) return;\n        setCalendarEvents(list);",
            load_body,
        )

    def test_upcoming_screen_starts_both_requests_before_awaiting_either(self) -> None:
        # P2 regression: getEvents() and getUpcomingCalendarEvents() must
        # both be invoked (the actual request fired) up front, as two
        # independent statements - never one nested inside the other's
        # .then()/.catch() callback, which would make the second request
        # wait for the first to settle before it even starts.
        load_start = self.upcoming_source.index("const load = useCallback(() => {")
        load_end = self.upcoming_source.index("\n  }, []);", load_start)
        load_body = self.upcoming_source[load_start:load_end]

        events_call_index = load_body.index("const eventsPromise = getEvents()")
        calendar_call_index = load_body.index(
            "const calendarPromise = getUpcomingCalendarEvents()"
        )
        self.assertLess(events_call_index, calendar_call_index)

        # calendarPromise's declaration is a sibling statement at the same
        # indentation as eventsPromise's - not nested one level deeper
        # inside eventsPromise's .then()/.catch() body.
        self.assertIn(
            "\n    const calendarPromise = getUpcomingCalendarEvents()", load_body
        )

        # load() itself never sequences one request's own await before
        # creating the other's promise.
        self.assertNotIn("await eventsPromise", load_body)
        self.assertIn("return Promise.race([eventsPromise, calendarPromise]);", load_body)

    def test_a_never_settling_get_events_does_not_block_calendar_data_from_rendering(
        self,
    ) -> None:
        # Behavioral proof, not just structural: extract the real load()
        # body, stub its React state setters/ref and the two API calls, and
        # run it with node where getEvents() never resolves.
        # getUpcomingCalendarEvents() resolving quickly must still update
        # calendar state - proving the two requests are genuinely
        # independent, not merely declared next to each other.
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        load_start = self.upcoming_source.index("const load = useCallback(() => {")
        body_start = load_start + len("const load = useCallback(() => {")
        load_end = self.upcoming_source.index("\n  }, []);", load_start)
        load_body = self.upcoming_source[body_start:load_end]

        script = f"""
let latestEventsLoadId = {{ current: 0 }};
let latestCalendarLoadId = {{ current: 0 }};
const calls = [];
function setEvents(v) {{ calls.push(['setEvents', v]); }}
function setError(v) {{ calls.push(['setError', v]); }}
function setCalendarEvents(v) {{ calls.push(['setCalendarEvents', v]); }}

function getEvents() {{ return new Promise(() => {{}}); }}
function getUpcomingCalendarEvents() {{ return Promise.resolve(['cal-1']); }}

function load() {{
{load_body}
}}

load();
setTimeout(() => {{ console.log(JSON.stringify(calls)); }}, 50);
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(result.stdout)
        names = [c[0] for c in calls]

        # The stalled getEvents() never settles, so setEvents may never
        # fire and setError may only ever be the load()-start reset
        # (setError(null)) - never an actual error message - but the fast
        # getUpcomingCalendarEvents() must still update calendar state
        # regardless.
        self.assertNotIn("setEvents", names)
        self.assertEqual([c[1] for c in calls if c[0] == "setError"], [None])
        self.assertIn("setCalendarEvents", names)
        calendar_call = next(c for c in calls if c[0] == "setCalendarEvents")
        self.assertEqual(calendar_call[1], ["cal-1"])

    def test_a_never_settling_calendar_request_does_not_block_tracked_events(self) -> None:
        # Symmetric proof: a stalled getUpcomingCalendarEvents() must never
        # hold back rendering the already-tracked EventExpectation list.
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        load_start = self.upcoming_source.index("const load = useCallback(() => {")
        body_start = load_start + len("const load = useCallback(() => {")
        load_end = self.upcoming_source.index("\n  }, []);", load_start)
        load_body = self.upcoming_source[body_start:load_end]

        script = f"""
let latestEventsLoadId = {{ current: 0 }};
let latestCalendarLoadId = {{ current: 0 }};
const calls = [];
function setEvents(v) {{ calls.push(['setEvents', v]); }}
function setError(v) {{ calls.push(['setError', v]); }}
function setCalendarEvents(v) {{ calls.push(['setCalendarEvents', v]); }}

function getEvents() {{ return Promise.resolve(['event-1']); }}
function getUpcomingCalendarEvents() {{ return new Promise(() => {{}}); }}

function load() {{
{load_body}
}}

load();
setTimeout(() => {{ console.log(JSON.stringify(calls)); }}, 50);
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(result.stdout)
        names = [c[0] for c in calls]

        self.assertNotIn("setCalendarEvents", names)
        self.assertIn("setEvents", names)
        events_call = next(c for c in calls if c[0] == "setEvents")
        self.assertEqual(events_call[1], ["event-1"])

    # -- pull-to-refresh (P2 regression): must never hang on one source ----

    def test_load_races_rather_than_waits_for_both_sources_to_settle(self) -> None:
        # Structural proof: load() itself must resolve as soon as the FIRST
        # of the two requests settles (Promise.race), not once both do
        # (Promise.all) - otherwise onRefresh() below, which awaits load(),
        # would hang forever if either source never settles, even though
        # the other one already has data to show.
        load_start = self.upcoming_source.index("const load = useCallback(() => {")
        load_end = self.upcoming_source.index("\n  }, []);", load_start)
        load_body = self.upcoming_source[load_start:load_end]

        self.assertIn("return Promise.race([eventsPromise, calendarPromise]);", load_body)
        self.assertNotIn("Promise.all(", load_body)

        on_refresh_start = self.upcoming_source.index("const onRefresh = useCallback(async () => {")
        on_refresh_end = self.upcoming_source.index("}, [load]);", on_refresh_start)
        on_refresh_body = self.upcoming_source[on_refresh_start:on_refresh_end]
        self.assertIn("await load();", on_refresh_body)
        self.assertIn("setRefreshing(false);", on_refresh_body)

    def test_on_refresh_does_not_hang_when_one_source_never_settles(self) -> None:
        # Behavioral proof: run the real load() body under node with
        # getEvents() resolving quickly and getUpcomingCalendarEvents()
        # stubbed to never settle (the reverse case is symmetric). The
        # promise load() itself returns must still win a race against a
        # 200ms timeout - if it were gated on both sources (Promise.all),
        # it would never resolve at all and the timeout would always win,
        # leaving onRefresh()'s `refreshing` state stuck true forever.
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        load_start = self.upcoming_source.index("const load = useCallback(() => {")
        body_start = load_start + len("const load = useCallback(() => {")
        load_end = self.upcoming_source.index("\n  }, []);", load_start)
        load_body = self.upcoming_source[body_start:load_end]

        script = f"""
let latestEventsLoadId = {{ current: 0 }};
let latestCalendarLoadId = {{ current: 0 }};
function setEvents() {{}}
function setError() {{}}
function setCalendarEvents() {{}}

function getEvents() {{ return Promise.resolve(['event-1']); }}
function getUpcomingCalendarEvents() {{ return new Promise(() => {{}}); }}

function load() {{
{load_body}
}}

Promise.race([
  load().then(() => 'load-settled'),
  new Promise((resolve) => setTimeout(() => resolve('timeout'), 200)),
]).then((winner) => {{ console.log(JSON.stringify({{ winner }})); }});
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        outcome = json.loads(result.stdout)
        self.assertEqual(outcome["winner"], "load-settled")

    def test_upcoming_screen_has_no_mocked_calendar_data(self) -> None:
        for forbidden in ("mock", "Mock", "MOCK", "fakeEvents", "dummy", "sampleEvents"):
            self.assertNotIn(forbidden, self.upcoming_source)

    def test_upcoming_screen_does_not_guess_unknown_exchange_suffixes_as_usa(self) -> None:
        # Only the no-suffix case (the actual USA convention on this
        # backend, e.g. "AAPL") may resolve to 'USA'. An unrecognized
        # suffix such as ".PA" must not silently fall through to 'USA' too.
        function_start = self.upcoming_source.index("function marketForInstrument(")
        function_end = self.upcoming_source.index("\n}\n", function_start)
        function_body = self.upcoming_source[function_start:function_end]

        no_suffix_guard = function_body.index("if (!instrument.includes('.')) {")
        no_suffix_return = function_body.index("return 'USA';", no_suffix_guard)
        switch_start = function_body.index("switch (")
        self.assertLess(no_suffix_return, switch_start)

        default_case = function_body.index("default:")
        default_body = function_body[default_case:]
        self.assertNotIn("'USA'", default_body)

    def test_market_for_instrument_actually_classifies_unknown_european_suffixes(self) -> None:
        # Behavioral proof, not just a structural string check: extract the
        # real marketForInstrument() body from upcoming.tsx, strip its (tiny,
        # type-annotation-only) TypeScript syntax, and execute it with node
        # for a handful of unmapped European suffixes (.PA Paris, .AS
        # Amsterdam, .SW Swiss) plus the mapped/no-suffix baselines.
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        function_start = self.upcoming_source.index("function marketForInstrument(")
        function_end = self.upcoming_source.index("\n}\n", function_start) + len("\n}")
        function_source = self.upcoming_source[function_start:function_end]

        # Strip the parameter/return type annotations - the only
        # TypeScript-specific syntax in this otherwise-plain-JS function.
        js_function = re.sub(r":\s*string\b", "", function_source, count=2)
        self.assertNotIn(": string", js_function)

        cases = ["BNPP.PA", "ASML.AS", "NOVN.SW", "AAPL", "HAS.L"]
        script = (
            js_function
            + "\nconsole.log(JSON.stringify("
            + json.dumps(cases)
            + ".map(marketForInstrument)));\n"
        )

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = dict(zip(cases, json.loads(result.stdout)))

        for suffix_case in ("BNPP.PA", "ASML.AS", "NOVN.SW"):
            self.assertNotEqual(outputs[suffix_case], "USA", outputs)

        self.assertEqual(outputs["AAPL"], "USA")
        self.assertEqual(outputs["HAS.L"], "Iso-Britannia")

    def _extract_upcoming_function(self, signature: str, *, strip_types: bool = True) -> str:
        start = self.upcoming_source.index(signature)
        end = self.upcoming_source.index("\n}\n", start) + len("\n}")
        source = self.upcoming_source[start:end]
        if strip_types:
            source = re.sub(r":\s*(EventExpectation|CalendarEvent|UpcomingRow)\[\]", "", source)
            source = re.sub(r":\s*string\b", "", source)
        return source

    def _run_merge_upcoming_rows(self, events_js: str, calendar_js: str) -> list[dict]:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        market_fn_js = self._extract_upcoming_function("function marketForInstrument(")
        merge_fn_js = self._extract_upcoming_function("export function mergeUpcomingRows(").replace(
            "export function ", "function "
        )
        for annotation in ("EventExpectation[]", "CalendarEvent[]", "UpcomingRow[]"):
            self.assertNotIn(annotation, merge_fn_js)

        script = f"""
{market_fn_js}
{merge_fn_js}

const events = {events_js};
const calendarEvents = {calendar_js};

const rows = mergeUpcomingRows(events, calendarEvents);
console.log(JSON.stringify(rows.map((r) => ({{
  instrument: r.instrument, scheduledDate: r.scheduledDate, kind: r.kind, status: r.status,
}}))));
"""

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_dedupe_is_occurrence_specific_a_historical_expectation_never_hides_a_future_candidate(
        self,
    ) -> None:
        # P1 regression: /api/v1/events intentionally keeps historical
        # expectations forever. A past AAPL Q2 expectation must never hide
        # AAPL's next, genuinely different, calendar candidate (Q3/Q4) -
        # only a calendar row matching the SAME instrument+date is deduped.
        rows = self._run_merge_upcoming_rows(
            "[{ event_id: 'aapl-q2', instrument: 'AAPL', event_name: 'Apple Q2 2026', "
            "scheduled_date: '2026-04-30' }]",
            "[{ calendar_event_id: 'cal-1', company_name: 'Apple Inc', instrument: 'AAPL', "
            "market: 'NASDAQ', event_type: 'earnings', scheduled_date: '2026-10-29', "
            "source: 'finnhub', status: 'candidate', created_at: '', updated_at: '' }]",
        )

        aapl_rows = [r for r in rows if r["instrument"] == "AAPL"]
        self.assertEqual(len(aapl_rows), 2)
        future_row = next(r for r in aapl_rows if r["scheduledDate"] == "2026-10-29")
        self.assertEqual(future_row["kind"], "calendar")
        self.assertEqual(future_row["status"], "candidate")
        past_row = next(r for r in aapl_rows if r["scheduledDate"] == "2026-04-30")
        self.assertEqual(past_row["kind"], "expectation")

    def test_dedupe_collapses_only_the_matching_same_ticker_same_date_occurrence(self) -> None:
        # A tracked calendar occurrence must never duplicate alongside its
        # own already-tracked expectation row (same instrument + same date).
        rows = self._run_merge_upcoming_rows(
            "[{ event_id: 'hays-fy2026-results', instrument: 'HAS.L', "
            "event_name: 'Hays plc FY2026 results', scheduled_date: '2026-08-20' }]",
            "[{ calendar_event_id: 'cal-2', company_name: 'Hays plc', instrument: 'has.l', "
            "market: 'Iso-Britannia', event_type: 'earnings', scheduled_date: '2026-08-20', "
            "source: 'finnhub', status: 'tracked', created_at: '', updated_at: '' }]",
        )

        has_rows = [r for r in rows if r["instrument"] == "HAS.L"]
        self.assertEqual(len(has_rows), 1)
        self.assertEqual(has_rows[0]["kind"], "expectation")
        self.assertEqual(has_rows[0]["status"], "tracked")

    def test_two_distinct_future_occurrences_of_the_same_ticker_both_survive(self) -> None:
        # No expectation at all for this ticker yet - both a Q3 and a Q4
        # calendar candidate for the same company must be kept, not
        # collapsed into one another.
        rows = self._run_merge_upcoming_rows(
            "[]",
            "[{ calendar_event_id: 'cal-3', company_name: 'Apple Inc', instrument: 'AAPL', "
            "market: 'NASDAQ', event_type: 'earnings', scheduled_date: '2026-07-30', "
            "source: 'finnhub', status: 'candidate', created_at: '', updated_at: '' }, "
            "{ calendar_event_id: 'cal-4', company_name: 'Apple Inc', instrument: 'AAPL', "
            "market: 'NASDAQ', event_type: 'earnings', scheduled_date: '2026-10-29', "
            "source: 'finnhub', status: 'candidate', created_at: '', updated_at: '' }]",
        )

        aapl_rows = [r for r in rows if r["instrument"] == "AAPL"]
        self.assertEqual(len(aapl_rows), 2)
        self.assertEqual(
            {r["scheduledDate"] for r in aapl_rows}, {"2026-07-30", "2026-10-29"}
        )

    def test_non_earnings_calendar_candidate_is_never_deduped_against_an_expectation(
        self,
    ) -> None:
        # A manual non-earnings candidate (e.g. a production report) for the
        # same instrument+date as an expectation is a different occurrence
        # and must survive - EventExpectation only ever represents earnings.
        rows = self._run_merge_upcoming_rows(
            "[{ event_id: 'afagr-earnings', instrument: 'AFAGR.HE', "
            "event_name: 'Afarak earnings', scheduled_date: '2026-10-15' }]",
            "[{ calendar_event_id: 'cal-5', company_name: 'Afarak Group', "
            "instrument: 'AFAGR.HE', market: 'Suomi', event_type: 'production_report', "
            "scheduled_date: '2026-10-15', source: 'manual', status: 'candidate', "
            "created_at: '', updated_at: '' }]",
        )

        afagr_rows = [r for r in rows if r["instrument"] == "AFAGR.HE"]
        self.assertEqual(len(afagr_rows), 2)
        self.assertEqual({r["kind"] for r in afagr_rows}, {"expectation", "calendar"})

    # -- deterministic merged order (P2 regression): upcoming before history

    def _run_merge_upcoming_rows_dynamic(self, body_js: str) -> list[dict]:
        # Same technique as _run_merge_upcoming_rows(), but the caller
        # builds `events`/`calendarEvents` itself using a `daysFromNow(n)`
        # helper (matching the date-range-filter test below), so dates are
        # always relative to node's own `new Date()` at run time - required
        # here because mergeUpcomingRows()'s sort itself compares against
        # "today".
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        market_fn_js = self._extract_upcoming_function("function marketForInstrument(")
        merge_fn_js = self._extract_upcoming_function("export function mergeUpcomingRows(").replace(
            "export function ", "function "
        )

        script = f"""
{market_fn_js}
{merge_fn_js}

function fmt(d) {{
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${{y}}-${{m}}-${{day}}`;
}}
function daysFromNow(n) {{
  const d = new Date();
  d.setDate(d.getDate() + n);
  return fmt(d);
}}

{body_js}

const rows = mergeUpcomingRows(events, calendarEvents);
console.log(JSON.stringify(rows.map((r) => ({{ key: r.key, instrument: r.instrument, scheduledDate: r.scheduledDate, kind: r.kind }}))));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_sort_places_a_future_candidate_before_historical_expectations(self) -> None:
        # A historical same-ticker (or any) expectation must never bury a
        # future calendar candidate below it - concatenation order
        # ("expectations then calendar rows") used to do exactly that.
        rows = self._run_merge_upcoming_rows_dynamic(
            """
const events = [
  { event_id: 'hist-1', instrument: 'AAPL', event_name: 'Old Apple release', scheduled_date: daysFromNow(-400) },
];
const calendarEvents = [
  { calendar_event_id: 'cal-1', company_name: 'Msft Corp', instrument: 'MSFT', market: 'NASDAQ', event_type: 'earnings', scheduled_date: daysFromNow(10), source: 'finnhub', status: 'candidate', created_at: '', updated_at: '' },
];
"""
        )

        self.assertEqual([r["key"] for r in rows], ["calendar:cal-1", "expectation:hist-1"])

    def test_sort_orders_multiple_future_events_chronologically(self) -> None:
        # Soonest first, regardless of whether a row originated from the
        # expectation list or the calendar candidates.
        rows = self._run_merge_upcoming_rows_dynamic(
            """
const events = [
  { event_id: 'exp-far', instrument: 'HAS.L', event_name: 'Hays far', scheduled_date: daysFromNow(60) },
];
const calendarEvents = [
  { calendar_event_id: 'cal-near', company_name: 'Near Co', instrument: 'NEAR', market: 'NASDAQ', event_type: 'earnings', scheduled_date: daysFromNow(3), source: 'finnhub', status: 'candidate', created_at: '', updated_at: '' },
  { calendar_event_id: 'cal-mid', company_name: 'Mid Co', instrument: 'MID', market: 'NASDAQ', event_type: 'earnings', scheduled_date: daysFromNow(20), source: 'finnhub', status: 'candidate', created_at: '', updated_at: '' },
];
"""
        )

        self.assertEqual(
            [r["key"] for r in rows], ["calendar:cal-near", "calendar:cal-mid", "expectation:exp-far"]
        )

    def test_sort_places_past_events_after_future_events_most_recent_first(self) -> None:
        rows = self._run_merge_upcoming_rows_dynamic(
            """
const events = [
  { event_id: 'exp-old', instrument: 'HAS.L', event_name: 'Hays old', scheduled_date: daysFromNow(-100) },
  { event_id: 'exp-recent-past', instrument: 'AAPL', event_name: 'Apple recent past', scheduled_date: daysFromNow(-5) },
];
const calendarEvents = [
  { calendar_event_id: 'cal-future-1', company_name: 'Future One', instrument: 'FUT1', market: 'NASDAQ', event_type: 'earnings', scheduled_date: daysFromNow(2), source: 'finnhub', status: 'candidate', created_at: '', updated_at: '' },
  { calendar_event_id: 'cal-future-2', company_name: 'Future Two', instrument: 'FUT2', market: 'NASDAQ', event_type: 'earnings', scheduled_date: daysFromNow(15), source: 'finnhub', status: 'candidate', created_at: '', updated_at: '' },
];
"""
        )

        keys = [r["key"] for r in rows]
        # Both future rows come first (soonest first), then both past rows,
        # most recently released first - never interleaved, and never
        # decided by candidate/tracked/expectation origin.
        self.assertEqual(
            keys,
            [
                "calendar:cal-future-1",
                "calendar:cal-future-2",
                "expectation:exp-recent-past",
                "expectation:exp-old",
            ],
        )

    def test_date_range_filter_excludes_past_events_but_kaikki_keeps_history(self) -> None:
        # Behavioral proof for the date-range filter: extract the real
        # filtered = useMemo(...) predicate from upcoming.tsx, strip TS
        # syntax, and run it with node against synthetic past/near/far rows.
        # A day-range filter (e.g. "7 pv") must exclude a released/past-dated
        # row even though calendar/tracked rows can be older than today;
        # "Kaikki" (no range selected) must keep showing it.
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        filter_start = self.upcoming_source.index("const filtered = useMemo(() => {")
        filter_body_start = filter_start + len("const filtered = useMemo(() => {")
        filter_end = self.upcoming_source.index(
            "}, [rows, market, search, rangeDays]);", filter_start
        )
        filter_body = self.upcoming_source[filter_body_start:filter_end]
        filter_fn_js = (
            "function filterRows(rows, market, search, rangeDays) {" + filter_body + "}"
        )
        self.assertNotIn(": string", filter_fn_js)

        script = f"""
{filter_fn_js}

function fmt(d) {{
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${{y}}-${{m}}-${{day}}`;
}}
function daysFromNow(n) {{
  const d = new Date();
  d.setDate(d.getDate() + n);
  return fmt(d);
}}

function row(key, days) {{
  return {{ key, companyName: 'X', instrument: 'HAS.L', market: 'Iso-Britannia', scheduledDate: daysFromNow(days) }};
}}

const rows = [row('past', -10), row('today', 0), row('near', 3), row('far', 60)];

const sevenDayIds = filterRows(rows, 'Kaikki', '', 7).map((r) => r.key);
const allIds = filterRows(rows, 'Kaikki', '', null).map((r) => r.key);
console.log(JSON.stringify({{ sevenDayIds, allIds }}));
"""

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = json.loads(result.stdout)

        # "7 pv" must exclude the past row and the far (60 days out) row,
        # but keep today's and the near (3 days out) row.
        self.assertNotIn("past", outputs["sevenDayIds"])
        self.assertNotIn("far", outputs["sevenDayIds"])
        self.assertIn("today", outputs["sevenDayIds"])
        self.assertIn("near", outputs["sevenDayIds"])

        # "Kaikki" (no range) must still surface the full tracked history,
        # including the already-released past row.
        self.assertEqual(set(outputs["allIds"]), {"past", "today", "near", "far"})

    def test_upcoming_screen_has_filter_and_tracking_ui(self) -> None:
        self.assertIn("Hae tickerillä", self.upcoming_source)
        self.assertIn("MARKKINA", self.upcoming_source)
        self.assertIn("JULKAISUPÄIVÄ", self.upcoming_source)
        self.assertIn("Seurannassa", self.upcoming_source)
        self.assertIn("Lisää seurantaan", self.upcoming_source)

    # -- calendar candidate -> tracked action --------------------------------

    def test_track_action_calls_track_calendar_event_and_updates_local_state(self) -> None:
        self.assertIn("trackCalendarEvent", self.upcoming_source)
        self.assertIn("export function trackCalendarEvent(", self.api_source)

        on_track_start = self.upcoming_source.index("const onTrack = useCallback(async (calendarEventId")
        on_track_end = self.upcoming_source.index("}, []);", on_track_start)
        on_track_body = self.upcoming_source[on_track_start:on_track_end]

        self.assertIn("await trackCalendarEvent(calendarEventId)", on_track_body)
        # The returned (server-confirmed) event replaces the matching local
        # entry - never a locally-guessed status flip - so the card's
        # tracked/candidate state always reflects what the backend actually
        # persisted.
        self.assertIn("event.calendar_event_id === calendarEventId ? updated : event", on_track_body)

        finally_start = on_track_body.index("} finally {")
        finally_body = on_track_body[finally_start:]
        self.assertIn("next.delete(calendarEventId);", finally_body)

    def test_track_action_never_calls_untrack_or_uses_admin_auth(self) -> None:
        self.assertNotIn("untrackCalendarEvent", self.upcoming_source)
        self.assertNotIn("X-Admin-Token", self.upcoming_source)
        self.assertNotIn("MARKETAI_ADMIN_API_KEY", self.upcoming_source)

    def test_track_button_is_disabled_while_its_own_request_is_in_flight(self) -> None:
        self.assertIn("disabled={trackingIds.has(row.calendarEventId ?? '')}", self.upcoming_source)

    # -- track mutation must beat a stale in-flight refresh (P2 regression) -

    def test_track_success_bumps_the_load_generation_after_updating_state(self) -> None:
        on_track_start = self.upcoming_source.index("const onTrack = useCallback(async (calendarEventId")
        on_track_end = self.upcoming_source.index("}, []);", on_track_start)
        on_track_body = self.upcoming_source[on_track_start:on_track_end]

        set_events_index = on_track_body.index(
            "event.calendar_event_id === calendarEventId ? updated : event"
        )
        bump_index = on_track_body.index("++latestCalendarLoadId.current;")
        catch_index = on_track_body.index("} catch (err) {")
        # The bump must happen in the success path, after the local state
        # is updated - not in catch/finally, and not before the update.
        self.assertLess(set_events_index, bump_index)
        self.assertLess(bump_index, catch_index)
        # And it must only ever be the calendar generation - never the
        # unrelated expectation-list generation (P2 regression: a track
        # mutation must not invalidate an independent in-flight
        # getEvents() response).
        self.assertNotIn("latestEventsLoadId.current", on_track_body)

    def test_track_success_wins_over_a_stale_in_flight_refresh_response(self) -> None:
        # Behavioral proof: a pull-to-refresh GET is already in flight when
        # the user successfully tracks a candidate. The GET only resolves
        # *after* the track completes, carrying a pre-track snapshot (the
        # row still 'candidate') - that stale response must never revert
        # the just-tracked row back to 'candidate'.
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        load_start = self.upcoming_source.index("const load = useCallback(() => {")
        load_body_start = load_start + len("const load = useCallback(() => {")
        load_end = self.upcoming_source.index("\n  }, []);", load_start)
        load_body = self.upcoming_source[load_body_start:load_end]

        on_track_start = self.upcoming_source.index(
            "const onTrack = useCallback(async (calendarEventId: string) => {"
        )
        on_track_body_start = on_track_start + len(
            "const onTrack = useCallback(async (calendarEventId: string) => {"
        )
        on_track_end = self.upcoming_source.index("\n  }, []);", on_track_start)
        on_track_body = self.upcoming_source[on_track_body_start:on_track_end]

        script = f"""
let latestEventsLoadId = {{ current: 0 }};
let latestCalendarLoadId = {{ current: 0 }};
let calendarEventsState = [
  {{ calendar_event_id: 'cal-1', instrument: 'AAPL', status: 'candidate' }},
];
function setCalendarEvents(updater) {{
  calendarEventsState = typeof updater === 'function' ? updater(calendarEventsState) : updater;
}}
function setEvents() {{}}
function setError() {{}}
function setTrackError() {{}}
function setTrackingIds() {{}}

let resolveStaleGet;
const staleGetPromise = new Promise((resolve) => {{ resolveStaleGet = resolve; }});

function getEvents() {{ return Promise.resolve([]); }}
function getUpcomingCalendarEvents() {{ return staleGetPromise; }}
function trackCalendarEvent(id) {{
  return Promise.resolve({{ calendar_event_id: id, instrument: 'AAPL', status: 'tracked' }});
}}

function load() {{
{load_body}
}}

async function onTrack(calendarEventId) {{
{on_track_body}
}}

async function main() {{
  // 1) A pull-to-refresh GET starts and captures the current loadId - it
  // deliberately never settles until step 3 below.
  load();

  // 2) The user successfully tracks the candidate while that GET is still
  // pending.
  await onTrack('cal-1');

  // 3) The stale GET now resolves, with data reflecting the state from
  // *before* the track (still 'candidate').
  resolveStaleGet([{{ calendar_event_id: 'cal-1', instrument: 'AAPL', status: 'candidate' }}]);
  await new Promise((resolve) => setTimeout(resolve, 20));

  console.log(JSON.stringify(calendarEventsState));
}}

main();
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        final_state = json.loads(result.stdout)
        cal1 = next(e for e in final_state if e["calendar_event_id"] == "cal-1")
        self.assertEqual(cal1["status"], "tracked")

    def test_track_never_invalidates_an_independent_pending_events_request(self) -> None:
        # Symmetric P2 regression: the calendar GET settles first, the
        # events GET is still pending when the user tracks a candidate -
        # the track's generation bump must only ever affect the calendar
        # side. When the events GET finally resolves, it must still apply
        # normally: `events` must never end up stuck null just because an
        # unrelated calendar mutation happened while it was in flight.
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        load_start = self.upcoming_source.index("const load = useCallback(() => {")
        load_body_start = load_start + len("const load = useCallback(() => {")
        load_end = self.upcoming_source.index("\n  }, []);", load_start)
        load_body = self.upcoming_source[load_body_start:load_end]

        on_track_start = self.upcoming_source.index(
            "const onTrack = useCallback(async (calendarEventId: string) => {"
        )
        on_track_body_start = on_track_start + len(
            "const onTrack = useCallback(async (calendarEventId: string) => {"
        )
        on_track_end = self.upcoming_source.index("\n  }, []);", on_track_start)
        on_track_body = self.upcoming_source[on_track_body_start:on_track_end]

        script = f"""
let latestEventsLoadId = {{ current: 0 }};
let latestCalendarLoadId = {{ current: 0 }};
let eventsState = null;
function setEvents(v) {{ eventsState = v; }}
function setError() {{}}
function setCalendarEvents() {{}}
function setTrackError() {{}}
function setTrackingIds() {{}}

let resolvePendingEvents;
const pendingEventsPromise = new Promise((resolve) => {{ resolvePendingEvents = resolve; }});

// 1) The calendar GET settles immediately.
function getEvents() {{ return pendingEventsPromise; }}
function getUpcomingCalendarEvents() {{
  return Promise.resolve([
    {{ calendar_event_id: 'cal-1', instrument: 'AAPL', status: 'candidate' }},
  ]);
}}
function trackCalendarEvent(id) {{
  return Promise.resolve({{ calendar_event_id: id, instrument: 'AAPL', status: 'tracked' }});
}}

function load() {{
{load_body}
}}

async function onTrack(calendarEventId) {{
{on_track_body}
}}

async function main() {{
  // 1) Refresh starts - calendar GET settles right away, events GET is
  // deliberately left pending.
  load();
  await new Promise((resolve) => setTimeout(resolve, 10));

  // 2) The user tracks a candidate while the events GET is still pending.
  await onTrack('cal-1');

  // 3) The events GET finally resolves.
  resolvePendingEvents([
    {{ event_id: 'hays-fy2026-results', instrument: 'HAS.L', event_name: 'Hays plc FY2026 results' }},
  ]);
  await new Promise((resolve) => setTimeout(resolve, 20));

  console.log(JSON.stringify(eventsState));
}}

main();
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        events_state = json.loads(result.stdout)
        self.assertIsNotNone(events_state)
        self.assertEqual(len(events_state), 1)
        self.assertEqual(events_state[0]["event_id"], "hays-fy2026-results")

    # -- security boundary: admin key never present in the mobile app -------

    def test_admin_api_key_is_absent_from_the_mobile_source_tree(self) -> None:
        offending: list[str] = []
        for path in MOBILE_SRC_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".json"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "MARKETAI_ADMIN_API_KEY" in text or "X-Admin-Token" in text:
                offending.append(str(path))
        self.assertEqual(offending, [])

    def test_no_expo_public_env_var_carries_the_admin_key_name(self) -> None:
        for path in MOBILE_SRC_ROOT.rglob("*.ts*"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("EXPO_PUBLIC_MARKETAI_ADMIN", text)

    # -- market memory / scanner regressions (unchanged after the move) -----

    def test_market_memory_screen_has_mobile_states(self) -> None:
        source = Path("mobile/src/app/(tabs)/memory.tsx").read_text(encoding="utf-8")
        for text in ("Ticker", "Analysoi", "ActivityIndicator", "Tärkeimmät analogiat"):
            self.assertIn(text, source)

    def test_new_market_memory_request_hides_previous_result(self) -> None:
        source = Path("mobile/src/app/(tabs)/memory.tsx").read_text(encoding="utf-8")
        request_start = source.index("async function analyze(")
        clear_data = source.index("setData(null)", request_start)
        start_loading = source.index("setLoading(true)", request_start)
        request = source.index("await apiGet", request_start)

        # A successful A result is removed before B enters its loading state.
        self.assertLess(clear_data, start_loading)
        self.assertLess(start_loading, request)
        # A cannot render while B is loading or alongside B's error; a
        # successful B still renders after both transient states clear.
        self.assertIn("!loading && !error && data &&", source)

    def test_ticker_edit_clears_stale_suggestions_and_invalidates_in_flight_request(self) -> None:
        source = Path("mobile/src/app/(tabs)/memory.tsx").read_text(encoding="utf-8")
        handler_start = source.index("onChangeText={(value) => {")
        handler_end = source.index("}}", handler_start)
        handler_body = source[handler_start:handler_end]

        # Old suggestions must disappear the moment the field changes, not
        # only once the next debounced fetch resolves.
        self.assertIn("setSuggestions([])", handler_body)
        # Any suggestion request already in flight must be invalidated here
        # so a late response for the old query cannot repopulate the list.
        self.assertIn("latestSuggestionRequestId.current", handler_body)

        # The suggestion effect itself must still guard against races: it
        # should only apply a response that matches the current request id.
        effect_start = source.index("useEffect(", source.index("showSuggestions"))
        effect_end = source.index("}, [showSuggestions, query]);", effect_start)
        effect_body = source[effect_start:effect_end]
        self.assertIn("requestId === latestSuggestionRequestId.current", effect_body)

    def test_selecting_a_suggestion_suppresses_further_suggestions(self) -> None:
        source = Path("mobile/src/app/(tabs)/memory.tsx").read_text(encoding="utf-8")

        analyze_start = source.index("async function analyze(")
        analyze_end = source.index("\n  return (", analyze_start)
        analyze_body = source[analyze_start:analyze_end]

        # Choosing a suggestion must immediately end suggestion mode: stop
        # treating the field as "being edited" so the effect does not
        # restart a search for the ticker just committed to.
        if_start = analyze_body.index("if (selectedTicker) {")
        if_end = analyze_body.index("\n    }", if_start)
        selected_ticker_block = analyze_body[if_start:if_end]
        self.assertIn("setHasEditedTicker(false)", selected_ticker_block)

        # And whatever suggestion state/request exists must be dropped too,
        # whether a suggestion was chosen or the ticker was submitted directly.
        self.assertIn("setSuggestions([])", analyze_body)
        self.assertIn("latestSuggestionRequestId.current", analyze_body)

        # Suggestions must never render while an analysis request is loading,
        # regardless of how the field state got there.
        show_suggestions_start = source.index("const showSuggestions =")
        show_suggestions_end = source.index(";", show_suggestions_start)
        self.assertIn("!loading", source[show_suggestions_start:show_suggestions_end])

        # Direct typing must still turn suggestion mode back on normally.
        handler_start = source.index("onChangeText={(value) => {")
        handler_end = source.index("}}", handler_start)
        self.assertIn("setHasEditedTicker(true)", source[handler_start:handler_end])

    def test_only_selected_suggestion_ends_suggestion_mode(self) -> None:
        source = Path("mobile/src/app/(tabs)/memory.tsx").read_text(encoding="utf-8")

        analyze_start = source.index("async function analyze(")
        analyze_end = source.index("\n  return (", analyze_start)
        analyze_body = source[analyze_start:analyze_end]

        if_start = analyze_body.index("if (selectedTicker) {")
        if_end = analyze_body.index("\n    }", if_start)
        selected_ticker_block = analyze_body[if_start:if_end]
        outside_selected_ticker_block = analyze_body[:if_start] + analyze_body[if_end:]

        # Suggestion mode is only ended when a real suggestion ticker was
        # chosen (selectedTicker truthy) ...
        self.assertIn("setHasEditedTicker(false)", selected_ticker_block)
        # ... never as a side effect of a direct submit, a failed request, or
        # loading finishing - a company-name/ticker guess typed by hand must
        # not permanently disable suggestions.
        self.assertNotIn("setHasEditedTicker(false)", outside_selected_ticker_block)

    def test_failed_direct_submit_keeps_suggestion_mode_available(self) -> None:
        source = Path("mobile/src/app/(tabs)/memory.tsx").read_text(encoding="utf-8")

        analyze_start = source.index("async function analyze(")
        analyze_end = source.index("\n  return (", analyze_start)
        analyze_body = source[analyze_start:analyze_end]

        catch_start = analyze_body.index("} catch (requestError) {")
        finally_start = analyze_body.index("} finally {")
        catch_block = analyze_body[catch_start:finally_start]
        finally_block = analyze_body[finally_start:]

        # A failed request - e.g. a company name typed and submitted
        # directly that the backend rejects as an unknown ticker - must not
        # touch hasEditedTicker. data stays null and loading clears, so
        # showSuggestions naturally re-evaluates to true for the still
        # unmatched query and the debounced effect can search it again.
        self.assertNotIn("setHasEditedTicker", catch_block)
        self.assertNotIn("setHasEditedTicker", finally_block)
        self.assertNotIn("setData(", catch_block)

        show_suggestions_start = source.index("const showSuggestions =")
        show_suggestions_end = source.index(";", show_suggestions_start)
        show_suggestions_expr = source[show_suggestions_start:show_suggestions_end]
        self.assertIn("hasEditedTicker", show_suggestions_expr)
        self.assertIn("!loading", show_suggestions_expr)
        self.assertIn("data?.ticker !== query.toUpperCase()", show_suggestions_expr)

    def test_market_selection_invalidates_active_scan_before_new_load(self) -> None:
        source = Path("mobile/src/app/(tabs)/scanner.tsx").read_text(encoding="utf-8")

        invalidate_start = source.index("function invalidateScan() {")
        invalidate_end = source.index("\n  }", invalidate_start)
        invalidate_body = source[invalidate_start:invalidate_end]

        # 1) The previous market's data must be dropped synchronously here -
        # never deferred to an effect or timer - so it can never render
        # under the new "Valittu" label once the selection changes.
        self.assertNotIn("useEffect", invalidate_body)
        self.assertNotIn("setTimeout", invalidate_body)
        self.assertIn("setData(null)", invalidate_body)
        self.assertIn("setLoading(true)", invalidate_body)

        # 2) The active request must be invalidated in the very same place,
        # so a late response from before the selection change can never
        # update state again - even if the new loadScanner() has not
        # started yet.
        self.assertIn("latestRequestId.current", invalidate_body)

        # invalidateScan is defined ahead of, and is independent of, the
        # debounced load effect that eventually starts the new request.
        debounce_effect_start = source.index("setTimeout(() => {\n      void loadScanner();")
        self.assertLess(invalidate_start, debounce_effect_start)

        # Both the country and the scope selector must call it synchronously,
        # right before changing the underlying state - not after, and not
        # through a separate deferred hook.
        self.assertIn("invalidateScan();\n              setCountry(item.value);", source)
        self.assertIn("invalidateScan();\n              setScope(item);", source)

    def test_reselecting_the_same_country_is_a_no_op(self) -> None:
        source = Path("mobile/src/app/(tabs)/scanner.tsx").read_text(encoding="utf-8")

        handler_start = source.index("onPress={() => {", source.index("COUNTRIES.map"))
        handler_end = source.index("}}", handler_start)
        handler_body = source[handler_start:handler_end]

        guard_index = handler_body.index("if (item.value === country) return;")
        invalidate_index = handler_body.index("invalidateScan();")
        set_country_index = handler_body.index("setCountry(item.value);")

        # Tapping the already-selected country must return before touching
        # the scan at all, so loading/data/requestId are left exactly as
        # they were - no stuck spinner from a request that never restarts.
        self.assertLess(guard_index, invalidate_index)
        self.assertLess(invalidate_index, set_country_index)

    def test_reselecting_the_same_scope_is_a_no_op(self) -> None:
        source = Path("mobile/src/app/(tabs)/scanner.tsx").read_text(encoding="utf-8")

        handler_start = source.index("onPress={() => {", source.index("SCOPES.map"))
        handler_end = source.index("}}", handler_start)
        handler_body = source[handler_start:handler_end]

        guard_index = handler_body.index("if (item === scope) return;")
        invalidate_index = handler_body.index("invalidateScan();")
        set_scope_index = handler_body.index("setScope(item);")

        # Same guard for scope: tapping the already-selected scope must
        # return before invalidating the scan or changing scope, so the
        # spinner set by a stale invalidateScan() call is never left on.
        self.assertLess(guard_index, invalidate_index)
        self.assertLess(invalidate_index, set_scope_index)

    def test_an_actual_market_change_still_invalidates_and_reloads(self) -> None:
        source = Path("mobile/src/app/(tabs)/scanner.tsx").read_text(encoding="utf-8")

        country_handler_start = source.index("onPress={() => {", source.index("COUNTRIES.map"))
        country_handler_end = source.index("}}", country_handler_start)
        country_handler = source[country_handler_start:country_handler_end]

        scope_handler_start = source.index("onPress={() => {", source.index("SCOPES.map"))
        scope_handler_end = source.index("}}", scope_handler_start)
        scope_handler = source[scope_handler_start:scope_handler_end]

        # A real selection change (the tapped item differs from the current
        # one) must still fall through the guard and reach invalidateScan()
        # plus the state setter, so market/limit change, loadScanner gets a
        # new identity, and the debounced effect fires a fresh request.
        for handler in (country_handler, scope_handler):
            self.assertIn("invalidateScan();", handler)
        self.assertIn("setCountry(item.value);", country_handler)
        self.assertIn("setScope(item);", scope_handler)

        # loadScanner still assigns its own fresh requestId once it runs,
        # on top of whatever invalidateScan() already bumped for the change.
        self.assertIn("const requestId = ++latestRequestId.current;", source)

    def test_ota_configuration_is_channel_bound(self) -> None:
        app = Path("mobile/app.json").read_text(encoding="utf-8")
        eas = Path("mobile/eas.json").read_text(encoding="utf-8")
        package = Path("mobile/package.json").read_text(encoding="utf-8")
        self.assertIn('"expo-updates"', package)
        self.assertIn('"runtimeVersion"', app)
        self.assertIn('"updates"', app)
        self.assertIn('"channel": "preview"', eas)
        self.assertIn('"channel": "production"', eas)

    def test_web_output_is_not_static_export(self) -> None:
        # `web.output: "static"` pre-renders one HTML file per route at
        # build time and needs generateStaticParams() for every dynamic
        # route - events/[eventId].tsx and events/[eventId]/edit.tsx can't
        # supply that meaningfully, since event ids are live API data, not
        # known at build time. "single" (Expo's own default) is a true SPA:
        # one index.html, all routing - including these dynamic ones -
        # resolved client-side, so a direct link or reload still works.
        app = Path("mobile/app.json").read_text(encoding="utf-8")
        self.assertNotIn('"output": "static"', app)


class StrategyDraftMobileSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.api_source = API_SERVICE.read_text(encoding="utf-8")
        cls.layout_source = STRATEGY_LAYOUT.read_text(encoding="utf-8")
        cls.summary_source = STRATEGY_SUMMARY_SCREEN.read_text(encoding="utf-8")
        cls.edit_source = STRATEGY_EDIT_SCREEN.read_text(encoding="utf-8")
        cls.confirm_source = STRATEGY_CONFIRM_SCREEN.read_text(encoding="utf-8")
        cls.format_util_source = STRATEGY_DRAFT_FORMAT_UTIL.read_text(encoding="utf-8")
        cls.reset_hook_source = RESET_ON_KEY_CHANGE_UTIL.read_text(encoding="utf-8")

    # -- api.ts: preview vs. approve use distinct, correctly-scoped auth ---

    def test_preview_uses_the_read_key_not_the_control_key(self) -> None:
        preview_start = self.api_source.index("export function previewStrategyDraft(")
        preview_end = self.api_source.index("\n}\n", preview_start)
        preview_body = self.api_source[preview_start:preview_end]

        self.assertIn("X-MarketAI-Key", preview_body)
        self.assertNotIn("X-MarketAI-Control-Key", preview_body)

    def test_approve_uses_the_control_key_not_the_read_key(self) -> None:
        approve_start = self.api_source.index("export function approveStrategyDraft(")
        approve_end = self.api_source.index("\n}\n", approve_start)
        approve_body = self.api_source[approve_start:approve_end]

        self.assertIn("X-MarketAI-Control-Key", approve_body)
        self.assertNotIn("X-MarketAI-Key", approve_body)

    def test_control_key_env_var_is_distinct_from_admin_and_service_role(self) -> None:
        self.assertIn("EXPO_PUBLIC_MARKETAI_CONTROL_API_KEY", self.api_source)
        for path in MOBILE_SRC_ROOT.rglob("*.ts*"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("MARKETAI_ADMIN_API_KEY", text)
            self.assertNotIn("SERVICE_ROLE", text)
            self.assertNotIn("MARKETAI_SUPABASE_SECRET_KEY", text)
            self.assertNotIn("X-Admin-Token", text)

    # -- strategy summary: two clear actions, draft state, all sections ----

    def test_summary_screen_shows_the_two_primary_actions(self) -> None:
        self.assertIn("Muokkaa luonnosta", self.summary_source)
        self.assertIn("Hyväksy strategia", self.summary_source)

    def test_summary_screen_shows_draft_state_and_every_required_section(self) -> None:
        for text in (
            "LUONNOS",
            "YHTEENVETO",
            "KONSENSUS",
            "TÄRKEIMMÄT KPI",
            "BULL-SKENAARIO",
            "BASE-SKENAARIO",
            "BEAR-SKENAARIO",
            "TRIGGERIT",
            "NO TRADE",
            "LÄHTEET",
        ):
            self.assertIn(text, self.summary_source)

    def test_summary_screen_links_to_the_advanced_raw_editor(self) -> None:
        self.assertIn("pathname: '/events/[eventId]/edit'", self.summary_source)

    def test_approve_action_only_calls_preview_never_approve_directly(self) -> None:
        # Tapping "Hyväksy strategia" on the summary screen must only ever
        # run a preview and navigate to the confirmation screen - it must
        # never call approveStrategyDraft itself, so a draft can never be
        # persisted without passing through the explicit confirm screen.
        self.assertIn("previewStrategyDraft", self.summary_source)
        self.assertNotIn("approveStrategyDraft", self.summary_source)

    # -- draft editor never persists anything itself ------------------------

    def test_draft_editor_never_calls_the_backend(self) -> None:
        self.assertNotIn("previewStrategyDraft", self.edit_source)
        self.assertNotIn("approveStrategyDraft", self.edit_source)
        self.assertNotIn("fetch(", self.edit_source)

    def test_saving_an_edited_draft_clears_the_stale_preview(self) -> None:
        # A draft saved from the editor may differ from whatever was last
        # previewed - the summary screen's ESIKATSELTU state and any
        # preview warnings must never keep being shown (or be approvable)
        # against a draft that has since changed underneath it. save()
        # must clear the shared preview unconditionally, every time, not
        # just when the edited fields happen to differ from before.
        self.assertIn("setDraft, setPreview, event", self.edit_source)
        save_start = self.edit_source.index("const save = () => {")
        save_end = self.edit_source.index("\n  };", save_start)
        save_body = self.edit_source[save_start:save_end]

        self.assertIn("setDraft(local)", save_body)
        self.assertIn("setPreview(null)", save_body)
        # Must run on every save, not only conditionally when a preview
        # already happens to be present.
        self.assertNotIn("if (preview)", save_body)

    # -- confirmation screen: explicit, non-accidental approval -------------

    def test_confirm_screen_states_the_locked_version_and_no_trade_conditions(self) -> None:
        self.assertIn("lukitaan eventin expectation-versioksi", self.confirm_source)
        self.assertIn("nextVersion", self.confirm_source)
        self.assertIn("NO TRADE", self.confirm_source)
        self.assertIn("invalidation_conditions", self.confirm_source)
        self.assertIn("TRIGGERIT (RAJAT)", self.confirm_source)

    def test_confirm_screen_requires_an_explicit_acknowledgement_switch(self) -> None:
        self.assertIn("<Switch", self.confirm_source)
        self.assertIn("acknowledged", self.confirm_source)
        can_submit_index = self.confirm_source.index("const canSubmit =")
        can_submit_end = self.confirm_source.index(";", can_submit_index)
        self.assertIn("acknowledged", self.confirm_source[can_submit_index:can_submit_end])

    def test_confirm_button_is_disabled_until_acknowledged(self) -> None:
        self.assertIn("disabled={!canSubmit}", self.confirm_source)

    def test_approve_requires_a_second_native_confirmation_dialog(self) -> None:
        # A single tap on the approve button must open a native Alert with
        # its own explicit confirm action - the actual API call only
        # happens from that dialog's confirm button, not from the button
        # press itself.
        on_press_start = self.confirm_source.index("const onApprovePress = () => {")
        on_press_end = self.confirm_source.index("\n  };", on_press_start)
        on_press_body = self.confirm_source[on_press_start:on_press_end]

        self.assertIn("Alert.alert(", on_press_body)
        self.assertIn("void submitApproval()", on_press_body)
        # The button's own handler must never call the API directly - only
        # the dialog's confirm action (submitApproval, invoked above) does.
        self.assertNotIn("await approveStrategyDraft(", on_press_body)

        submit_start = self.confirm_source.index("const submitApproval = async () => {")
        submit_end = self.confirm_source.index("\n  };", submit_start)
        submit_body = self.confirm_source[submit_start:submit_end]
        self.assertIn("await approveStrategyDraft(", submit_body)

        approve_button_index = self.confirm_source.index("Hyväksy ja lukitse")
        # The direct approve button handler is onApprovePress (opens the
        # dialog), not a handler that calls the API synchronously.
        self.assertLess(self.confirm_source.index("onPress={onApprovePress}"), approve_button_index)

    def test_confirm_screen_requires_an_approver_identity(self) -> None:
        self.assertIn("approvedBy", self.confirm_source)
        can_submit_index = self.confirm_source.index("const canSubmit =")
        can_submit_end = self.confirm_source.index(";", can_submit_index)
        self.assertIn("approvedBy.trim().length > 0", self.confirm_source[can_submit_index:can_submit_end])

    def test_conflict_response_forces_a_fresh_preview_not_a_resubmit(self) -> None:
        self.assertIn("setConflict(true)", self.confirm_source)
        self.assertIn("setPreview(null)", self.confirm_source)
        self.assertIn("Tee esikatselu uudelleen", self.confirm_source)

    def test_missing_preview_never_shows_the_approve_button(self) -> None:
        guard_start = self.confirm_source.index("if (!draft || !preview) {")
        guard_end = self.confirm_source.index("\n  }\n", guard_start)
        guard_body = self.confirm_source[guard_start:guard_end]
        self.assertNotIn("approveStrategyDraft", guard_body)
        self.assertIn("return (", guard_body)

    # -- draft state stays purely client-side until preview/approve --------

    def test_strategy_layout_holds_draft_state_client_side(self) -> None:
        self.assertIn("useState<", self.layout_source)
        self.assertNotIn("fetch(", self.layout_source)
        self.assertNotIn("approveStrategyDraft", self.layout_source)
        self.assertNotIn("previewStrategyDraft", self.layout_source)

    def test_format_util_never_touches_the_network(self) -> None:
        self.assertNotIn("fetch(", self.format_util_source)
        self.assertNotIn("apiPost", self.format_util_source)

    # -- consensus/trigger lossless round-trip (JSON object representation) -

    def _extract_function(self, name: str) -> str:
        source = self.format_util_source
        start = source.index(f"export function {name}(")
        end = source.index("\n}\n", start) + len("\n}")
        return source[start:end]

    def _run_typed_record_script(self, script_suffix: str) -> dict:
        """Extracts recordToText/parseTypedRecord/textToTypedRecord from
        strategy-draft-format.ts, strips their TS-only syntax, and runs them
        under node with the given trailing script appended. Returns the
        parsed JSON the script printed."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        combined = "\n".join(
            self._extract_function(name)
            for name in ("recordToText", "parseTypedRecord", "textToTypedRecord")
        )

        combined = combined.replace("export function ", "function ")
        combined = combined.replace("(record: Record<string, unknown>): string", "(record)")
        combined = combined.replace("(text: string): TypedRecordParseResult", "(text)")
        combined = combined.replace(
            "(text: string): Record<string, number | string | null>", "(text)"
        )
        combined = combined.replace("let parsed: unknown;", "let parsed;")
        combined = combined.replace(
            "const value: Record<string, number | string | null> = {};",
            "const value = {};",
        )
        combined = combined.replace(
            "const entries: [string, number | string | null][] = [];",
            "const entries = [];",
        )
        combined = combined.replace(" as Record<string, unknown>", "")
        self.assertNotIn(": Record", combined)
        self.assertNotIn(": string", combined)
        self.assertNotIn(": unknown", combined)
        self.assertNotIn(": [string,", combined)
        self.assertNotIn("TypedRecordParseResult", combined)

        script = combined + "\n" + script_suffix

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, script_path], capture_output=True, text=True, timeout=10
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_every_scalar_type_and_a_colon_containing_key_round_trip_losslessly(
        self,
    ) -> None:
        # The representation is a single JSON object literal (recordToText
        # -> JSON.stringify, textToTypedRecord -> JSON.parse), not "key:
        # value" lines - so there is no colon-splitting anywhere to trip up
        # on a key that itself contains a colon, and no heuristic
        # numeric/null guessing to mis-type a value.
        outputs = self._run_typed_record_script(
            """
const original = {
  "Revenue: FY27": "colon-in-key value",
  leading_zero_string: "001",
  literal_null_string: "null",
  actual_null: null,
  plain_number: 1,
  decimal_number: 55.6,
  plain_string: "manual",
};
const text = recordToText(original);
const roundTripped = textToTypedRecord(text);
console.log(JSON.stringify({ text, roundTripped }));
"""
        )

        round_tripped = outputs["roundTripped"]
        # A key containing ":" must survive completely unchanged - this is
        # exactly the case a line-based "key: value" format cannot express
        # unambiguously, since it can't tell the key's own colon apart from
        # the key/value separator.
        self.assertIn("Revenue: FY27", round_tripped)
        self.assertEqual(round_tripped["Revenue: FY27"], "colon-in-key value")
        # The string "001" must never become the number 1.
        self.assertEqual(round_tripped["leading_zero_string"], "001")
        self.assertIsInstance(round_tripped["leading_zero_string"], str)
        # The string "null" must never become real null.
        self.assertEqual(round_tripped["literal_null_string"], "null")
        self.assertIsInstance(round_tripped["literal_null_string"], str)
        # Real null must stay null, not become the string "null".
        self.assertIsNone(round_tripped["actual_null"])
        # A real number must stay a number.
        self.assertEqual(round_tripped["plain_number"], 1)
        self.assertNotIsInstance(round_tripped["plain_number"], str)
        self.assertEqual(round_tripped["decimal_number"], 55.6)
        self.assertEqual(round_tripped["plain_string"], "manual")

        # And the rendered text is genuinely the value's own JSON
        # representation - not a hand-built "${value}" interpolation that
        # could itself be ambiguous.
        self.assertEqual(
            json.loads(outputs["text"]),
            {
                "Revenue: FY27": "colon-in-key value",
                "leading_zero_string": "001",
                "literal_null_string": "null",
                "actual_null": None,
                "plain_number": 1,
                "decimal_number": 55.6,
                "plain_string": "manual",
            },
        )

    def test_a_literal_proto_key_round_trips_instead_of_being_silently_dropped(
        self,
    ) -> None:
        # Building the parsed record with `obj[key] = value` on a plain {}
        # goes through Object.prototype's own __proto__ *setter* for a key
        # literally named "__proto__", which silently discards non-object
        # values instead of storing them - so a key named exactly that would
        # vanish without any error. parseTypedRecord builds the record via
        # Object.fromEntries instead, which creates a genuine own property
        # for every key, "__proto__" included.
        outputs = self._run_typed_record_script(
            """
// Written as a raw JSON string, not a `{ "__proto__": ... }` object
// literal - an object *literal* with that exact key is itself
// special-cased by the JS spec to set the prototype rather than create
// an own property, which would trip this test up before parseTypedRecord
// even runs. JSON.parse has no such special case: it always creates a
// genuine own property, whatever the key's name.
const text = '{"__proto__": "should not vanish", "other": 1}';
const parsed = parseTypedRecord(text);
const roundTripped = textToTypedRecord(text);
console.log(JSON.stringify({
  ok: parsed.ok,
  hasOwnProto: Object.prototype.hasOwnProperty.call(parsed.value, "__proto__"),
  protoValue: parsed.value["__proto__"],
  roundTrippedHasOwnProto: Object.prototype.hasOwnProperty.call(roundTripped, "__proto__"),
  roundTrippedProtoValue: roundTripped["__proto__"],
  other: roundTripped.other,
  valueIsStillAPlainObject: Object.getPrototypeOf(parsed.value) === Object.prototype,
}));
"""
        )

        self.assertTrue(outputs["ok"])
        self.assertTrue(outputs["hasOwnProto"])
        self.assertEqual(outputs["protoValue"], "should not vanish")
        self.assertTrue(outputs["roundTrippedHasOwnProto"])
        self.assertEqual(outputs["roundTrippedProtoValue"], "should not vanish")
        self.assertEqual(outputs["other"], 1)
        # Object.fromEntries still yields an ordinary object, not one whose
        # prototype got reassigned - "__proto__" is stored as a data
        # property, not interpreted as the prototype-setting accessor.
        self.assertTrue(outputs["valueIsStillAPlainObject"])

    def test_invalid_structured_input_is_reported_as_an_error_not_guessed(self) -> None:
        outputs = self._run_typed_record_script(
            """
const invalidJson = parseTypedRecord('{not valid json');
const notAnObject = parseTypedRecord('[1, 2, 3]');
const badValueType = parseTypedRecord('{"key": true}');
const nestedObject = parseTypedRecord('{"key": {"nested": 1}}');
const validEmpty = parseTypedRecord('');
let threwForInvalidJson = false;
try {
  textToTypedRecord('{not valid json');
} catch (err) {
  threwForInvalidJson = true;
}
console.log(JSON.stringify({
  invalidJson, notAnObject, badValueType, nestedObject, validEmpty, threwForInvalidJson,
}));
"""
        )

        for case_name in ("invalidJson", "notAnObject", "badValueType", "nestedObject"):
            case = outputs[case_name]
            self.assertFalse(case["ok"], f"{case_name} was incorrectly accepted")
            self.assertTrue(case.get("error"), f"{case_name} has no error message")

        # Empty/whitespace-only text is treated as "no entries yet", not an
        # error - a freshly-opened draft with nothing typed must not show a
        # validation error before the user has written anything.
        self.assertTrue(outputs["validEmpty"]["ok"])
        self.assertEqual(outputs["validEmpty"]["value"], {})

        # textToTypedRecord() (used by draftFormToInput -> preview/approve)
        # must raise for the same invalid input parseTypedRecord flags -
        # never silently normalize/guess a value to send to the backend.
        self.assertTrue(outputs["threwForInvalidJson"])

    def test_a_number_that_overflows_to_infinity_is_rejected_not_normalized_to_null(
        self,
    ) -> None:
        # "1e400" is a syntactically valid JSON number literal - JSON has no
        # way to write NaN or a literal Infinity/-Infinity token, so this
        # overflow is the only route a non-finite number can take through
        # JSON.parse. It must surface as a validation error the user sees,
        # never be silently accepted: JSON.stringify(Infinity) renders as
        # `null`, so an accepted 1e400 would quietly turn into a real null
        # value the next time this record round-trips through recordToText.
        outputs = self._run_typed_record_script(
            """
const overflow = parseTypedRecord('{"metric": 1e400}');
const negativeOverflow = parseTypedRecord('{"metric": -1e400}');
const finiteControl = parseTypedRecord('{"metric": 55.6}');
let threwForOverflow = false;
try {
  textToTypedRecord('{"metric": 1e400}');
} catch (err) {
  threwForOverflow = true;
}
console.log(JSON.stringify({
  overflow, negativeOverflow, finiteControl, threwForOverflow,
}));
"""
        )

        self.assertFalse(outputs["overflow"]["ok"])
        self.assertTrue(outputs["overflow"].get("error"))
        self.assertFalse(outputs["negativeOverflow"]["ok"])
        self.assertTrue(outputs["negativeOverflow"].get("error"))
        # An ordinary finite number must still be accepted - this isn't
        # rejecting all numbers, only the non-finite ones.
        self.assertTrue(outputs["finiteControl"]["ok"])
        self.assertEqual(outputs["finiteControl"]["value"]["metric"], 55.6)
        # textToTypedRecord() (used by draftFormToInput -> preview/approve)
        # must raise for the same overflow, never send Infinity onward.
        self.assertTrue(outputs["threwForOverflow"])

    def test_draft_to_input_uses_the_same_typed_parser_for_consensus_and_triggers(self) -> None:
        self.assertIn(
            "consensus: textToTypedRecord(draft.consensusText)", self.format_util_source
        )
        self.assertIn(
            "triggers: textToTypedRecord(draft.triggersText)", self.format_util_source
        )
        # The old heuristic numeric/null-guessing parser and the old
        # colon-split line format must be gone entirely, not just unused -
        # line.indexOf(':') was specifically how a key's own ":" used to
        # get confused with the key/value separator (the explanatory
        # comment at the top of the file still references the old pattern
        # by name, which is fine - only the actual code matters here).
        # Number.isFinite *is* still expected here - unlike the old
        # heuristic Number(rawValue) guess, it's now used to reject a
        # non-finite JSON number (e.g. "1e400" overflowing to Infinity),
        # not to guess a value's type.
        self.assertNotIn("Number(rawValue)", self.format_util_source)
        self.assertNotIn("separatorIndex", self.format_util_source)
        self.assertNotIn("line.slice(", self.format_util_source)

    def test_summary_screen_renders_consensus_and_triggers_via_the_typed_parser(self) -> None:
        # Confirms the summary screen doesn't reintroduce line-splitting to
        # *display* consensus/triggers even though textToTypedRecord/
        # draftFormToInput are only used for the API payload - RecordCard
        # must use the same parseTypedRecord() as everything else, and must
        # show its error rather than rendering garbage from a naive split.
        self.assertIn("function RecordCard(", self.summary_source)
        record_card_start = self.summary_source.index("function RecordCard(")
        record_card_end = self.summary_source.index("\n}\n", record_card_start)
        record_card_body = self.summary_source[record_card_start:record_card_end]

        self.assertIn("parseTypedRecord(text)", record_card_body)
        self.assertIn("result.error", record_card_body)
        self.assertIn("RecordCard text={draft.consensusText}", self.summary_source)
        self.assertIn("RecordCard text={draft.triggersText}", self.summary_source)

    # -- route event change resets stale draft/preview/approval state -------

    def test_layout_resets_event_draft_and_preview_on_route_change(self) -> None:
        self.assertIn("useResetOnKeyChange(eventId,", self.layout_source)
        reset_start = self.layout_source.index("useResetOnKeyChange(eventId, () => {")
        reset_end = self.layout_source.index("});", reset_start)
        reset_body = self.layout_source[reset_start:reset_end]
        self.assertIn("setEvent(null)", reset_body)
        self.assertIn("setDraft(null)", reset_body)
        self.assertIn("setPreview(null)", reset_body)

    def test_edit_screen_resyncs_local_draft_on_route_change(self) -> None:
        self.assertIn(
            "useResetOnKeyChange(eventId, () => setLocal(draft))", self.edit_source
        )

    def test_summary_screen_invalidates_in_flight_load_before_anything_else_on_route_change(
        self,
    ) -> None:
        # load() only guards against a stale response via
        # `loadId !== latestLoadId.current`, and load() itself is invoked
        # from a deferred setTimeout(0) - so the ref must be bumped
        # synchronously the moment eventId changes (here, in the
        # useResetOnKeyChange callback), not only from inside load() the
        # next time it happens to run. Otherwise a slow response for the
        # *previous* event could resolve in the gap between the route
        # changing and the new load() actually starting, still pass the
        # (not-yet-bumped) staleness check, and repopulate the freshly
        # cleared screen with the wrong event's data.
        self.assertIn("useResetOnKeyChange(eventId, () => {", self.summary_source)
        reset_start = self.summary_source.index("useResetOnKeyChange(eventId, () => {")
        reset_end = self.summary_source.index("});", reset_start)
        reset_body = self.summary_source[reset_start:reset_end]

        self.assertIn("latestLoadId.current += 1;", reset_body)
        self.assertIn("setError(null);", reset_body)

        # The ref bump must be the first statement in the callback, ahead
        # of the state resets - not merely present somewhere in the body.
        bump_index = reset_body.index("latestLoadId.current += 1;")
        set_error_index = reset_body.index("setError(null);")
        self.assertLess(bump_index, set_error_index)

    def test_load_still_guards_against_its_own_stale_responses(self) -> None:
        # The existing same-event staleness guard (e.g. overlapping
        # pull-to-refresh calls) must remain intact alongside the route
        # -change invalidation above - both must hold at once.
        load_start = self.summary_source.index("const load = useCallback(async () => {")
        load_end = self.summary_source.index("}, [eventId]);", load_start)
        load_body = self.summary_source[load_start:load_end]

        self.assertIn("const loadId = ++latestLoadId.current;", load_body)
        self.assertIn("if (loadId !== latestLoadId.current) return;", load_body)

    # -- preview request cancellation guard ----------------------------------

    def test_approve_press_guards_against_a_stale_preview_response(self) -> None:
        # A previewStrategyDraft() response that resolves after eventId
        # changed or the screen unmounted must never call setPreview() or
        # navigate to the confirmation screen - both would act on the
        # wrong event.
        press_start = self.summary_source.index("const onApprovePress = useCallback(async () => {")
        press_end = self.summary_source.index("}, [eventId, draft, router, setPreview]);", press_start)
        press_body = self.summary_source[press_start:press_end]

        self.assertIn("const requestId = ++latestPreviewRequestId.current;", press_body)

        # setPreview() must be guarded by the requestId check immediately
        # before it, not just somewhere earlier in the function.
        set_preview_index = press_body.index("setPreview(result);")
        guard_before_set_preview = press_body.rindex(
            "if (requestId !== latestPreviewRequestId.current) return;", 0, set_preview_index
        )
        self.assertLess(guard_before_set_preview, set_preview_index)
        # And nothing else must sit between that guard and setPreview() -
        # the navigation call included, since a stale response must skip
        # the push() too.
        between = press_body[guard_before_set_preview:set_preview_index]
        self.assertNotIn("router.push", between)

        # The catch/finally branches must carry their own guard too - a
        # stale error/loading-cleared update must not apply either.
        catch_start = press_body.index("} catch (err) {")
        finally_start = press_body.index("} finally {")
        catch_block = press_body[catch_start:finally_start]
        finally_block = press_body[finally_start:]
        self.assertIn("if (requestId !== latestPreviewRequestId.current) return;", catch_block)
        self.assertIn("if (requestId === latestPreviewRequestId.current) setPreviewing(false);", finally_block)

    def test_preview_request_id_is_invalidated_on_route_change(self) -> None:
        reset_start = self.summary_source.index("useResetOnKeyChange(eventId, () => {")
        reset_end = self.summary_source.index("});", reset_start)
        reset_body = self.summary_source[reset_start:reset_end]

        self.assertIn("latestPreviewRequestId.current += 1;", reset_body)

    def test_preview_request_id_is_invalidated_on_unmount(self) -> None:
        # Distinct from the route-change reset above: navigating away from
        # the strategy flow entirely (not just to a different eventId)
        # must also stop a still-in-flight preview response from touching
        # state on an unmounted screen or firing a stray navigation.
        unmount_effect_start = self.summary_source.index(
            "useEffect(() => {\n    return () => {\n      latestPreviewRequestId.current += 1;"
        )
        self.assertGreaterEqual(unmount_effect_start, 0)
        effect_end = self.summary_source.index("}, []);", unmount_effect_start)
        effect_body = self.summary_source[unmount_effect_start:effect_end]
        self.assertIn("latestPreviewRequestId.current += 1;", effect_body)

    def test_confirm_screen_resets_approval_state_on_route_change(self) -> None:
        self.assertIn("useResetOnKeyChange(eventId, () => {", self.confirm_source)
        reset_start = self.confirm_source.index("useResetOnKeyChange(eventId, () => {")
        reset_end = self.confirm_source.index("});", reset_start)
        reset_body = self.confirm_source[reset_start:reset_end]
        self.assertIn("setApprovedBy('')", reset_body)
        self.assertIn("setAcknowledged(false)", reset_body)
        self.assertIn("setError(null)", reset_body)
        self.assertIn("setConflict(false)", reset_body)

        # A pending approveStrategyDraft() call for the previous event must
        # be invalidated first, ahead of every state reset in this same
        # callback - not left to resolve and act on the new event's screen.
        self.assertIn("latestApprovalRequestId.current += 1;", reset_body)
        bump_index = reset_body.index("latestApprovalRequestId.current += 1;")
        set_approved_by_index = reset_body.index("setApprovedBy('')")
        self.assertLess(bump_index, set_approved_by_index)

    # -- approveStrategyDraft() request cancellation guard -------------------
    #
    # Regression scenario: event A's approval is submitted and still
    # in-flight when the route changes to event B (eventId changes while
    # this screen stays mounted, or the confirm screen for B is opened
    # fresh while A's request is still pending in the background). A's
    # response resolving afterwards must never clear B's draft/preview,
    # must never navigate away from B, and must never write B's
    # error/conflict/submitting state.

    def test_submit_approval_captures_a_request_id_before_awaiting(self) -> None:
        submit_start = self.confirm_source.index("const submitApproval = async () => {")
        submit_end = self.confirm_source.index("\n  };", submit_start)
        submit_body = self.confirm_source[submit_start:submit_end]

        request_id_index = submit_body.index(
            "const requestId = ++latestApprovalRequestId.current;"
        )
        await_index = submit_body.index("await approveStrategyDraft(")
        self.assertLess(request_id_index, await_index)

    def test_stale_approval_success_cannot_clear_state_or_navigate(self) -> None:
        submit_start = self.confirm_source.index("const submitApproval = async () => {")
        submit_end = self.confirm_source.index("\n  };", submit_start)
        submit_body = self.confirm_source[submit_start:submit_end]

        await_index = submit_body.index("await approveStrategyDraft(")
        catch_index = submit_body.index("} catch (err) {")
        success_block = submit_body[await_index:catch_index]

        guard = "if (requestId !== latestApprovalRequestId.current) return;"
        self.assertIn(guard, success_block)
        guard_index = success_block.index(guard)

        # The guard must sit ahead of every state-clearing/navigating call
        # in the success path - not merely appear somewhere in the block.
        for call in ("setDraft(null)", "setPreview(null)", "router.replace("):
            call_index = success_block.index(call)
            self.assertLess(
                guard_index, call_index, f"{call} is not guarded by the stale-response check"
            )

    def test_stale_approval_error_and_finally_cannot_update_the_screen(self) -> None:
        submit_start = self.confirm_source.index("const submitApproval = async () => {")
        submit_end = self.confirm_source.index("\n  };", submit_start)
        submit_body = self.confirm_source[submit_start:submit_end]

        catch_start = submit_body.index("} catch (err) {")
        finally_start = submit_body.index("} finally {")
        catch_block = submit_body[catch_start:finally_start]
        finally_block = submit_body[finally_start:]

        guard = "if (requestId !== latestApprovalRequestId.current) return;"
        self.assertIn(guard, catch_block)
        guard_index = catch_block.index(guard)
        set_error_index = catch_block.index("setError(message)")
        self.assertLess(guard_index, set_error_index)

        self.assertIn(
            "if (requestId === latestApprovalRequestId.current) setSubmitting(false);",
            finally_block,
        )

    def test_approval_request_id_is_invalidated_on_unmount(self) -> None:
        unmount_effect_start = self.confirm_source.index(
            "useEffect(() => {\n    return () => {\n      latestApprovalRequestId.current += 1;"
        )
        self.assertGreaterEqual(unmount_effect_start, 0)
        effect_end = self.confirm_source.index("}, []);", unmount_effect_start)
        effect_body = self.confirm_source[unmount_effect_start:effect_end]
        self.assertIn("latestApprovalRequestId.current += 1;", effect_body)

    def test_reset_hook_updates_synchronously_during_render_not_in_an_effect(self) -> None:
        # A useEffect-based reset would leave a one-frame window where the
        # previous event's draft/preview/approval state is still rendered
        # under the new event's route before the effect runs. The guarded
        # conditional setState-during-render pattern has no such window.
        self.assertNotIn("useEffect", self.reset_hook_source)
        self.assertIn("if (key !== seenKey) {", self.reset_hook_source)

    def test_every_strategy_screen_imports_the_reset_hook(self) -> None:
        for source in (self.layout_source, self.edit_source, self.confirm_source, self.summary_source):
            self.assertIn("useResetOnKeyChange", source)


if __name__ == "__main__":
    unittest.main()
