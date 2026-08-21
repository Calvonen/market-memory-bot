import unittest
from pathlib import Path


HOME_SCREEN = Path("mobile/src/app/index.tsx")
NATIVE_TABS = Path("mobile/src/components/app-tabs.tsx")


class MobileSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = HOME_SCREEN.read_text(encoding="utf-8")

    def test_rejected_risk_reasons_remain_visible_with_quantity_and_reward_risk(self) -> None:
        self.assertIn("risk.reasons.join(' • ')", self.source)
        self.assertIn("risk?.status === 'REJECT'", self.source)
        self.assertIn("Enimmäismäärä ${risk.max_quantity", self.source)
        self.assertIn("Tuotto/riski ${risk.reward_risk", self.source)

    def test_waiting_confirmation_preserves_the_persisted_reason(self) -> None:
        self.assertIn(
            "run?.status === 'waiting_confirmation' ? run.message : null",
            self.source,
        )
        self.assertIn("{confirmationReason}", self.source)

    def test_unified_navigation_preserves_event_dashboard(self) -> None:
        tabs = NATIVE_TABS.read_text(encoding="utf-8")
        for route in ('name="index"', 'name="memory"', 'name="scanner"', 'name="trades"', 'name="settings"'):
            self.assertIn(route, tabs)
        self.assertIn("paper-status", self.source)

    def test_market_memory_screen_has_mobile_states(self) -> None:
        source = Path("mobile/src/app/memory.tsx").read_text(encoding="utf-8")
        for text in ("Ticker", "Analysoi", "ActivityIndicator", "Tärkeimmät analogiat"):
            self.assertIn(text, source)

    def test_new_market_memory_request_hides_previous_result(self) -> None:
        source = Path("mobile/src/app/memory.tsx").read_text(encoding="utf-8")
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
        source = Path("mobile/src/app/memory.tsx").read_text(encoding="utf-8")
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
        source = Path("mobile/src/app/memory.tsx").read_text(encoding="utf-8")

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
        source = Path("mobile/src/app/memory.tsx").read_text(encoding="utf-8")

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
        source = Path("mobile/src/app/memory.tsx").read_text(encoding="utf-8")

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
        source = Path("mobile/src/app/scanner.tsx").read_text(encoding="utf-8")

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
        source = Path("mobile/src/app/scanner.tsx").read_text(encoding="utf-8")

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
        source = Path("mobile/src/app/scanner.tsx").read_text(encoding="utf-8")

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
        source = Path("mobile/src/app/scanner.tsx").read_text(encoding="utf-8")

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
