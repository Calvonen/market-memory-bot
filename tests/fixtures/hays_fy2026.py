from datetime import date

from trading_system.models import EventExpectation


HAYS_FY2026 = EventExpectation(
    event_id="hays-fy2026-results",
    instrument="HAS.L",
    event_name="Hays plc FY2026 results",
    scheduled_date=date(2026, 8, 20),
    consensus={},
    important_kpis=(
        "operating_profit_pre_exceptional",
        "net_fees",
        "net_cash_or_debt",
        "guidance",
        "regional_performance",
    ),
    bull_case=(),
    base_case=(),
    bear_case=(),
    triggers={},
    invalidation_conditions=(
        "Do not trade from a single consensus miss alone.",
        "Require price reaction and full event analysis before proposal creation.",
    ),
)
