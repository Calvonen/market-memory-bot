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
        ".map((event) => <TrackedEventCard key={event.event_id} event={event} />)}",
        ".map((event) => (\n          <TrackedEventCard key={event.event_id} event={event} refreshToken={refreshToken} />\n        ))}",
    ),
    (
        "export function TrackedEventCard({ event }: { event: TrackedMarketEvent }) {",
        "export function TrackedEventCard({\n  event,\n  refreshToken = 0,\n}: {\n  event: TrackedMarketEvent;\n  refreshToken?: number;\n}) {",
    ),
    (
        "    }, [event.event_id]),",
        "    }, [event.event_id, refreshToken]),",
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
test.write_text(text, encoding="utf-8")
