from pathlib import Path

home = Path("mobile/src/app/(tabs)/index.tsx")
text = home.read_text(encoding="utf-8")
text = text.replace(
    "import { TrackedEventsSection } from '@/components/TrackedEventsSection';",
    "import { TrackedEventDetails, TrackedEventsSection } from '@/components/TrackedEventsSection';",
    1,
)
text = text.replace(
    "import {\n  CalendarEvent,\n  EventExpectation,\n  getEvents,\n  getPaperStatus,\n  getUpcomingCalendarEvents,\n  PaperRun,\n} from '@/services/api';",
    "import {\n  CalendarEvent,\n  EventExpectation,\n  getEvents,\n  getPaperStatus,\n  getUpcomingCalendarEvents,\n  PaperRun,\n} from '@/services/api';\nimport { TrackedMarketEvent } from '@/services/tracked-events';",
    1,
)
text = text.replace(
    "type TrackedEventSnapshot = {\n  count: number;\n  calendarEventIds: string[];\n  statusByCalendarEventId: Record<string, string>;\n};",
    "type TrackedEventSnapshot = {\n  count: number;\n  calendarEventIds: string[];\n  statusByCalendarEventId: Record<string, string>;\n  eventByCalendarEventId: Record<string, TrackedMarketEvent>;\n};",
    1,
)
text = text.replace(
    "  const [persistentStatusByCalendarEventId, setPersistentStatusByCalendarEventId] = useState<\n    Record<string, string>\n  >({});",
    "  const [persistentStatusByCalendarEventId, setPersistentStatusByCalendarEventId] = useState<\n    Record<string, string>\n  >({});\n  const [persistentEventByCalendarEventId, setPersistentEventByCalendarEventId] = useState<\n    Record<string, TrackedMarketEvent>\n  >({});",
    1,
)
text = text.replace(
    "    setPersistentStatusByCalendarEventId(snapshot.statusByCalendarEventId);\n  }, []);",
    "    setPersistentStatusByCalendarEventId(snapshot.statusByCalendarEventId);\n    setPersistentEventByCalendarEventId(snapshot.eventByCalendarEventId);\n  }, []);",
    1,
)
text = text.replace(
    "            runtimeStatus={\n              calendarEventId ? persistentStatusByCalendarEventId[calendarEventId] : undefined\n            }\n          />",
    "            runtimeStatus={\n              calendarEventId ? persistentStatusByCalendarEventId[calendarEventId] : undefined\n            }\n            trackedEvent={\n              calendarEventId ? persistentEventByCalendarEventId[calendarEventId] : undefined\n            }\n          />",
    1,
)
text = text.replace(
    "  runtimeStatus,\n}: {\n  event: EventExpectation;\n  status?: EventStatus;\n  runtimeStatus?: string;\n}) {",
    "  runtimeStatus,\n  trackedEvent,\n}: {\n  event: EventExpectation;\n  status?: EventStatus;\n  runtimeStatus?: string;\n  trackedEvent?: TrackedMarketEvent;\n}) {",
    1,
)
text = text.replace(
    "        <View style={styles.statusRow}>\n          <Text style={styles.statusText}>{statusText}</Text>\n          {strategy ? (\n            <Text style={styles.strategyText}>\n              {localizeDirection(strategy.direction)}\n              {strategy.confidence !== undefined ? ` · ${strategy.confidence}` : ''}\n            </Text>\n          ) : null}\n        </View>\n      </Pressable>",
    "        <View style={styles.statusRow}>\n          <Text style={styles.statusText}>{statusText}</Text>\n          {strategy ? (\n            <Text style={styles.strategyText}>\n              {localizeDirection(strategy.direction)}\n              {strategy.confidence !== undefined ? ` · ${strategy.confidence}` : ''}\n            </Text>\n          ) : null}\n        </View>\n\n        {trackedEvent ? <TrackedEventDetails event={trackedEvent} /> : null}\n      </Pressable>",
    1,
)
home.write_text(text, encoding="utf-8")

component = Path("mobile/src/components/TrackedEventsSection.tsx")
text = component.read_text(encoding="utf-8")
text = text.replace(
    "type Snapshot = {\n  count: number;\n  calendarEventIds: string[];\n  statusByCalendarEventId: Record<string, string>;\n};",
    "type Snapshot = {\n  count: number;\n  calendarEventIds: string[];\n  statusByCalendarEventId: Record<string, string>;\n  eventByCalendarEventId: Record<string, TrackedMarketEvent>;\n};",
    1,
)
text = text.replace(
    "          statusByCalendarEventId: Object.fromEntries(\n            list\n              .filter((event) => Boolean(event.calendar_event_id))\n              .map((event) => [event.calendar_event_id as string, describeTrackedEvent(event).status]),\n          ),\n        });",
    "          statusByCalendarEventId: Object.fromEntries(\n            list\n              .filter((event) => Boolean(event.calendar_event_id))\n              .map((event) => [event.calendar_event_id as string, describeTrackedEvent(event).status]),\n          ),\n          eventByCalendarEventId: Object.fromEntries(\n            list\n              .filter((event) => Boolean(event.calendar_event_id))\n              .map((event) => [event.calendar_event_id as string, event]),\n          ),\n        });",
    1,
)
old = '''export function TrackedEventCard({ event }: { event: TrackedMarketEvent }) {\n  const scheduleText = formatTrackedEventSchedule(event);\n  const presentation = describeTrackedEvent(event);\n  const configText = formatTrackingConfigSnapshot(event.tracking_config_snapshot);\n  const [latestReactionState, setLatestReactionState] = useState<LatestReactionState>({\n    status: 'loading',\n  });\n\n  useFocusEffect(\n    useCallback(() => {\n      let active = true;\n      void getTrackedEventLatestReaction(event.event_id)\n        .then((response) => {\n          if (!active) return;\n          if (response.event_id !== event.event_id) {\n            setLatestReactionState({ status: 'error' });\n            return;\n          }\n          setLatestReactionState({ status: 'ready', reaction: response.latest_reaction });\n        })\n        .catch(() => {\n          if (!active) return;\n          setLatestReactionState({ status: 'error' });\n        });\n      return () => {\n        active = false;\n      };\n    }, [event.event_id]),\n  );\n\n  return (\n    <View style={styles.eventCard}>\n      <View style={styles.rowBetween}>\n        <View style={styles.titleBlock}>\n          <Text style={styles.company} numberOfLines={1}>\n            {event.company_name || event.title}\n          </Text>\n          <Text style={styles.symbol}>{event.instrument}</Text>\n        </View>\n        <Text style={styles.dateText}>{scheduleText}</Text>\n      </View>\n\n      <View style={styles.statusBlock}>\n        <Text style={styles.statusText}>{presentation.status}</Text>\n        {presentation.detail ? <Text style={styles.detailText}>{presentation.detail}</Text> : null}\n      </View>\n\n      <View style={styles.configBlock}>\n        <Text style={styles.configTitle}>Seuranta-asetukset</Text>\n        <Text style={styles.configText}>{configText}</Text>\n      </View>\n\n      <TrackedEventResult state={latestReactionState} />\n    </View>\n  );\n}\n'''
new = '''export function TrackedEventCard({ event }: { event: TrackedMarketEvent }) {\n  const scheduleText = formatTrackedEventSchedule(event);\n\n  return (\n    <View style={styles.eventCard}>\n      <View style={styles.rowBetween}>\n        <View style={styles.titleBlock}>\n          <Text style={styles.company} numberOfLines={1}>\n            {event.company_name || event.title}\n          </Text>\n          <Text style={styles.symbol}>{event.instrument}</Text>\n        </View>\n        <Text style={styles.dateText}>{scheduleText}</Text>\n      </View>\n\n      <TrackedEventDetails event={event} />\n    </View>\n  );\n}\n\nexport function TrackedEventDetails({ event }: { event: TrackedMarketEvent }) {\n  const presentation = describeTrackedEvent(event);\n  const configText = formatTrackingConfigSnapshot(event.tracking_config_snapshot);\n  const [latestReactionState, setLatestReactionState] = useState<LatestReactionState>({\n    status: 'loading',\n  });\n\n  useFocusEffect(\n    useCallback(() => {\n      let active = true;\n      void getTrackedEventLatestReaction(event.event_id)\n        .then((response) => {\n          if (!active) return;\n          if (response.event_id !== event.event_id) {\n            setLatestReactionState({ status: 'error' });\n            return;\n          }\n          setLatestReactionState({ status: 'ready', reaction: response.latest_reaction });\n        })\n        .catch(() => {\n          if (!active) return;\n          setLatestReactionState({ status: 'error' });\n        });\n      return () => {\n        active = false;\n      };\n    }, [event.event_id]),\n  );\n\n  return (\n    <>\n      <View style={styles.statusBlock}>\n        <Text style={styles.statusText}>{presentation.status}</Text>\n        {presentation.detail ? <Text style={styles.detailText}>{presentation.detail}</Text> : null}\n      </View>\n\n      <View style={styles.configBlock}>\n        <Text style={styles.configTitle}>Seuranta-asetukset</Text>\n        <Text style={styles.configText}>{configText}</Text>\n      </View>\n\n      <TrackedEventResult state={latestReactionState} />\n    </>\n  );\n}\n'''
if old not in text:
    raise SystemExit("TrackedEventCard anchor missing")
text = text.replace(old, new, 1)
component.write_text(text, encoding="utf-8")

test = Path("tests/test_mobile_source.py")
text = test.read_text(encoding="utf-8")
anchor = "    # -- event card navigates to the detail route with the event id --------\n"
extra = '''    def test_unified_expectation_card_preserves_linked_tracked_event_details(self) -> None:\n        component = Path("mobile/src/components/TrackedEventsSection.tsx").read_text(encoding="utf-8")\n        self.assertIn("eventByCalendarEventId", component)\n        self.assertIn("export function TrackedEventDetails", component)\n        self.assertIn("<TrackedEventResult state={latestReactionState} />", component)\n        self.assertIn("formatTrackingConfigSnapshot(event.tracking_config_snapshot)", component)\n        self.assertIn("trackedEvent={", self.home_source)\n        self.assertIn("<TrackedEventDetails event={trackedEvent} />", self.home_source)\n\n'''
if anchor not in text:
    raise SystemExit("test insertion anchor missing")
text = text.replace(anchor, extra + anchor, 1)
test.write_text(text, encoding="utf-8")
