from __future__ import annotations

import unittest
from pathlib import Path


CARD_PATH = Path("mobile/src/components/TrackedEventsSection.tsx")
PAPER_SUMMARY_PATH = Path("mobile/src/components/TrackedEventPaperSummary.tsx")
HOME_PATH = Path("mobile/src/app/(tabs)/index.tsx")


class MobileTrackedEventCardPaperSummaryTests(unittest.TestCase):
    def test_earnings_details_mount_paper_summary_for_merged_home_card(self) -> None:
        card = CARD_PATH.read_text(encoding="utf-8")
        home = HOME_PATH.read_text(encoding="utf-8")

        details_start = card.index("export function TrackedEventDetails")
        details_end = card.index("function TrackedEventWorkflow", details_start)
        details = card[details_start:details_end]
        self.assertIn("event.kind === 'earnings'", details)
        self.assertIn("<TrackedEventPaperSummary", details)
        self.assertIn("eventId={event.event_id}", details)
        self.assertIn("expectationEventId={expectationEventId}", details)

        self.assertIn("<TrackedEventDetails", home)
        self.assertIn("excludeCalendarEventIds={expectationCalendarEventIds}", home)

    def test_paper_summary_reads_permission_and_canonical_paper_status(self) -> None:
        source = PAPER_SUMMARY_PATH.read_text(encoding="utf-8")

        self.assertIn("getTrackedEventPaperPermission(eventId)", source)
        self.assertIn("getPaperStatus(expectationEventId)", source)
        self.assertIn("PAPER-lupa ja asetukset", source)
        self.assertIn("pathname: '/tracked-events/[eventId]/release'", source)
        self.assertIn("params: { eventId }", source)
        self.assertIn("pressEvent.stopPropagation()", source)

    def test_paper_summary_exposes_permission_and_execution_lifecycle(self) -> None:
        source = PAPER_SUMMARY_PATH.read_text(encoding="utf-8")

        self.assertIn("Lupa hyväksytty", source)
        self.assertIn("Lupa vanhentunut", source)
        self.assertIn("Lupa odottaa hyväksyntää", source)
        self.assertIn("Lupa puuttuu", source)
        self.assertIn("permission.state === 'pending'", source)
        self.assertIn("Kauppa: ei vielä käsitelty.", source)
        self.assertIn("Kauppa: odottaa vahvistuksia", source)
        self.assertIn("Kauppa: PAPER-kauppa toteutettu", source)
        self.assertIn("Kauppa: NO TRADE", source)

    def test_paper_executed_uses_canonical_broker_outcome_statuses(self) -> None:
        source = PAPER_SUMMARY_PATH.read_text(encoding="utf-8")

        for status in (
            "FILLED",
            "FILLED_SIMULATED",
            "ETORO_DEMO_FILLED",
            "EXECUTED",
            "COMPLETE",
            "COMPLETED",
            "ACCEPTED",
            "ETORO_DEMO_ACCEPTED",
            "PENDING",
            "OPEN",
            "SUBMITTED",
            "REJECTED",
            "CANCELLED",
            "CANCELED",
            "FAILED",
            "ERROR",
        ):
            self.assertIn(status, source)

        self.assertIn("export function classifyPaperBrokerStatus", source)
        self.assertIn("PAPER-toimeksianto odottaa toteutusta", source)
        self.assertIn("PAPER-toimeksianto hylätty tai peruttu", source)
        self.assertIn("PAPER-toimeksianto epäonnistui", source)
        self.assertIn("brokerin toteutustila puuttuu", source)
        self.assertIn("brokerin tila varmistamatta", source)
        self.assertIn("brokerOutcome(order?.status)", source)

    def test_merged_home_headline_uses_same_broker_outcome_classifier(self) -> None:
        home = HOME_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "import { classifyPaperBrokerStatus } from '@/components/TrackedEventPaperSummary';",
            home,
        )
        self.assertIn("classifyPaperBrokerStatus(run.paper_order?.status)", home)
        self.assertIn("Paperitoimeksianto odottaa toteutusta", home)
        self.assertIn("Paperitoimeksianto hylätty tai peruttu", home)
        self.assertIn("Paperitoimeksianto epäonnistui", home)
        self.assertIn("Brokerin toteutustila puuttuu", home)
        self.assertIn("Brokerin tila varmistamatta", home)
        paper_executed_start = home.index("case 'paper_executed':")
        paper_executed_end = home.index("default:", paper_executed_start)
        paper_executed_branch = home[paper_executed_start:paper_executed_end]
        self.assertNotIn("return 'Paperikauppa toteutettu';\n    default", paper_executed_branch)

    def test_execution_status_is_bound_to_current_expectation_version(self) -> None:
        source = PAPER_SUMMARY_PATH.read_text(encoding="utf-8")

        self.assertIn("run.expectation_version !== status.expectation_version", source)
        self.assertIn("Kaupan aiempi tila on expectation v", source)
        self.assertIn("nykyinen v", source)

    def test_latest_reaction_percent_rounds_decimal_string_to_two_places(self) -> None:
        card = CARD_PATH.read_text(encoding="utf-8")

        self.assertIn("formatReactionPercent(reaction.return_pct)", card)
        self.assertIn("/^([+-]?)(\\d+)(?:\\.(\\d*))?$/", card)
        self.assertIn("const roundingDigit = fraction.length > 2 ? fraction[2] : '0'", card)
        self.assertIn("incrementUnsignedInteger(integerPart)", card)
        self.assertNotIn("const parsed = Number(value)", card)
        self.assertNotIn("parsed.toFixed(2)", card)
        self.assertNotIn("Muutos {reaction.return_pct} %", card)


if __name__ == "__main__":
    unittest.main()