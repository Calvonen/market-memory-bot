from pathlib import Path

home = Path("mobile/src/app/(tabs)/index.tsx")
text = home.read_text(encoding="utf-8")
replacements = [
    (
        "  const [trackedRefreshToken, setTrackedRefreshToken] = useState(0);",
        "  const [trackedRefreshToken, setTrackedRefreshToken] = useState(0);\n  const nextTrackedRefreshToken = useRef(0);\n  const trackedRefreshWaiters = useRef(new Map<number, () => void>());",
    ),
    (
        "  const onRefresh = useCallback(async () => {\n    setRefreshing(true);\n    setTrackedRefreshToken((value) => value + 1);\n    await loadEvents();\n    setRefreshing(false);\n  }, [loadEvents]);",
        "  const handleTrackedRefreshSettled = useCallback((token: number) => {\n    const resolve = trackedRefreshWaiters.current.get(token);\n    if (!resolve) return;\n    trackedRefreshWaiters.current.delete(token);\n    resolve();\n  }, []);\n\n  const onRefresh = useCallback(async () => {\n    setRefreshing(true);\n    const token = ++nextTrackedRefreshToken.current;\n    const trackedRefresh = new Promise<void>((resolve) => {\n      trackedRefreshWaiters.current.set(token, resolve);\n    });\n    setTrackedRefreshToken(token);\n    await loadEvents();\n    await trackedRefresh;\n    setRefreshing(false);\n  }, [loadEvents]);",
    ),
    (
        "        refreshToken={trackedRefreshToken}\n      />",
        "        refreshToken={trackedRefreshToken}\n        onRefreshSettled={handleTrackedRefreshSettled}\n      />",
    ),
]
for old, new in replacements:
    if old not in text:
        raise SystemExit(f"home anchor missing: {old[:100]!r}")
    text = text.replace(old, new, 1)
home.write_text(text, encoding="utf-8")

component = Path("mobile/src/components/TrackedEventsSection.tsx")
text = component.read_text(encoding="utf-8")
replacements = [
    (
        "  refreshToken?: number;\n};",
        "  refreshToken?: number;\n  onRefreshSettled?: (token: number) => void;\n};",
    ),
    (
        "  refreshToken = 0,\n}: Props) {",
        "  refreshToken = 0,\n  onRefreshSettled,\n}: Props) {",
    ),
    (
        "      .catch((err) => {\n        if (loadId !== latestLoadId.current) return;\n        setError(err instanceof Error ? err.message : 'Seurantatietoja ei juuri nyt saatu haettua.');\n      });",
        "      .catch((err) => {\n        if (loadId !== latestLoadId.current) return;\n        setError(err instanceof Error ? err.message : 'Seurantatietoja ei juuri nyt saatu haettua.');\n      })\n      .finally(() => {\n        if (loadId !== latestLoadId.current) return;\n        if (refreshToken > 0) onRefreshSettled?.(refreshToken);\n      });",
    ),
    (
        "  }, [onSnapshot]);",
        "  }, [onRefreshSettled, onSnapshot, refreshToken]);",
    ),
    (
        "    }, [load, refreshToken]),",
        "    }, [load]),",
    ),
    (
        ".map((event) => <TrackedEventCard key={event.event_id} event={event} />)}",
        ".map((event) => (\n          <TrackedEventCard key={`${event.event_id}:${refreshToken}`} event={event} />\n        ))}",
    ),
]
for old, new in replacements:
    if old not in text:
        raise SystemExit(f"component anchor missing: {old[:100]!r}")
    text = text.replace(old, new, 1)
component.write_text(text, encoding="utf-8")

test = Path("tests/test_mobile_source.py")
text = test.read_text(encoding="utf-8")
old = "        self.assertIn(\"!status\\n    ? 'Ladataan...'\", self.home_source)"
new = "        card_start = self.home_source.index(\"function EventCard(\")\n        card_end = self.home_source.index(\"const scheduled =\", card_start)\n        loading_fallback = self.home_source[card_start:card_end]\n        self.assertIn(\"!status\", loading_fallback)\n        self.assertIn(\"? 'Ladataan...'\", loading_fallback)"
if old not in text:
    raise SystemExit("test loading assertion anchor missing")
text = text.replace(old, new, 1)

insert_anchor = "    # -- event card navigates to the detail route with the event id --------\n"
extra_tests = '''    def test_home_refresh_waits_for_persistent_tracked_events_to_settle(self) -> None:\n        # Pull-to-refresh may keep the established Promise.race policy for\n        # expectation/calendar sources so one hung source cannot wedge the\n        # screen, but the newly-triggered persistent tracked-event refresh\n        # must settle before the spinner is cleared.\n        refresh_start = self.home_source.index("const onRefresh = useCallback(async () => {")\n        refresh_end = self.home_source.index("}, [loadEvents]);", refresh_start)\n        refresh_body = self.home_source[refresh_start:refresh_end]\n        self.assertIn("const trackedRefresh = new Promise<void>", refresh_body)\n        self.assertIn("setTrackedRefreshToken(token);", refresh_body)\n        self.assertIn("await loadEvents();", refresh_body)\n        self.assertIn("await trackedRefresh;", refresh_body)\n        self.assertLess(refresh_body.index("await trackedRefresh;"), refresh_body.index("setRefreshing(false);"))\n        self.assertIn("onRefreshSettled={handleTrackedRefreshSettled}", self.home_source)\n\n    def test_home_refresh_reloads_latest_reaction_for_surviving_tracked_cards(self) -> None:\n        component = Path("mobile/src/components/TrackedEventsSection.tsx").read_text(encoding="utf-8")\n        # A refresh generation changes the card key, remounting a surviving\n        # event card even when event_id itself did not change. Its existing\n        # focus effect therefore issues a fresh latest-reaction request.\n        self.assertIn("key={`${event.event_id}:${refreshToken}`}", component)\n        self.assertIn("getTrackedEventLatestReaction(event.event_id)", component)\n        self.assertIn("if (refreshToken > 0) onRefreshSettled?.(refreshToken);", component)\n\n'''
if insert_anchor not in text:
    raise SystemExit("test insertion anchor missing")
text = text.replace(insert_anchor, extra_tests + insert_anchor, 1)
test.write_text(text, encoding="utf-8")
