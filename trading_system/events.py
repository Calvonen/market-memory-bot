from __future__ import annotations

from trading_system.models import EventActuals, EventComparison, EventExpectation, EventMetricComparison


def compare_event(expectation: EventExpectation, actuals: EventActuals) -> EventComparison:
    """Compare released metrics with pre-event expectations without deriving a trade direction."""
    if expectation.event_id != actuals.event_id:
        raise ValueError("event_id mismatch")
    if expectation.instrument != actuals.instrument:
        raise ValueError("instrument mismatch")

    metric_names = tuple(dict.fromkeys([*expectation.consensus.keys(), *actuals.metrics.keys()]))
    comparisons: list[EventMetricComparison] = []

    for metric in metric_names:
        expected = expectation.consensus.get(metric)
        actual = actuals.metrics.get(metric)
        absolute_delta: float | None = None
        percent_delta: float | None = None

        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            absolute_delta = float(actual) - float(expected)
            if float(expected) != 0:
                percent_delta = (absolute_delta / abs(float(expected))) * 100.0

        comparisons.append(
            EventMetricComparison(
                metric=metric,
                consensus=expected,
                actual=actual,
                absolute_delta=absolute_delta,
                percent_delta=percent_delta,
            )
        )

    missing = tuple(kpi for kpi in expectation.important_kpis if actuals.metrics.get(kpi) is None)

    return EventComparison(
        event_id=expectation.event_id,
        instrument=expectation.instrument,
        metrics=tuple(comparisons),
        missing_important_kpis=missing,
        guidance_summary=actuals.guidance_summary,
        price_reaction_pct=actuals.price_reaction_pct,
    )
