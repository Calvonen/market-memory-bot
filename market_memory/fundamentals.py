from __future__ import annotations

import pandas as pd
import yfinance as yf


METRIC_LABELS = {
    "Total Revenue": "Liikevaihto",
    "Gross Profit": "Gross Profit",
    "Operating Income": "Operating Income / EBIT",
    "EBIT": "Operating Income / EBIT",
    "Net Income": "Net Income",
}


def _first_available_series(financials: pd.DataFrame, keys: list[str]) -> pd.Series:
    for key in keys:
        if key in financials.index:
            return financials.loc[key]
    return pd.Series(dtype="float64")


def _pct_change_from_previous(values: pd.Series) -> pd.Series:
    chronological = values.sort_index()
    change = chronological.pct_change() * 100
    return change.sort_index(ascending=False)


def fetch_quarterly_fundamentals(ticker: str) -> pd.DataFrame:
    financials = yf.Ticker(ticker).quarterly_financials
    if financials is None or financials.empty:
        return pd.DataFrame()

    revenue = _first_available_series(financials, ["Total Revenue"])
    operating_income = _first_available_series(financials, ["Operating Income", "EBIT"])
    net_income = _first_available_series(financials, ["Net Income"])

    quarter_cols = financials.columns
    panel = pd.DataFrame(index=quarter_cols)
    panel["Liikevaihto"] = revenue.reindex(quarter_cols)
    panel["Operating Income / EBIT"] = operating_income.reindex(quarter_cols)
    panel["Net Income"] = net_income.reindex(quarter_cols)

    panel["Liikevaihto muutos %"] = _pct_change_from_previous(panel["Liikevaihto"])
    panel["EBIT muutos %"] = _pct_change_from_previous(panel["Operating Income / EBIT"])
    panel["Net Income muutos %"] = _pct_change_from_previous(panel["Net Income"])

    panel = panel.reset_index().rename(columns={"index": "Kvartaali"})
    quarter_dates = pd.to_datetime(panel["Kvartaali"], errors="coerce")
    panel["Kvartaali"] = quarter_dates.dt.to_period("Q").astype(str)

    ordered_cols = [
        "Kvartaali",
        "Liikevaihto",
        "Liikevaihto muutos %",
        "Operating Income / EBIT",
        "EBIT muutos %",
        "Net Income",
        "Net Income muutos %",
    ]

    panel = panel[ordered_cols]
    panel = panel.head(8)

    for col in ["Liikevaihto", "Operating Income / EBIT", "Net Income"]:
        panel[col] = panel[col].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "-")
    for col in ["Liikevaihto muutos %", "EBIT muutos %", "Net Income muutos %"]:
        panel[col] = panel[col].map(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")

    return panel
