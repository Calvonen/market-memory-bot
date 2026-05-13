from __future__ import annotations

import pandas as pd
import streamlit as st

from market_memory.config import SECTOR_SETTINGS
from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.pivots import Pivot, detect_pivots
from market_memory.similarity import MatchResult, find_best_matches
from market_memory.visualization import plot_overlay


st.set_page_config(page_title="Market Memory", page_icon="📈", layout="wide")


@st.cache_data(show_spinner=False)
def run_analysis(
    ticker: str,
    sector: str,
    similarity_alert: float,
    pivot_mode: str,
    pivot_source: str,
    manual_pivot_type: str,
    manual_pivot_dates_text: str,
    period: str = "5y",
) -> tuple[pd.DataFrame, list[MatchResult], list[str]]:
    raw = fetch_ohlcv(ticker=ticker, period=period)
    enriched = add_indicators(raw)
    settings = SECTOR_SETTINGS[sector]

    notes: list[str] = []

    if pivot_source == "manual":
        raw_tokens = manual_pivot_dates_text.replace(",", "\n").splitlines()
        date_tokens = [token.strip() for token in raw_tokens if token.strip()]
        if not date_tokens:
            raise ValueError("Manual pivot source selected but no dates were provided.")

        pivots: list[Pivot] = []
        unique_dates = set()
        for token in date_tokens:
            dt = pd.to_datetime(token, errors="coerce")
            if pd.isna(dt):
                notes.append(f"Skipped invalid date: {token}")
                continue

            date_key = pd.Timestamp(dt).normalize()
            if date_key in unique_dates:
                continue

            eligible = enriched.index[enriched.index.normalize() <= date_key]
            if len(eligible) == 0:
                notes.append(f"Skipped {date_key.date().isoformat()}: no earlier trading day in dataset")
                continue

            pivot_idx = eligible[-1]
            if pivot_idx.normalize() != date_key:
                notes.append(
                    f"Adjusted {date_key.date().isoformat()} -> {pivot_idx.date().isoformat()} (previous trading day)"
                )

            unique_dates.add(date_key)
            pivots.append(Pivot(index=pivot_idx, pivot_type=manual_pivot_type, pivot_move_pct=0.0))

        if not pivots:
            raise ValueError("No valid manual pivot dates found in the available dataset.")

        pivots = sorted(pivots, key=lambda p: p.index)
    else:
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
    return enriched, matches, notes


def build_matches_table(matches: list[MatchResult], ticker: str, threshold: float, pivot_source: str) -> pd.DataFrame:
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
                "Pivot source": pivot_source,
                "Alert status": f"{status}: {alert_text if status == 'ALERT' else 'no alert'}",
                "Historical return after pivot": round(match.historical_return_after_pivot, 2),
                "return +5d": round(match.return_plus_5d, 2) if match.return_plus_5d is not None else None,
                "return +10d": round(match.return_plus_10d, 2) if match.return_plus_10d is not None else None,
                "return +15d": round(match.return_plus_15d, 2) if match.return_plus_15d is not None else None,
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
    pivot_source = st.radio("Pivot source", options=["automatic", "manual"], horizontal=True)
    pivot_mode = st.radio("Pivot mode", options=["all", "bottom", "peak"], horizontal=True, disabled=pivot_source == "manual")
    manual_pivot_type = st.radio("Manual pivot type", options=["bottom", "peak"], horizontal=True, disabled=pivot_source != "manual")
    manual_pivot_dates_text = st.text_area(
        "Manual pivot dates",
        value="",
        placeholder="2023-10-04\n2024-10-31\n2025-04-25",
        disabled=pivot_source != "manual",
        help="Syötä päivämäärät riveittäin tai pilkulla eroteltuna (YYYY-MM-DD).",
    )
    run = st.button("Suorita analyysi", type="primary", use_container_width=True)

if run:
    if not ticker:
        st.error("Syötä ticker ennen analyysiä.")
    else:
        with st.spinner("Haetaan dataa ja lasketaan osumat..."):
            try:
                enriched, matches, notes = run_analysis(
                    ticker=ticker,
                    sector=sector,
                    similarity_alert=similarity_alert,
                    pivot_mode=pivot_mode,
                    pivot_source=pivot_source,
                    manual_pivot_type=manual_pivot_type,
                    manual_pivot_dates_text=manual_pivot_dates_text,
                )
            except Exception as exc:
                st.exception(exc)
            else:
                st.success(
                    f"Analyysi valmis: {ticker} | sektori: {sector} | pivot source: {pivot_source} | moodi: {pivot_mode}"
                )

                for note in notes:
                    st.caption(note)

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
                        table = build_matches_table(
                            matches, ticker=ticker, threshold=similarity_alert, pivot_source=pivot_source
                        )
                        st.dataframe(table, use_container_width=True, hide_index=True)
                    with c2:
                        avg_ret = table["Historical return after pivot"].mean()
                        avg_ret_5 = table["return +5d"].mean()
                        avg_ret_10 = table["return +10d"].mean()
                        avg_ret_15 = table["return +15d"].mean()
                        st.metric("Average historical return after pivot", f"{avg_ret:+.2f}%")
                        st.metric("avg return +5d", f"{avg_ret_5:+.2f}%")
                        st.metric("avg return +10d", f"{avg_ret_10:+.2f}%")
                        st.metric("avg return +15d", f"{avg_ret_15:+.2f}%")
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
