from datetime import date

from trading_system.models import EventExpectation


HAYS_FY2026 = EventExpectation(
    event_id="hays-fy2026-results",
    instrument="HAS.L",
    event_name="Hays plc FY2026 results",
    scheduled_date=date(2026, 8, 20),
    consensus={
        # Hays company-compiled analyst consensus, last updated 1 July 2026.
        "fy26_net_fees_gbp_m": 902.3,
        "fy26_net_fees_range_low_gbp_m": 893.0,
        "fy26_net_fees_range_high_gbp_m": 911.0,
        "fy26_operating_profit_pre_exceptional_gbp_m": 43.5,
        "fy26_operating_profit_range_low_gbp_m": 37.0,
        "fy26_operating_profit_range_high_gbp_m": 46.0,
        "fy26_eps_p": 1.09,
        "fy26_eps_range_low_p": 0.80,
        "fy26_eps_range_high_p": 1.40,
        "fy27_net_fees_gbp_m": 897.5,
        "fy27_net_fees_range_low_gbp_m": 868.0,
        "fy27_net_fees_range_high_gbp_m": 942.0,
        "fy27_operating_profit_pre_exceptional_gbp_m": 55.6,
        "fy27_operating_profit_range_low_gbp_m": 47.8,
        "fy27_operating_profit_range_high_gbp_m": 64.0,
        "fy27_eps_p": 1.74,
        "fy27_eps_range_low_p": 1.25,
        "fy27_eps_range_high_p": 2.40,
    },
    important_kpis=(
        "fy26_operating_profit_pre_exceptional_gbp_m",
        "fy26_net_fees_gbp_m",
        "fy27_operating_profit_pre_exceptional_gbp_m",
        "fy27_net_fees_gbp_m",
        "net_cash_or_debt_gbp_m",
        "guidance",
        "structural_cost_savings",
        "germany_net_fee_trend",
        "uk_ireland_net_fee_trend",
        "temp_contracting_trend",
        "permanent_trend",
    ),
    bull_case=(
        "FY26 pre-exceptional operating profit lands at or above the top of the £37-46m range.",
        "FY27 outlook is clearly above the £55.6m operating-profit consensus or implies meaningful upgrades.",
        "Management signals improving net-fee trends, especially in Germany and Temp & Contracting.",
        "New structural cost actions improve medium-term margins without materially weakening growth capacity.",
        "Initial price reaction confirms the positive catalyst rather than fading it immediately.",
    ),
    base_case=(
        "FY26 operating profit is near the top of the guided range, broadly as pre-announced in July.",
        "FY27 outlook is broadly consistent with the current £55.6m operating-profit consensus.",
        "Recruitment markets remain challenging with Temp & Contracting more resilient than Permanent.",
        "Cost-saving and strategy announcements are credible but do not materially change near-term expectations.",
        "Price reaction is contained because much of the FY26 improvement was already priced after the Q4 update.",
    ),
    bear_case=(
        "FY26 operating profit fails to reach the top end that management indicated in July.",
        "FY27 guidance is materially below the £55.6m operating-profit consensus or implies estimate cuts.",
        "Germany, UK&I or Permanent recruitment deteriorates further versus the Q4 trend.",
        "Restructuring costs or cash effects are materially worse than expected without sufficient future benefit.",
        "Negative price reaction is confirmed by weak technicals and does not quickly reverse.",
    ),
    triggers={
        # These are strategy thresholds, not analyst-consensus facts. They are intentionally
        # stored separately so they can later be edited/versioned in Supabase.
        "bull_fy27_operating_profit_gbp_m": 60.0,
        "bear_fy27_operating_profit_gbp_m": 50.0,
        "bull_initial_price_reaction_pct": 5.0,
        "bear_initial_price_reaction_pct": -5.0,
        "minimum_strategy_confidence": 60,
    },
    invalidation_conditions=(
        "Do not trade from a single consensus miss or beat alone.",
        "Do not treat FY26 operating profit at the top of the range as a fresh catalyst by itself; it was pre-guided on 10 July 2026.",
        "Require FY27 outlook/strategy, price reaction, technical state and market-memory context before proposal creation.",
        "If guidance direction and price reaction materially conflict, prefer NO_TRADE until the conflict resolves.",
    ),
    source_name="Hays plc Analysts' Consensus",
    source_url="https://www.haysplc.com/investors/analysts-consensus",
    source_as_of=date(2026, 7, 1),
    version=1,
)
