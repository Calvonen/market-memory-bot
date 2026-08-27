from pathlib import Path

index = Path("mobile/src/app/(tabs)/index.tsx")
text = index.read_text()
replacements = [
    (
        "type TrackedEventSnapshot = { count: number; calendarEventIds: string[] };",
        "type TrackedEventSnapshot = {\n  count: number;\n  calendarEventIds: string[];\n  statusByCalendarEventId: Record<string, string>;\n};",
    ),
    (
        "  const [persistentCalendarEventIds, setPersistentCalendarEventIds] = useState<Set<string>>(\n    () => new Set(),\n  );",
        "  const [persistentCalendarEventIds, setPersistentCalendarEventIds] = useState<Set<string>>(\n    () => new Set(),\n  );\n  const [persistentStatusByCalendarEventId, setPersistentStatusByCalendarEventId] = useState<\n    Record<string, string>\n  >({});\n  const [trackedRefreshToken, setTrackedRefreshToken] = useState(0);",
    ),
    (
        "  const handleTrackedEventSnapshot = useCallback((snapshot: TrackedEventSnapshot) => {\n    setTrackedEventCount(snapshot.count);\n    setPersistentCalendarEventIds(new Set(snapshot.calendarEventIds));\n  }, []);",
        "  const handleTrackedEventSnapshot = useCallback((snapshot: TrackedEventSnapshot) => {\n    setTrackedEventCount(snapshot.count);\n    setPersistentCalendarEventIds(new Set(snapshot.calendarEventIds));\n    setPersistentStatusByCalendarEventId(snapshot.statusByCalendarEventId);\n  }, []);\n\n  const expectationCalendarEventIds = useMemo(() => {\n    const ids = new Set<string>();\n    for (const event of events ?? []) {\n      if (!event.event_id.startsWith('calendar:')) continue;\n      const calendarEventId = event.event_id.slice('calendar:'.length);\n      if (calendarEventId) ids.add(calendarEventId);\n    }\n    return ids;\n  }, [events]);",
    ),
    (
        "  const onRefresh = useCallback(async () => {\n    setRefreshing(true);\n    await loadEvents();\n    setRefreshing(false);\n  }, [loadEvents]);",
        "  const onRefresh = useCallback(async () => {\n    setRefreshing(true);\n    setTrackedRefreshToken((value) => value + 1);\n    await loadEvents();\n    setRefreshing(false);\n  }, [loadEvents]);",
    ),
    (
        "      {events?.map((event) => (\n        <EventCard key={event.event_id} event={event} status={statuses[event.event_id]} />\n      ))}\n\n      <TrackedEventsSection onSnapshot={handleTrackedEventSnapshot} />",
        "      {events?.map((event) => {\n        const calendarEventId = event.event_id.startsWith('calendar:')\n          ? event.event_id.slice('calendar:'.length)\n          : null;\n        return (\n          <EventCard\n            key={event.event_id}\n            event={event}\n            status={statuses[event.event_id]}\n            runtimeStatus={\n              calendarEventId ? persistentStatusByCalendarEventId[calendarEventId] : undefined\n            }\n          />\n        );\n      })}\n\n      <TrackedEventsSection\n        onSnapshot={handleTrackedEventSnapshot}\n        excludeCalendarEventIds={expectationCalendarEventIds}\n        refreshToken={trackedRefreshToken}\n      />",
    ),
    (
        "function EventCard({ event, status }: { event: EventExpectation; status?: EventStatus }) {",
        "function EventCard({\n  event,\n  status,\n  runtimeStatus,\n}: {\n  event: EventExpectation;\n  status?: EventStatus;\n  runtimeStatus?: string;\n}) {",
    ),
    (
        "  const statusText = !status\n    ? 'Ladataan...'\n    : isStale\n      ? 'Vanhentunut analyysi'\n      : describeStatus(status.run, status.statusError);",
        "  const statusText = isStale\n    ? 'Vanhentunut analyysi'\n    : run\n      ? describeStatus(run, false)\n      : runtimeStatus\n        ? runtimeStatus\n        : !status\n          ? 'Ladataan...'\n          : describeStatus(null, status.statusError);",
    ),
]
for old, new in replacements:
    if old not in text:
        raise SystemExit(f"index anchor missing: {old[:80]!r}")
    text = text.replace(old, new, 1)
index.write_text(text)

component = Path("mobile/src/components/TrackedEventsSection.tsx")
text = component.read_text()
replacements = [
    (
        "type Snapshot = {\n  count: number;\n  calendarEventIds: string[];\n};",
        "type Snapshot = {\n  count: number;\n  calendarEventIds: string[];\n  statusByCalendarEventId: Record<string, string>;\n};",
    ),
    (
        "type Props = {\n  onSnapshot?: (snapshot: Snapshot) => void;\n};",
        "type Props = {\n  onSnapshot?: (snapshot: Snapshot) => void;\n  excludeCalendarEventIds?: ReadonlySet<string>;\n  refreshToken?: number;\n};",
    ),
    (
        "export function TrackedEventsSection({ onSnapshot }: Props) {",
        "export function TrackedEventsSection({\n  onSnapshot,\n  excludeCalendarEventIds,\n  refreshToken = 0,\n}: Props) {",
    ),
    (
        "          calendarEventIds: list\n            .map((event) => event.calendar_event_id)\n            .filter((value): value is string => Boolean(value)),\n        });",
        "          calendarEventIds: list\n            .map((event) => event.calendar_event_id)\n            .filter((value): value is string => Boolean(value)),\n          statusByCalendarEventId: Object.fromEntries(\n            list\n              .filter((event) => Boolean(event.calendar_event_id))\n              .map((event) => [event.calendar_event_id as string, describeTrackedEvent(event).status]),\n          ),\n        });",
    ),
    (
        "    }, [load]),\n  );",
        "    }, [load, refreshToken]),\n  );",
    ),
    (
        "      {events?.map((event) => (\n        <TrackedEventCard key={event.event_id} event={event} />\n      ))}",
        "      {events\n        ?.filter(\n          (event) =>\n            !event.calendar_event_id || !excludeCalendarEventIds?.has(event.calendar_event_id),\n        )\n        .map((event) => <TrackedEventCard key={event.event_id} event={event} />)}",
    ),
]
for old, new in replacements:
    if old not in text:
        raise SystemExit(f"component anchor missing: {old[:80]!r}")
    text = text.replace(old, new, 1)
component.write_text(text)
