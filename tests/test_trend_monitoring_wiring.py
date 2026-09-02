import os
import unittest
from unittest.mock import Mock, patch

from trading_system.trend_monitoring_wiring import (
    DEFAULT_TREND_REFRESH_SECONDS,
    _observe_target_snapshot_safely,
    build_trend_monitoring_service_from_env,
    trend_refresh_interval_seconds,
)


class TrendMonitoringWiringTests(unittest.TestCase):
    def test_refresh_interval_defaults_to_sixty_seconds(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(trend_refresh_interval_seconds(), DEFAULT_TREND_REFRESH_SECONDS)

    def test_refresh_interval_rejects_invalid_values(self) -> None:
        for value in ("", "0", "-1", "nan", "inf", "not-a-number"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"MARKETAI_TREND_REFRESH_SECONDS": value},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "must be a positive number"):
                    trend_refresh_interval_seconds()

    def test_target_snapshot_diagnostic_failure_is_contained(self) -> None:
        targets = Mock(name="targets")
        observer = Mock(side_effect=BrokenPipeError("journal pipe closed"))

        _observe_target_snapshot_safely(observer, targets)

        observer.assert_called_once_with(targets)

    @patch("trading_system.trend_monitoring_wiring.stream_supervised_trend_monitoring")
    @patch("trading_system.trend_monitoring_wiring.TrendMonitoringSupervisor")
    @patch("trading_system.trend_monitoring_wiring.TrackedCandlePipeline")
    @patch("trading_system.trend_monitoring_wiring.TrendMonitoringRuntime")
    @patch("trading_system.trend_monitoring_wiring.SupabaseTrackedInstrumentProfileRegistry")
    @patch("trading_system.trend_monitoring_wiring.SupabaseTrackedInstrumentRegistry")
    @patch("trading_system.trend_monitoring_wiring.EtoroInstrumentResolver")
    @patch("trading_system.trend_monitoring_wiring.EtoroMarketDataProvider")
    def test_from_env_wires_only_existing_canonical_boundaries(
        self,
        provider_type,
        resolver_type,
        tracked_registry_type,
        profile_registry_type,
        runtime_type,
        pipeline_type,
        supervisor_type,
        service_factory,
    ) -> None:
        provider = Mock(name="provider")
        resolver = Mock(name="resolver")
        tracked_reader = Mock(name="tracked_reader")
        profile_reader = Mock(name="profile_reader")
        runtime = Mock(name="runtime")
        pipeline = Mock(name="pipeline")
        supervisor = Mock(name="supervisor")
        service = Mock(name="service")

        provider_type.from_env.return_value = provider
        resolver_type.return_value = resolver
        tracked_registry_type.from_env.return_value = tracked_reader
        profile_registry_type.from_env.return_value = profile_reader
        runtime_type.return_value = runtime
        pipeline_type.return_value = pipeline
        supervisor_type.return_value = supervisor
        service_factory.return_value = service

        with patch.dict(
            os.environ,
            {"MARKETAI_TREND_REFRESH_SECONDS": "45"},
            clear=True,
        ):
            result = build_trend_monitoring_service_from_env()

        self.assertIs(result, service)
        provider_type.from_env.assert_called_once_with()
        resolver_type.assert_called_once_with(provider)
        tracked_registry_type.from_env.assert_called_once_with()
        profile_registry_type.from_env.assert_called_once_with()
        runtime_type.assert_called_once_with()
        pipeline_type.assert_called_once_with()
        supervisor_type.assert_called_once()

        supervisor_kwargs = supervisor_type.call_args.kwargs
        self.assertIs(supervisor_kwargs["runtime"], runtime)
        self.assertTrue(callable(supervisor_kwargs["select_targets"]))

        service_factory.assert_called_once()
        service_kwargs = service_factory.call_args.kwargs
        self.assertIs(service_kwargs["supervisor"], supervisor)
        self.assertIs(service_kwargs["candle_pipeline"], pipeline)
        self.assertTrue(callable(service_kwargs["stream_factory"]))
        self.assertEqual(service_kwargs["refresh_interval_seconds"], 45.0)


if __name__ == "__main__":
    unittest.main()
