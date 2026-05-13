from __future__ import annotations

import pandas as pd
import streamlit as st

from market_memory.config import SECTOR_SETTINGS
from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.pivots import detect_pivots
from market_memory.similarity import MatchResult, find_best_matches
from market_memory.visualization import plot_overlay


st.set_page_config(page_title="Market Memory", page_icon="📈", layout="wide")


@st.cache_data(show_spinner=False)
def run_analysis(
    ticker: str,
    sector: str,
    similarity_alert: float,
    pivot_mode: str,
    period: str = "5y",
) -> tuple[pd.DataFrame, list[MatchResult]]:
    raw = fetch_ohlcv(ticker=ticker, period=period)
    enriched = add_indicators(raw)
    settings = SECTOR_SETTINGS[sector]

    pivots = detect_pivots(
        enriched,
        window=5,
        rsi_low=settings.rsi_low,
        rsi_high=settings.rsi_high,
        dip_threshold_pct=settings.dip_threshold_pct,
        peak_threshold_pct=settings.peak_threshold_pct,
        min_atr_pct=settings.min_atr_pct,
        max_atr_pct=settings.max_atr_pct,
        pivot_mode=pivot_mode,
    )
    matches = find_best_matches(enriched, pivots, current_window=15, top_k=8)
    return enriched, matches


def build_matches_table(matches: list[MatchResult], ticker: str, threshold: float) -> pd.DataFrame:
    rows: list[dict[str, str | float]] = []
    for match in matches:
        alert_text = "REBOUND WATCH" if match.pivot.pivot_type == "bottom" else "SHORT WATCH"
        status = "ALERT" if match.score >= threshold else "INFO"
        rows.append(
            {
                "Ticker": ticker,
                "Similarity": round(match.score, 3),
                "price similarity": round(match.price_similarity, 3),
                "RSI similarity": round(match.rsi_similarity, 3),
                "volume similarity": round(match.volume_similarity, 3),
                "volatility similarity": round(match.volatility_similarity, 3),
                "trend similarity": round(match.trend_similarity, 3),
                "Pivot date": match.pivot.index.date().isoformat(),
                "Pivot type": match.pivot.pivot_type,
                "Alert status": f"{status}: {alert_text if status == 'ALERT' else 'no alert'}",
                "Historical return after pivot": round(match.historical_return_after_pivot, 2),
            }
        )
    return pd.DataFrame(rows)


st.title("Market Memory")
st.caption("Historiallisten markkinatilanteiden vertailu nykyiseen rakenteeseen")

with st.sidebar:
    st.subheader("Asetukset")
    ticker = st.text_input("Ticker", value="AAPL", max_chars=12).strip().upper()
    sector = st.selectbox("Sektori", options=list(SECTOR_SETTINGS.keys()), index=0)
    similarity_alert = st.slider("Similarity-alert", min_value=0.50, max_value=0.99, value=0.75, step=0.01)
    pivot_mode = st.radio("Pivot mode", options=["all", "bottom", "peak"], horizontal=True)
    run = st.button("Suorita analyysi", type="primary", use_container_width=True)

if run:
    if not ticker:
        st.error("Syötä ticker ennen analyysiä.")
    else:
        with st.spinner("Haetaan dataa ja lasketaan osumat..."):
            try:
                enriched, matches = run_analysis(
                    ticker=ticker,
                    sector=sector,
                    similarity_alert=similarity_alert,
                    pivot_mode=pivot_mode,
                )
            except Exception as exc:
                st.exception(exc)
            else:
                st.success(f"Analyysi valmis: {ticker} | sektori: {sector} | moodi: {pivot_mode}")

                if not matches:
                    st.warning("Ei historiallisia osumia valituilla ehdoilla.")
                else:
                    top_match = matches[0]
                    is_alert = top_match.score >= similarity_alert
                    if is_alert:
                        kind = "REBOUND WATCH" if top_match.pivot.pivot_type == "bottom" else "SHORT WATCH"
                        st.error(f"Alert status: ALERT — {kind} (top score {top_match.score:.3f})")
                    else:
                        st.info(f"Alert status: INFO — no alert (top score {top_match.score:.3f})")

                    c1, c2 = st.columns([1.2, 1])
                    with c1:
                        st.subheader("Parhaat historialliset osumat")
                        table = build_matches_table(matches, ticker=ticker, threshold=similarity_alert)
                        st.dataframe(table, use_container_width=True, hide_index=True)
                    with c2:
                        avg_ret = table["Historical return after pivot"].mean()
                        st.metric("Average historical return after pivot", f"{avg_ret:+.2f}%")
                        st.metric(
                            "Top historical return after pivot",
                            f"{top_match.historical_return_after_pivot:+.2f}%",
                        )
                        st.metric("Detected pivots", f"{len(matches)} shown")

                    st.subheader("Plotly overlay")
                    current = enriched.iloc[-15:]
                    fig = plot_overlay(current=current, matches=matches)
                    fig.update_layout(legend_title_text=f"Similarity score ({sector})")
                    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Valitse asetukset vasemmalta ja suorita analyysi.")
