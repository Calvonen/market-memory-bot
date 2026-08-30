import unittest
from pathlib import Path


SCANNER = Path("mobile/src/app/(tabs)/scanner.tsx")
EDITOR = Path("mobile/src/components/tracking-profile-editor.tsx")


class MobileTrackingProfileEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scanner = SCANNER.read_text(encoding="utf-8")
        cls.editor = EDITOR.read_text(encoding="utf-8")

    def test_editor_uses_only_canonical_tracking_profile_service(self) -> None:
        self.assertIn("getTrackingProfiles", self.editor)
        self.assertIn("setTrackingProfile", self.editor)
        self.assertIn("from '@/services/tracking-profiles';", self.editor)
        self.assertNotIn("fetch(", self.editor)
        self.assertNotIn("apiPut", self.editor)
        self.assertNotIn("apiControlPost", self.editor)

    def test_editor_exposes_only_initial_profile_types(self) -> None:
        for profile_type, label in (
            ("earnings", "Tulosjulkaisut"),
            ("trend", "Trendi"),
            ("future_tech", "Future Tech"),
        ):
            self.assertIn(f"type: '{profile_type}'", self.editor)
            self.assertIn(f"label: '{label}'", self.editor)
        self.assertNotIn("catalyst", self.editor)
        self.assertNotIn("anomaly", self.editor)

    def test_editor_reads_and_writes_by_canonical_tracked_instrument_id(self) -> None:
        self.assertIn("getTrackingProfiles(trackedInstrumentId)", self.editor)
        self.assertIn("const saveInstrumentId = trackedInstrumentId;", self.editor)
        self.assertIn("saveInstrumentId,\n        saveProfileType,", self.editor)
        self.assertIn("const PROFILE_ACTOR = 'mobile-tracking-profile';", self.editor)

    def test_scanner_opens_editor_only_for_persisted_tracked_row(self) -> None:
        self.assertIn("const trackedInstrument = trackedInstruments.find", self.scanner)
        self.assertIn("trackedInstrument?.id === profileEditorId", self.scanner)
        self.assertIn("<TrackingProfileEditor trackedInstrumentId={trackedInstrument.id} />", self.scanner)
        self.assertIn("Muokkaa seurantaprofiileja", self.scanner)

    def test_editor_supports_enable_disable_and_specs(self) -> None:
        self.assertIn("setEnabled((current) => !current)", self.editor)
        self.assertIn("value={specs}", self.editor)
        self.assertIn("onChangeText={setSpecs}", self.editor)
        self.assertIn("{ specs, enabled }", self.editor)
        self.assertIn("Tallenna profiili", self.editor)

    def test_editor_fails_closed_until_canonical_state_loads(self) -> None:
        self.assertIn("const canonicalStateReady = loadedInstrumentId === trackedInstrumentId;", self.editor)
        self.assertIn("if (!canonicalStateReady || saving) return;", self.editor)
        self.assertIn("disabled={saving || !canonicalStateReady}", self.editor)
        self.assertIn("editable={!saving && canonicalStateReady}", self.editor)
        self.assertIn("setLoadedInstrumentId(trackedInstrumentId);", self.editor)

    def test_editor_locks_profile_selection_while_save_is_in_flight(self) -> None:
        self.assertIn("if (saving || !canonicalStateReady) return;", self.editor)
        self.assertIn("const saveProfileType = selectedType;", self.editor)
        self.assertIn("selectedTypeRef.current === saveProfileType", self.editor)

    def test_instrument_change_starts_fresh_editor_generation_and_unlocks_save_state(self) -> None:
        self.assertIn("const editorGenerationRef = useRef(0);", self.editor)
        self.assertIn("const generation = ++editorGenerationRef.current;", self.editor)
        self.assertIn("setSaving(false);", self.editor)
        self.assertIn("setLoadedInstrumentId(null);", self.editor)
        self.assertIn("setLoading(true);", self.editor)

    def test_save_completion_is_bound_to_exact_editor_generation(self) -> None:
        self.assertIn("const saveGeneration = editorGenerationRef.current;", self.editor)
        guard = "!mountedRef.current || editorGenerationRef.current !== saveGeneration"
        self.assertGreaterEqual(self.editor.count(guard), 2)
        self.assertIn(
            "mountedRef.current && editorGenerationRef.current === saveGeneration",
            self.editor,
        )
        self.assertNotIn("activeInstrumentIdRef", self.editor)

    def test_profile_ui_does_not_create_events_or_trading_state(self) -> None:
        source = (self.scanner + self.editor).lower()
        for forbidden in (
            "tracked-events",
            "calendar/",
            "trading-tasks",
            "strategyengine",
            "riskengine",
            "broker",
            "paper_order",
            "live-execution",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
