from pathlib import Path

home = Path("mobile/src/app/(tabs)/index.tsx")
text = home.read_text(encoding="utf-8")
text = text.replace(
    "            key={event.event_id}\n            event={event}",
    "            key={`${event.event_id}:${trackedRefreshToken}`}\n            event={event}",
    1,
)
text = text.replace(
    "            trackedEvent={\n              calendarEventId ? persistentEventByCalendarEventId[calendarEventId] : undefined\n            }\n          />",
    "            trackedEvent={\n              calendarEventId ? persistentEventByCalendarEventId[calendarEventId] : undefined\n            }\n            refreshToken={trackedRefreshToken}\n          />",
    1,
)
text = text.replace(
    "  trackedEvent,\n}: {\n  event: EventExpectation;\n  status?: EventStatus;\n  runtimeStatus?: string;\n  trackedEvent?: TrackedMarketEvent;\n}) {",
    "  trackedEvent,\n  refreshToken = 0,\n}: {\n  event: EventExpectation;\n  status?: EventStatus;\n  runtimeStatus?: string;\n  trackedEvent?: TrackedMarketEvent;\n  refreshToken?: number;\n}) {",
    1,
)
text = text.replace(
    "        {trackedEvent ? <TrackedEventDetails event={trackedEvent} /> : null}",
    "        {trackedEvent ? (\n          <TrackedEventDetails event={trackedEvent} refreshToken={refreshToken} showSchedule />\n        ) : null}",
    1,
)
home.write_text(text, encoding="utf-8")

component = Path("mobile/src/components/TrackedEventsSection.tsx")
text = component.read_text(encoding="utf-8")
old_sig = "export function TrackedEventDetails({ event }: { event: TrackedMarketEvent }) {"
new_sig = "export function TrackedEventDetails({\n  event,\n  refreshToken = 0,\n  showSchedule = false,\n}: {\n  event: TrackedMarketEvent;\n  refreshToken?: number;\n  showSchedule?: boolean;\n}) {"
if old_sig not in text:
    raise SystemExit("TrackedEventDetails signature anchor missing")
text = text.replace(old_sig, new_sig, 1)
text = text.replace(
    "    }, [event.event_id]),",
    "    }, [event.event_id, refreshToken]),",
    1,
)
text = text.replace(
    "  return (\n    <>\n      <View style={styles.statusBlock}>",
    "  const scheduleText = showSchedule ? formatTrackedEventSchedule(event) : null;\n\n  return (\n    <>\n      {scheduleText ? <Text style={styles.detailText}>Runtime {scheduleText}</Text> : null}\n      <View style={styles.statusBlock}>",
    1,
)
component.write_text(text, encoding="utf-8")

test = Path("tests/test_mobile_source.py")
text = test.read_text(encoding="utf-8")
old_assert = '        self.assertIn("<TrackedEventDetails event={trackedEvent} />", self.home_source)\n'
new_assert = '        self.assertIn("<TrackedEventDetails event={trackedEvent} refreshToken={refreshToken} showSchedule />", self.home_source)\n'
if old_assert not in text:
    raise SystemExit("existing linked-details assertion anchor missing")
text = text.replace(old_assert, new_assert, 1)
anchor = "    # -- event card navigates to the detail route with the event id --------\n"
extra = '''    def test_linked_tracked_details_refresh_and_preserve_runtime_schedule(self) -> None:\n        component = Path("mobile/src/components/TrackedEventsSection.tsx").read_text(encoding="utf-8")\n        self.assertIn("key={`${event.event_id}:${trackedRefreshToken}`}", self.home_source)\n        self.assertIn("refreshToken={trackedRefreshToken}", self.home_source)\n        self.assertIn("<TrackedEventDetails event={trackedEvent} refreshToken={refreshToken} showSchedule />", self.home_source)\n        self.assertIn("refreshToken?: number", component)\n        self.assertIn("showSchedule?: boolean", component)\n        self.assertIn("[event.event_id, refreshToken]", component)\n        self.assertIn("Runtime {scheduleText}", component)\n        self.assertIn("formatTrackedEventSchedule(event)", component)\n\n'''
if anchor not in text:
    raise SystemExit("test insertion anchor missing")
text = text.replace(anchor, extra + anchor, 1)
test.write_text(text, encoding="utf-8")
