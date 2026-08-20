import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from tests.fixtures.hays_fy2026 import HAYS_FY2026
from trading_system.ai_event_analyzer import AIEventAnalysis, EventAnalysisPayload
from trading_system.event_repository import InMemoryEventExpectationRepository
from trading_system.post_release_paper import PostReleasePaperResult
from trading_system.release_ingestion import HaysResultsCentreProvider, ReleaseDocument
from trading_system.release_worker import (
    EventReleaseMonitor,
    build_hays_monitor,
    build_paper_portfolio_from_env,
    hays_confirmation_deadline,
    run_paper_confirmation_loop,
)


class ReleaseWorkerPaperConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = EventAnalysisPayload(
            metrics=[
                {
                    "name": "fy27_operating_profit_pre_exceptional_gbp_m",
                    "value": 61.0,
                    "unit": "GBP m",
                }
            ],
            guidance_summary="FY27 outlook above consensus",
            management_summary="management summary",
            catalyst_direction="BULLISH",
            catalyst_score_0_25=18,
            fundamental_direction="BULLISH",
            fundamental_score_0_35=24,
            key_positive_surprises=[],
            key_negative_surprises=[],
            uncertainties=[],
            invalidation_flags=[],
            evidence_quotes=[],
        )

    def test_confirmation_loop_waits_without_reanalyzing_then_stops_after_paper_execution(
        self,
    ) -> None:
        results = iter(
            [
                PostReleasePaperResult(
                    "waiting_confirmation", "no event-day market bar yet"
                ),
                PostReleasePaperResult(
                    "paper_executed", "LONG 100 HAS.L FILLED_SIMULATED"
                ),
            ]
        )
        runner_calls = []
        sleeps = []

        def runner(**kwargs):
            runner_calls.append(kwargs)
            return next(results)

        result = run_paper_confirmation_loop(
            event_id="hays-fy2026-results",
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            interval_seconds=300,
            once=False,
            runner=runner,
            sleeper=sleeps.append,
            clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
        )

        self.assertEqual(result.status, "paper_executed")
        self.assertEqual(len(runner_calls), 2)
        self.assertEqual(sleeps, [300])
        self.assertIs(runner_calls[0]["analysis"], self.analysis)

    def test_once_mode_runs_only_one_confirmation_attempt(self) -> None:
        calls = []

        def runner(**kwargs):
            calls.append(kwargs)
            return PostReleasePaperResult(
                "waiting_confirmation", "technical confirmation is not aligned"
            )

        result = run_paper_confirmation_loop(
            event_id="hays-fy2026-results",
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            interval_seconds=300,
            once=True,
            runner=runner,
            sleeper=lambda _: self.fail("once mode must not sleep"),
            clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
        )

        self.assertEqual(result.status, "waiting_confirmation")
        self.assertEqual(len(calls), 1)

    def test_paper_portfolio_has_explicit_spread_assumption_and_live_mode_is_not_involved(
        self,
    ) -> None:
        with patch.dict(
            "os.environ",
            {
                "MARKETAI_PAPER_EQUITY": "12000",
                "MARKETAI_PAPER_CASH": "11000",
                "MARKETAI_PAPER_SPREAD_PCT": "0.45",
            },
            clear=False,
        ):
            portfolio = build_paper_portfolio_from_env()

        self.assertEqual(portfolio.equity, 12000.0)
        self.assertEqual(portfolio.cash, 11000.0)
        self.assertEqual(portfolio.spread_pct, 0.45)
        self.assertIsNone(portfolio.volatility_pct)

    def test_restart_after_paper_executed_skips_runner_and_broker(self) -> None:
        class AlreadyExecutedPersistence:
            def get_latest_for_event(self, event_id: str) -> dict[str, Any]:
                return {"status": "paper_executed", "analysis_id": "analysis-1"}

            def save_result(self, **kwargs: Any) -> None:
                raise AssertionError(
                    "must not persist a new result on restart short-circuit"
                )

        def runner(**kwargs: Any) -> PostReleasePaperResult:
            raise AssertionError(
                "runner (Strategy/Risk/PaperBroker) must not run after paper_executed"
            )

        result = run_paper_confirmation_loop(
            event_id="hays-fy2026-results",
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            interval_seconds=300,
            once=False,
            analysis_id="analysis-1",
            persistence=AlreadyExecutedPersistence(),
            runner=runner,
            sleeper=lambda _: self.fail("must not sleep when already paper_executed"),
        )

        self.assertEqual(result.status, "paper_executed")

    def test_hays_deadline_is_lse_close_plus_grace_in_utc(self) -> None:
        deadline = hays_confirmation_deadline(HAYS_FY2026, grace_minutes=15)
        self.assertEqual(deadline, datetime(2026, 8, 20, 15, 45, tzinfo=UTC))

    def test_expired_confirmation_never_calls_runner(self) -> None:
        class WaitingPersistence:
            def __init__(self) -> None:
                self.expired = False

            def get_latest_for_event(self, event_id: str) -> dict[str, Any]:
                return {
                    "status": "waiting_confirmation",
                    "confirmation_deadline_at": "2026-08-20T15:45:00+00:00",
                }

            def expire_waiting(self, **kwargs: Any) -> dict[str, Any]:
                self.expired = True
                return {"status": "expired_no_trade"}

        persistence = WaitingPersistence()
        result = run_paper_confirmation_loop(
            event_id="hays-fy2026-results",
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            interval_seconds=300,
            once=False,
            analysis_id="analysis-1",
            persistence=persistence,
            runner=lambda **_: self.fail(
                "expired confirmation must not enter the pipeline"
            ),
            clock=lambda: datetime(2026, 8, 20, 15, 45, tzinfo=UTC),
        )
        self.assertEqual(result.status, "expired_no_trade")
        self.assertTrue(persistence.expired)

    def test_restart_after_expiry_skips_runner(self) -> None:
        class ExpiredPersistence:
            def get_latest_for_event(self, event_id: str) -> dict[str, Any]:
                return {"status": "expired_no_trade"}

        result = run_paper_confirmation_loop(
            event_id="hays-fy2026-results",
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            interval_seconds=300,
            once=False,
            persistence=ExpiredPersistence(),
            runner=lambda **_: self.fail("terminal expiry must skip the pipeline"),
        )
        self.assertEqual(result.status, "expired_no_trade")

    def test_stale_runner_result_yields_to_atomic_expiry_winner(self) -> None:
        class ExpiryWinsPersistence:
            def get_latest_for_event(self, event_id: str) -> dict[str, Any]:
                return {
                    "status": "waiting_confirmation",
                    "confirmation_deadline_at": "2026-08-20T15:45:00+00:00",
                }

            def save_result(self, **kwargs: Any) -> dict[str, Any]:
                return {
                    "status": "expired_no_trade",
                    "message": "confirmation window expired without a trade",
                    "expired_at": "2026-08-20T15:45:01+00:00",
                }

        result = run_paper_confirmation_loop(
            event_id="hays-fy2026-results",
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            interval_seconds=300,
            once=False,
            source_document_id="document-1",
            analysis_id="analysis-1",
            persistence=ExpiryWinsPersistence(),
            runner=lambda **_: PostReleasePaperResult(
                "paper_executed", "stale paper result"
            ),
            sleeper=lambda _: self.fail("terminal database winner must stop the loop"),
            clock=lambda: datetime(2026, 8, 20, 15, 44, tzinfo=UTC),
        )
        self.assertEqual(result.status, "expired_no_trade")
        self.assertIsNone(result.pipeline)


class _FakeReleaseRepository:
    """In-memory stand-in for SupabaseReleaseRepository's dedupe/upsert semantics."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.analyses: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []

    def find_document(
        self, event_id: str, content_sha256: str
    ) -> dict[str, Any] | None:
        for row in self.documents:
            if row["event_id"] == event_id and row["content_sha256"] == content_sha256:
                return row
        return None

    def save_document(self, document: ReleaseDocument) -> dict[str, Any]:
        existing = self.find_document(document.event_id, document.content_sha256)
        if existing:
            return existing
        row = {
            "id": f"doc-{len(self.documents) + 1}",
            "event_id": document.event_id,
            "content_sha256": document.content_sha256,
            "source_url": document.source_url,
        }
        self.documents.append(row)
        return row

    def find_analysis(
        self, *, event_id: str, source_document_id: str, expectation_version: int
    ) -> dict[str, Any] | None:
        for row in self.analyses:
            if (
                row["event_id"] == event_id
                and row["source_document_id"] == source_document_id
                and row["expectation_version"] == expectation_version
            ):
                return row
        return None

    def save_analysis(
        self,
        *,
        event_id: str,
        source_document_id: str,
        expectation_version: int,
        analysis: AIEventAnalysis,
    ) -> dict[str, Any]:
        row = {
            "id": f"analysis-{len(self.analyses) + 1}",
            "event_id": event_id,
            "source_document_id": source_document_id,
            "expectation_version": expectation_version,
            "provider": analysis.provider,
            "model": analysis.model,
            "analysis": analysis.payload.model_dump(),
            "raw_response": analysis.raw_response,
        }
        self.analyses.append(row)
        return row

    def record_run(
        self,
        *,
        event_id: str,
        provider: str,
        status: str,
        source_url: str | None = None,
        source_document_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.runs.append(
            {
                "event_id": event_id,
                "provider": provider,
                "status": status,
                "error_message": error_message,
            }
        )


class _FakeProvider:
    name = "hays_results_centre"

    def __init__(self, document: ReleaseDocument) -> None:
        self.document = document

    def discover(self, event_id: str) -> ReleaseDocument:
        return self.document


class _NoReleaseProvider:
    name = "hays_results_centre"

    def discover(self, event_id: str) -> None:
        return None


class _CountingAnalyzer:
    def __init__(self, analysis: AIEventAnalysis) -> None:
        self.analysis = analysis
        self.calls = 0

    def analyze(self, expectation: Any, document: ReleaseDocument) -> AIEventAnalysis:
        self.calls += 1
        return self.analysis


class EventReleaseMonitorRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expectations = InMemoryEventExpectationRepository(
            {HAYS_FY2026.event_id: HAYS_FY2026}
        )
        self.document = ReleaseDocument(
            event_id=HAYS_FY2026.event_id,
            source_type="company_results",
            source_url="https://www.haysplc.com/results/fy26",
            source_title="Full-year results for the year ended 30 June 2026",
            raw_text="Official FY26 results text " * 40,
        )
        self.analysis = AIEventAnalysis(
            payload=EventAnalysisPayload(
                metrics=[],
                guidance_summary="FY27 outlook above consensus",
                management_summary="management summary",
                catalyst_direction="BULLISH",
                catalyst_score_0_25=18,
                fundamental_direction="BULLISH",
                fundamental_score_0_35=24,
                key_positive_surprises=[],
                key_negative_surprises=[],
                uncertainties=[],
                invalidation_flags=[],
                evidence_quotes=[],
            ),
            provider="groq",
            model="openai/gpt-oss-120b",
            raw_response="{}",
        )

    def _build_monitor(
        self, analyzer: _CountingAnalyzer, releases: _FakeReleaseRepository
    ) -> EventReleaseMonitor:
        return EventReleaseMonitor(
            expectation_repository=self.expectations,
            release_repository=releases,
            analyzer=analyzer,
            provider=_FakeProvider(self.document),
        )

    def test_restart_after_already_analyzed_document_does_not_call_analyzer_again(
        self,
    ) -> None:
        releases = _FakeReleaseRepository()
        analyzer = _CountingAnalyzer(self.analysis)
        monitor = self._build_monitor(analyzer, releases)

        first = monitor.run_once(HAYS_FY2026.event_id)
        second = monitor.run_once(HAYS_FY2026.event_id)  # simulates a process restart

        self.assertEqual(first.status, "analyzed")
        self.assertEqual(second.status, "analyzed")
        self.assertEqual(
            analyzer.calls, 1, "analyzer must not be called again on restart"
        )

    def test_restart_continues_with_the_same_analysis_id_and_source_document_id(
        self,
    ) -> None:
        releases = _FakeReleaseRepository()
        analyzer = _CountingAnalyzer(self.analysis)
        monitor = self._build_monitor(analyzer, releases)

        first = monitor.run_once(HAYS_FY2026.event_id)
        second = monitor.run_once(HAYS_FY2026.event_id)

        self.assertEqual(first.analysis_id, second.analysis_id)
        self.assertEqual(first.source_document_id, second.source_document_id)
        self.assertEqual(second.analysis, first.analysis)

    def test_same_document_and_expectation_version_never_produces_a_second_analysis_row(
        self,
    ) -> None:
        releases = _FakeReleaseRepository()
        analyzer = _CountingAnalyzer(self.analysis)
        monitor = self._build_monitor(analyzer, releases)

        monitor.run_once(HAYS_FY2026.event_id)
        monitor.run_once(HAYS_FY2026.event_id)
        monitor.run_once(HAYS_FY2026.event_id)

        self.assertEqual(len(releases.analyses), 1)

    def test_provider_fallback_variance_across_restarts_does_not_fork_the_analysis_chain(
        self,
    ) -> None:
        releases = _FakeReleaseRepository()
        groq_analysis = self.analysis
        ollama_analysis = AIEventAnalysis(
            payload=self.analysis.payload,
            provider="ollama",
            model="gpt-oss:20b",
            raw_response="{}",
        )
        analyzer = _CountingAnalyzer(groq_analysis)
        monitor = self._build_monitor(analyzer, releases)
        first = monitor.run_once(HAYS_FY2026.event_id)

        # Simulate a restart where Groq is unavailable and Ollama would be used
        # if the analyzer were called again; it must not be, because a
        # persisted analysis already exists for this document + version.
        analyzer.analysis = ollama_analysis
        second = monitor.run_once(HAYS_FY2026.event_id)

        self.assertEqual(analyzer.calls, 1)
        self.assertEqual(first.analysis_id, second.analysis_id)
        self.assertEqual(len(releases.analyses), 1)
        self.assertEqual(releases.analyses[0]["provider"], "groq")


class EventReleaseMonitorOverdueTests(unittest.TestCase):
    """scheduled_date=2026-08-20, default grace window=8h (see setUp)."""

    def setUp(self) -> None:
        self.expectations = InMemoryEventExpectationRepository(
            {HAYS_FY2026.event_id: HAYS_FY2026}
        )
        self.releases = _FakeReleaseRepository()
        self.analyzer = _CountingAnalyzer(
            AIEventAnalysis(
                payload=EventAnalysisPayload(
                    metrics=[],
                    guidance_summary="",
                    management_summary="",
                    catalyst_direction="NEUTRAL",
                    catalyst_score_0_25=0,
                    fundamental_direction="NEUTRAL",
                    fundamental_score_0_35=0,
                    key_positive_surprises=[],
                    key_negative_surprises=[],
                    uncertainties=[],
                    invalidation_flags=[],
                    evidence_quotes=[],
                ),
                provider="groq",
                model="openai/gpt-oss-120b",
                raw_response="{}",
            )
        )

    def _monitor(self, now: datetime) -> EventReleaseMonitor:
        return EventReleaseMonitor(
            expectation_repository=self.expectations,
            release_repository=self.releases,
            analyzer=self.analyzer,
            provider=_NoReleaseProvider(),
            overdue_grace_hours=8.0,
            clock=lambda: now,
        )

    def test_no_release_before_scheduled_date_is_not_overdue(self) -> None:
        monitor = self._monitor(datetime(2026, 8, 19, 12, 0, tzinfo=UTC))
        result = monitor.run_once(HAYS_FY2026.event_id)

        self.assertEqual(result.status, "no_release")
        self.assertFalse(result.overdue)
        self.assertIsNone(result.message)
        self.assertIsNone(self.releases.runs[-1]["error_message"])

    def test_no_release_on_scheduled_date_within_grace_window_is_not_yet_overdue(
        self,
    ) -> None:
        monitor = self._monitor(datetime(2026, 8, 20, 2, 0, tzinfo=UTC))
        result = monitor.run_once(HAYS_FY2026.event_id)

        self.assertEqual(result.status, "no_release")
        self.assertFalse(result.overdue)

    def test_no_release_past_grace_window_is_flagged_overdue_and_distinguishable_in_audit_log(
        self,
    ) -> None:
        monitor = self._monitor(datetime(2026, 8, 20, 20, 0, tzinfo=UTC))
        result = monitor.run_once(HAYS_FY2026.event_id)

        self.assertEqual(result.status, "no_release")
        self.assertTrue(result.overdue)
        assert result.message is not None
        self.assertIn("overdue", result.message.lower())

        # status column stays a known, schema-safe value; the distinguishing
        # signal rides in error_message instead of inventing a new status.
        last_run = self.releases.runs[-1]
        self.assertEqual(last_run["status"], "no_release")
        assert last_run["error_message"] is not None
        self.assertIn("overdue", last_run["error_message"].lower())

    def test_overdue_never_invents_a_release_or_calls_the_analyzer(self) -> None:
        monitor = self._monitor(datetime(2026, 8, 25, 0, 0, tzinfo=UTC))
        result = monitor.run_once(HAYS_FY2026.event_id)

        self.assertEqual(result.status, "no_release")
        self.assertIsNone(result.analysis)
        self.assertEqual(self.analyzer.calls, 0)
        self.assertEqual(len(self.releases.documents), 0)
        self.assertEqual(len(self.releases.analyses), 0)


class BuildHaysMonitorTests(unittest.TestCase):
    def test_release_url_override_is_threaded_into_the_provider(self) -> None:
        override_url = "https://www.haysplc.com/results/manually-confirmed-release"
        with (
            patch(
                "trading_system.release_worker.SupabaseEventExpectationRepository.from_env",
                return_value=InMemoryEventExpectationRepository(),
            ),
            patch(
                "trading_system.release_worker.SupabaseReleaseRepository.from_env",
                return_value=_FakeReleaseRepository(),
            ),
            patch(
                "trading_system.release_worker.build_default_event_analyzer",
                return_value=_CountingAnalyzer(
                    AIEventAnalysis(
                        payload=EventAnalysisPayload(
                            metrics=[],
                            guidance_summary="",
                            management_summary="",
                            catalyst_direction="NEUTRAL",
                            catalyst_score_0_25=0,
                            fundamental_direction="NEUTRAL",
                            fundamental_score_0_35=0,
                            key_positive_surprises=[],
                            key_negative_surprises=[],
                            uncertainties=[],
                            invalidation_flags=[],
                            evidence_quotes=[],
                        ),
                        provider="groq",
                        model="openai/gpt-oss-120b",
                        raw_response="{}",
                    )
                ),
            ),
        ):
            monitor = build_hays_monitor(release_url_override=override_url)

        self.assertIsInstance(monitor.provider, HaysResultsCentreProvider)
        self.assertEqual(monitor.provider.override_url, override_url)

    def test_no_override_url_leaves_provider_in_auto_discovery_mode(self) -> None:
        with (
            patch(
                "trading_system.release_worker.SupabaseEventExpectationRepository.from_env",
                return_value=InMemoryEventExpectationRepository(),
            ),
            patch(
                "trading_system.release_worker.SupabaseReleaseRepository.from_env",
                return_value=_FakeReleaseRepository(),
            ),
            patch(
                "trading_system.release_worker.build_default_event_analyzer",
                return_value=_CountingAnalyzer(
                    AIEventAnalysis(
                        payload=EventAnalysisPayload(
                            metrics=[],
                            guidance_summary="",
                            management_summary="",
                            catalyst_direction="NEUTRAL",
                            catalyst_score_0_25=0,
                            fundamental_direction="NEUTRAL",
                            fundamental_score_0_35=0,
                            key_positive_surprises=[],
                            key_negative_surprises=[],
                            uncertainties=[],
                            invalidation_flags=[],
                            evidence_quotes=[],
                        ),
                        provider="groq",
                        model="openai/gpt-oss-120b",
                        raw_response="{}",
                    )
                ),
            ),
        ):
            monitor = build_hays_monitor()

        self.assertIsNone(monitor.provider.override_url)


if __name__ == "__main__":
    unittest.main()
