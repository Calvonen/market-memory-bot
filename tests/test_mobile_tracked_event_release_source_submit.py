from __future__ import annotations

import unittest
from pathlib import Path


API_PATH = Path("mobile/src/services/api.ts")
SERVICE_PATH = Path("mobile/src/services/tracked-events.ts")
SCREEN_PATH = Path("mobile/src/app/tracked-events/[eventId]/release.tsx")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n  }\n", start)
    return source[start:end]


class MobileTrackedEventReleaseSourceSubmitTests(unittest.TestCase):
    def test_mobile_reuses_existing_control_auth_for_put(self) -> None:
        api_source = API_PATH.read_text(encoding="utf-8")
        service_source = SERVICE_PATH.read_text(encoding="utf-8")

        self.assertIn("X-MarketAI-Control-Key", api_source)
        self.assertIn("putTrackedEventReleaseSource", service_source)
        self.assertIn("/release-source", service_source)
        self.assertNotIn("X-Admin-Token", service_source)
        self.assertNotIn("EXPO_PUBLIC_MARKETAI_ADMIN", service_source)

    def test_submit_preserves_loaded_results_page_kind_title_and_version(self) -> None:
        screen_source = SCREEN_PATH.read_text(encoding="utf-8")
        service_source = SERVICE_PATH.read_text(encoding="utf-8")

        self.assertIn("source_kind: 'direct_url' | 'results_page'", service_source)
        self.assertIn("setSourceUrl(source.source_url ?? '')", screen_source)
        self.assertIn("setSourceTitle(source.source_title ?? '')", screen_source)
        self.assertIn("releaseSource.active && releaseSource.source_kind", screen_source)
        self.assertIn("? releaseSource.source_kind", screen_source)
        self.assertIn(": 'direct_url'", screen_source)
        self.assertIn("sourceTitle.trim()", screen_source)
        self.assertIn("expected_version: releaseSource.version", screen_source)
        self.assertIn("setReleaseSource(saved)", screen_source)

    def test_failed_submit_resets_edits_to_refreshed_canonical_source(self) -> None:
        screen_source = SCREEN_PATH.read_text(encoding="utf-8")

        self.assertIn("setReleaseSource(currentSource)", screen_source)
        self.assertIn("setSourceUrl(currentSource.source_url ?? '')", screen_source)
        self.assertIn("setSourceTitle(currentSource.source_title ?? '')", screen_source)
        self.assertIn("setError(writeError)", screen_source)

    def test_submit_clears_stale_success_before_validation_returns(self) -> None:
        screen_source = SCREEN_PATH.read_text(encoding="utf-8")
        submit_start = screen_source.index("async function submitReleaseSource()")
        clear_success = screen_source.index("setSubmitMessage(null);", submit_start)
        url_validation = screen_source.index("if (!normalizedUrl.startsWith('https://'))", submit_start)
        actor_validation = screen_source.index("if (!normalizedActor)", submit_start)

        self.assertLess(clear_success, url_validation)
        self.assertLess(clear_success, actor_validation)

    def test_submit_is_serialized_with_paper_permission_approval(self) -> None:
        screen_source = SCREEN_PATH.read_text(encoding="utf-8")
        submit_body = _function_body(screen_source, "  async function submitReleaseSource() {")
        source_card_start = screen_source.index(
            '<Text style={styles.label}>Muuta julkaisulähdettä</Text>'
        )
        source_card_end = screen_source.index(
            '<Text style={styles.label}>Julkaisun käsittely</Text>', source_card_start
        )
        source_card = screen_source[source_card_start:source_card_end]

        self.assertIn(
            "if (!eventId || !releaseSource || submitting || processing || skipping || approvingPermission) return;",
            submit_body,
        )
        self.assertIn(
            "disabled={!releaseSource || !sourceUrl.trim() || !actor.trim() || submitting || processing || skipping || approvingPermission}",
            source_card,
        )
        self.assertIn(
            "(!releaseSource || !sourceUrl.trim() || !actor.trim() || submitting || processing || skipping || approvingPermission) && styles.buttonDisabled",
            source_card,
        )

    def test_submit_records_explicit_actor_in_actor_header(self) -> None:
        service_source = SERVICE_PATH.read_text(encoding="utf-8")
        screen_source = SCREEN_PATH.read_text(encoding="utf-8")

        self.assertIn("actor: string", service_source)
        self.assertIn("'X-MarketAI-Actor': actor", service_source)
        self.assertNotIn("'X-MarketAI-Actor': 'marketai-mobile'", service_source)
        self.assertIn("const [actor, setActor]", screen_source)
        self.assertIn('placeholder="Esim. Marko"', screen_source)
        self.assertIn(
            "Tätä käytetään PAPER-luvan ja muiden tämän näkymän auditoitujen toimenpiteiden yhteydessä.",
            screen_source,
        )
        self.assertIn("const normalizedActor = actor.trim();", screen_source)
        self.assertIn("normalizedActor,", screen_source)

    def test_paper_approval_uses_shared_audited_actor_input(self) -> None:
        screen_source = SCREEN_PATH.read_text(encoding="utf-8")
        approval_body = _function_body(
            screen_source,
            "  async function approvePaperPermission(expectedVersion: number, positionCapUsd: number) {",
        )

        self.assertIn("const normalizedActor = actor.trim();", approval_body)
        self.assertIn(
            "const approved = await approveTrackedEventPaperPermission(\n"
            "        submittedEventId,\n"
            "        normalizedActor,",
            approval_body,
        )

    def test_submit_does_not_start_ingestion_or_create_trading_task(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8") + SERVICE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("/ingest", source)
        self.assertNotIn("/retry", source)
        self.assertNotIn("trading-task", source)
        self.assertNotIn("paper-run", source)


if __name__ == "__main__":
    unittest.main()
