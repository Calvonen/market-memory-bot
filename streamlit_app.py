from __future__ import annotations

import pandas as pd
import streamlit as st

from market_memory.config import SECTOR_SETTINGS
from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.pivots import Pivot, detect_pivots
from market_memory.similarity import MatchResult, find_best_matches, normalize_similarity_weights
from market_memory.visualization import plot_overlay


st.set_page_config(page_title="Market Memory", page_icon="📈", layout="wide")


DEFAULT_SIMILARITY_WEIGHTS = {
    "price": 0.20,
    "rsi": 0.20,
    "volume": 0.20,
    "volatility": 0.20,
    "trend": 0.20,
}

DEFAULT_TICKER_SETTINGS = {
    "manual_pivot_dates_text": "",
    "manual_pivot_type": "bottom",
    "pivot_source": "automatic",
    "pivot_mode": "all",
    "sector": list(SECTOR_SETTINGS.keys())[0],
    "similarity_alert": 0.75,
    "selected_preset": "balanced",
    "last_applied_preset": "balanced",
    "similarity_weights": DEFAULT_SIMILARITY_WEIGHTS,
}


def _get_ticker_settings(ticker: str) -> dict:
    settings_store = st.session_state.setdefault("ticker_settings", {})
    base = DEFAULT_TICKER_SETTINGS | {"similarity_weights": DEFAULT_SIMILARITY_WEIGHTS.copy()}
    saved = settings_store.get(ticker.upper(), {})
    merged = base | saved
    merged["similarity_weights"] = (base["similarity_weights"] | saved.get("similarity_weights", {})).copy()
    return merged


def _save_ticker_settings(ticker: str, settings: dict) -> None:
    settings_store = st.session_state.setdefault("ticker_settings", {})
    settings_store[ticker.upper()] = settings


@st.cache_data(show_spinner=False)
def run_analysis(
    ticker: str,
    sector: str,
    similarity_alert: float,
    pivot_mode: str,
    pivot_source: str,
    manual_pivot_type: str,
    manual_pivot_dates_text: str,
    similarity_weights: dict[str, float],
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

    matches = find_best_matches(
        enriched,
        pivots,
        current_window=15,
        top_k=8,
        similarity_weights=similarity_weights,
    )
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
    ticker = st.text_input("Ticker", value=st.session_state.get("ticker_input", "AAPL"), max_chars=12, key="ticker_input").strip().upper()
    current_ticker = ticker or "AAPL"
    previous_ticker = st.session_state.get("active_ticker")

    if previous_ticker != current_ticker:
        ticker_settings = _get_ticker_settings(current_ticker)
        st.session_state["sector_widget"] = ticker_settings["sector"]
        st.session_state["similarity_alert_widget"] = float(ticker_settings["similarity_alert"])
        st.session_state["pivot_source_widget"] = ticker_settings["pivot_source"]
        st.session_state["pivot_mode_widget"] = ticker_settings["pivot_mode"]
        st.session_state["manual_pivot_type_widget"] = ticker_settings["manual_pivot_type"]
        st.session_state["manual_pivot_dates_text_widget"] = ticker_settings["manual_pivot_dates_text"]
        st.session_state["selected_preset_widget"] = ticker_settings["selected_preset"]
        st.session_state["last_applied_preset_widget"] = ticker_settings.get("last_applied_preset", ticker_settings["selected_preset"])
        st.session_state["price_weight_widget"] = float(ticker_settings["similarity_weights"]["price"])
        st.session_state["rsi_weight_widget"] = float(ticker_settings["similarity_weights"]["rsi"])
        st.session_state["volume_weight_widget"] = float(ticker_settings["similarity_weights"]["volume"])
        st.session_state["volatility_weight_widget"] = float(ticker_settings["similarity_weights"]["volatility"])
        st.session_state["trend_weight_widget"] = float(ticker_settings["similarity_weights"]["trend"])
        st.session_state["active_ticker"] = current_ticker

    st.caption("Ticker-kohtaiset asetukset tallennetaan tämän session ajaksi.")

    sector = st.selectbox("Sektori", options=list(SECTOR_SETTINGS.keys()), key="sector_widget")
    similarity_alert = st.slider("Similarity-alert", min_value=0.50, max_value=0.99, step=0.01, key="similarity_alert_widget")
    pivot_source = st.radio("Pivot source", options=["automatic", "manual"], horizontal=True, key="pivot_source_widget")
    pivot_mode = st.radio("Pivot mode", options=["all", "bottom", "peak"], horizontal=True, disabled=pivot_source == "manual", key="pivot_mode_widget")
    manual_pivot_type = st.radio("Manual pivot type", options=["bottom", "peak"], horizontal=True, disabled=pivot_source != "manual", key="manual_pivot_type_widget")
    manual_pivot_dates_text = st.text_area(
        "Manual pivot dates",
        key="manual_pivot_dates_text_widget",
        placeholder="2023-10-04\n2024-10-31\n2025-04-25",
        disabled=pivot_source != "manual",
        help="Syötä päivämäärät riveittäin tai pilkulla eroteltuna (YYYY-MM-DD).",
    )

    st.markdown("---")
    st.subheader("Similarity weights")
    preset_options = {
        "balanced": {"price": 0.20, "rsi": 0.20, "volume": 0.20, "volatility": 0.20, "trend": 0.20},
        "rebound hunter": {"price": 0.10, "rsi": 0.35, "volume": 0.20, "volatility": 0.25, "trend": 0.10},
        "panic reversal": {"price": 0.10, "rsi": 0.30, "volume": 0.25, "volatility": 0.30, "trend": 0.05},
        "trend continuation": {"price": 0.30, "rsi": 0.10, "volume": 0.15, "volatility": 0.10, "trend": 0.35},
    }
    selected_preset = st.selectbox("Preset", options=list(preset_options.keys()), key="selected_preset_widget")
    last_applied_preset = st.session_state.get("last_applied_preset_widget")
    if selected_preset != last_applied_preset:
        preset_weights = preset_options[selected_preset]
        st.session_state["price_weight_widget"] = float(preset_weights["price"])
        st.session_state["rsi_weight_widget"] = float(preset_weights["rsi"])
        st.session_state["volume_weight_widget"] = float(preset_weights["volume"])
        st.session_state["volatility_weight_widget"] = float(preset_weights["volatility"])
        st.session_state["trend_weight_widget"] = float(preset_weights["trend"])
        st.session_state["last_applied_preset_widget"] = selected_preset

    price_weight = st.slider("price weight", min_value=0.0, max_value=1.0, step=0.01, key="price_weight_widget")
    rsi_weight = st.slider("RSI weight", min_value=0.0, max_value=1.0, step=0.01, key="rsi_weight_widget")
    volume_weight = st.slider("volume weight", min_value=0.0, max_value=1.0, step=0.01, key="volume_weight_widget")
    volatility_weight = st.slider("volatility weight", min_value=0.0, max_value=1.0, step=0.01, key="volatility_weight_widget")
    trend_weight = st.slider("trend weight", min_value=0.0, max_value=1.0, step=0.01, key="trend_weight_widget")
    similarity_weights = normalize_similarity_weights(
        {
            "price": price_weight,
            "rsi": rsi_weight,
            "volume": volume_weight,
            "volatility": volatility_weight,
            "trend": trend_weight,
        }
    )
    st.caption(
        "Normalized weights: "
        f"price {similarity_weights['price']:.2f}, "
        f"RSI {similarity_weights['rsi']:.2f}, "
        f"volume {similarity_weights['volume']:.2f}, "
        f"volatility {similarity_weights['volatility']:.2f}, "
        f"trend {similarity_weights['trend']:.2f}"
    )
    st.caption(f"Weight sum = {sum(similarity_weights.values()):.2f}")

    _save_ticker_settings(
        current_ticker,
        {
            "manual_pivot_dates_text": manual_pivot_dates_text,
            "manual_pivot_type": manual_pivot_type,
            "pivot_source": pivot_source,
            "pivot_mode": pivot_mode,
            "sector": sector,
            "similarity_alert": similarity_alert,
            "selected_preset": selected_preset,
            "last_applied_preset": st.session_state.get("last_applied_preset_widget", selected_preset),
            "similarity_weights": similarity_weights,
        },
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
                    similarity_weights=similarity_weights,
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

                    st.subheader("Similarity formula")
                    st.code(
                        "\n".join(
                            [
                                f"{similarity_weights['price']:.2f} * price +",
                                f"{similarity_weights['rsi']:.2f} * RSI +",
                                f"{similarity_weights['volume']:.2f} * volume +",
                                f"{similarity_weights['volatility']:.2f} * volatility +",
                                f"{similarity_weights['trend']:.2f} * trend",
                            ]
                        ),
                        language="text",
                    )
                    st.caption(
                        "Top match score = "
                        f"{similarity_weights['price']:.2f}×{top_match.price_similarity:.3f} + "
                        f"{similarity_weights['rsi']:.2f}×{top_match.rsi_similarity:.3f} + "
                        f"{similarity_weights['volume']:.2f}×{top_match.volume_similarity:.3f} + "
                        f"{similarity_weights['volatility']:.2f}×{top_match.volatility_similarity:.3f} + "
                        f"{similarity_weights['trend']:.2f}×{top_match.trend_similarity:.3f}"
                    )

                    st.subheader("Plotly overlay")
                    current = enriched.iloc[-15:]
                    fig = plot_overlay(current=current, matches=matches)
                    fig.update_layout(legend_title_text=f"Similarity score ({sector})")
                    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Valitse asetukset vasemmalta ja suorita analyysi.")
