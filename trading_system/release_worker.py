from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass

from trading_system.ai_event_analyzer import OpenAIEventAnalyzer
from trading_system.release_ingestion import HaysResultsCentreProvider, OfficialReleaseProvider
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository


@dataclass(frozen=True)
class IngestionResult:
    status: str
    source_document_id: str | None = None
    analysis_id: str | None = None
    message: str | None = None


class EventReleaseMonitor:
    def __init__(
        self,
        *,
        expectation_repository: SupabaseEventExpectationRepository,
        release_repository: SupabaseReleaseRepository,
        analyzer: OpenAIEventAnalyzer,
        provider: OfficialReleaseProvider,
    ) -> None:
        self.expectations = expectation_repository
        self.releases = release_repository
        self.analyzer = analyzer
        self.provider = provider

    def run_once(self, event_id: str) -> IngestionResult:
        expectation = self.expectations.get(event_id)
        if expectation is None:
            raise ValueError(f"Unknown event: {event_id}")

        try:
            document = self.provider.discover(event_id)
            if document is None:
                self.releases.record_run(
                    event_id=event_id,
                    provider=self.provider.name,
                    status="no_release",
                )
                return IngestionResult(status="no_release")

            existing = self.releases.find_document(event_id, document.content_sha256)
            stored = existing or self.releases.save_document(document)
            document_id = str(stored["id"])

            analysis = self.analyzer.analyze(expectation, document)
            saved_analysis = self.releases.save_analysis(
                event_id=event_id,
                source_document_id=document_id,
                expectation_version=expectation.version,
                analysis=analysis,
            )
            self.releases.record_run(
                event_id=event_id,
                provider=self.provider.name,
                status="analyzed",
                source_url=document.source_url,
                source_document_id=document_id,
            )
            return IngestionResult(
                status="analyzed",
                source_document_id=document_id,
                analysis_id=str(saved_analysis.get("id")) if saved_analysis.get("id") else None,
            )
        except Exception as exc:
            self.releases.record_run(
                event_id=event_id,
                provider=self.provider.name,
                status="error",
                error_message=str(exc)[:2000],
            )
            raise


def build_hays_monitor() -> EventReleaseMonitor:
    return EventReleaseMonitor(
        expectation_repository=SupabaseEventExpectationRepository.from_env(),
        release_repository=SupabaseReleaseRepository.from_env(),
        analyzer=OpenAIEventAnalyzer(),
        provider=HaysResultsCentreProvider(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor an official results release")
    parser.add_argument("--event-id", default="hays-fy2026-results")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.environ.get("MARKETAI_RELEASE_POLL_SECONDS", "300")),
    )
    args = parser.parse_args()

    monitor = build_hays_monitor()
    while True:
        result = monitor.run_once(args.event_id)
        print(f"{args.event_id}: {result.status}", flush=True)
        if args.once or result.status == "analyzed":
            return
        time.sleep(max(60, args.interval_seconds))


if __name__ == "__main__":
    main()
