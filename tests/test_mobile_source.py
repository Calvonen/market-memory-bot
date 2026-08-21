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
MOBILE_SRC_ROOT = Path("mobile/src")


class MobileSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home_source = HOME_SCREEN.read_text(encoding="utf-8")
        cls.detail_source = EVENT_DETAIL_SCREEN.read_text(encoding="utf-8")
        cls.edit_source = EVENT_EDIT_SCREEN.read_text(encoding="utf-8")
        cls.upcoming_source = UPCOMING_SCREEN.read_text(encoding="utf-8")
        cls.api_source = API_SERVICE.read_text(encoding="utf-8")

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
        self.assertIn("status ? describeStatus(status.run, status.statusError) : 'Ladataan...'", self.home_source)

    # -- event card navigates to the detail route with the event id --------

    def test_event_card_navigates_to_detail_with_event_id(self) -> None:
        self.assertIn("pathname: '/events/[eventId]'", self.home_source)
        self.assertIn("params: { eventId: event.event_id }", self.home_source)

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

    def test_detail_screen_links_to_settings_editor(self) -> None:
        self.assertIn("pathname: '/events/[eventId]/edit'", self.detail_source)

    def test_paper_status_failure_does_not_block_pre_release_expectation_render(self) -> None:
        # getEvent() and getPaperStatus() must be awaited independently: a
        # failing/unavailable paper-status lookup (e.g. before release) must
        # still let the fetched event's expectation data render, instead of
        # rejecting a combined Promise.all and leaving the screen error-only.
        self.assertNotIn("Promise.all", self.detail_source)
        set_event_index = self.detail_source.index("setEvent(await getEvent(eventId))")
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

    # -- upcoming/calendar foundation page: no fake data --------------------

    def test_upcoming_screen_only_reads_the_real_events_endpoint(self) -> None:
        self.assertIn("getEvents", self.upcoming_source)
        self.assertNotIn("fetch(", self.upcoming_source)

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

    def test_date_range_filter_excludes_past_events_but_kaikki_keeps_history(self) -> None:
        # Behavioral proof for the date-range filter: extract the real
        # filtered = useMemo(...) predicate from upcoming.tsx (plus the
        # marketForInstrument() it calls), strip TS syntax, and run it with
        # node against synthetic past/near/far events. A day-range filter
        # (e.g. "7 pv") must exclude a released/past-dated event even though
        # /api/v1/events intentionally still returns it (for Seurannassa);
        # "Kaikki" (no range selected) must keep showing it.
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this environment")

        market_fn_start = self.upcoming_source.index("function marketForInstrument(")
        market_fn_end = self.upcoming_source.index("\n}\n", market_fn_start) + len("\n}")
        market_fn_js = re.sub(
            r":\s*string\b", "", self.upcoming_source[market_fn_start:market_fn_end], count=2
        )

        filter_start = self.upcoming_source.index("const filtered = useMemo(() => {")
        filter_body_start = filter_start + len("const filtered = useMemo(() => {")
        filter_end = self.upcoming_source.index(
            "}, [events, market, search, rangeDays]);", filter_start
        )
        filter_body = self.upcoming_source[filter_body_start:filter_end]
        filter_fn_js = (
            "function filterEvents(events, market, search, rangeDays) {" + filter_body + "}"
        )
        self.assertNotIn(": string", filter_fn_js)

        script = f"""
{market_fn_js}
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

const events = [
  {{ event_id: 'past', instrument: 'HAS.L', event_name: 'Past event', scheduled_date: daysFromNow(-10) }},
  {{ event_id: 'today', instrument: 'HAS.L', event_name: 'Today event', scheduled_date: daysFromNow(0) }},
  {{ event_id: 'near', instrument: 'HAS.L', event_name: 'Near event', scheduled_date: daysFromNow(3) }},
  {{ event_id: 'far', instrument: 'HAS.L', event_name: 'Far event', scheduled_date: daysFromNow(60) }},
];

const sevenDayIds = filterEvents(events, 'Kaikki', '', 7).map((e) => e.event_id);
const allIds = filterEvents(events, 'Kaikki', '', null).map((e) => e.event_id);
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

        # "7 pv" must exclude the past event and the far (60 days out) event,
        # but keep today's and the near (3 days out) event.
        self.assertNotIn("past", outputs["sevenDayIds"])
        self.assertNotIn("far", outputs["sevenDayIds"])
        self.assertIn("today", outputs["sevenDayIds"])
        self.assertIn("near", outputs["sevenDayIds"])

        # "Kaikki" (no range) must still surface the full tracked history,
        # including the already-released past event - this is the whole
        # point of removing list_upcoming()'s status/date filter.
        self.assertEqual(set(outputs["allIds"]), {"past", "today", "near", "far"})

    def test_upcoming_screen_has_filter_and_tracking_ui(self) -> None:
        self.assertIn("Hae tickerillä", self.upcoming_source)
        self.assertIn("MARKKINA", self.upcoming_source)
        self.assertIn("JULKAISUPÄIVÄ", self.upcoming_source)
        self.assertIn("Seurannassa", self.upcoming_source)

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


if __name__ == "__main__":
    unittest.main()
