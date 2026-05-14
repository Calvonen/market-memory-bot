from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf

from market_memory.config import SECTOR_SETTINGS
from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.news import fetch_latest_news
from market_memory.pivots import Pivot, detect_pivots, detect_reversal_zones
from market_memory.sector_resolver import resolve_sector
from market_memory.similarity import MatchResult, find_best_matches, normalize_similarity_weights
from market_memory.visualization import plot_5y_pivot_map, plot_overlay


st.set_page_config(page_title="Market Memory", page_icon="📈", layout="wide")




TICKER_ALIASES = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "valmet": "VALMT.HE",
    "konecranes": "KCR.HE",
    "fortum": "FORTUM.HE",
}


def _clean_user_symbol(value: str) -> str:
    return value.strip()


def _looks_like_ticker(value: str) -> bool:
    return any(ch in value for ch in ".-=") or (value.isupper() and 1 <= len(value) <= 12)


@st.cache_data(show_spinner=False)
def _search_ticker_candidates(query: str) -> list[dict[str, str]]:
    search = yf.Search(query=query, max_results=8, news_count=0)
    quotes = getattr(search, "quotes", []) or []
    candidates: list[dict[str, str]] = []
    seen = set()
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip().upper()
        name = str(quote.get("shortname") or quote.get("longname") or symbol).strip()
        exchange = str(quote.get("exchange") or quote.get("fullExchangeName") or "").strip()
        country = str(quote.get("region") or quote.get("country") or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        location = " / ".join([part for part in [exchange, country] if part])
        candidates.append({"symbol": symbol, "name": name, "location": location})
    return candidates


def resolve_ticker_input(user_input: str) -> tuple[str | None, list[dict[str, str]], str | None]:
    cleaned = _clean_user_symbol(user_input)
    if not cleaned:
        return None, [], None

    alias_hit = TICKER_ALIASES.get(cleaned.lower())
    if alias_hit:
        return alias_hit, [], None

    if _looks_like_ticker(cleaned):
        return cleaned.upper(), [], None

    try:
        candidates = _search_ticker_candidates(cleaned)
    except Exception:
        return cleaned.upper(), [], None

    if not candidates:
        return None, [], "Tickeriä ei löytynyt. Kokeile kirjoittaa virallinen ticker, esim. VALMT.HE."

    if len(candidates) == 1:
        return candidates[0]["symbol"], candidates, None

    return None, candidates, None

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
    "sector": "yleinen",
    "similarity_alert": 0.75,
    "selected_preset": "Valitse metsästystapa",
    "last_applied_preset": None,
    "similarity_weights": DEFAULT_SIMILARITY_WEIGHTS,
    "pivot_window": 5,
    "pivot_detection_method": "exact pivot",
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
def _resolve_sector_for_ticker(ticker: str) -> tuple[str, str]:
    return resolve_sector(ticker)


def _mark_sector_manual() -> None:
    active_ticker = st.session_state.get("active_ticker", "")
    if not active_ticker:
        return
    st.session_state.setdefault("manual_sector_by_ticker", {})[active_ticker] = True

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
    pivot_window: int,
    pivot_detection_method: str,
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
        if pivot_detection_method == "reversal zone":
            pivots = detect_reversal_zones(
                enriched,
                pivot_mode=pivot_mode,
            )
        else:
            pivots = detect_pivots(
                enriched,
                pivot_window=pivot_window,
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




@st.cache_data(show_spinner=False)
def _get_company_name(ticker: str) -> str | None:
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None

    for key in ("shortName", "longName", "displayName", "name"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@st.cache_data(show_spinner=False)
def run_news_fetch(ticker: str, company_name: str | None = None, limit: int = 5) -> list[dict[str, str | None]]:
    return fetch_latest_news(ticker=ticker, company_name=company_name, limit=limit)


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
                "Pivot source": "manual" if pivot_source == "manual" else match.pivot.source,
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
    ticker_input = st.text_input("Ticker tai yrityksen nimi", value=st.session_state.get("ticker_input", "AAPL"), max_chars=32, key="ticker_input").strip()
    resolved_ticker, ticker_candidates, ticker_error = resolve_ticker_input(ticker_input)

    if ticker_candidates and not resolved_ticker:
        options = {
            f"{item['name']} ({item['symbol']})" + (f" — {item['location']}" if item['location'] else ""): item['symbol']
            for item in ticker_candidates
        }
        selected_label = st.selectbox("Valitse ticker", options=list(options.keys()), key="ticker_candidate_widget")
        resolved_ticker = options[selected_label]

    if ticker_error:
        st.error(ticker_error)

    if resolved_ticker and resolved_ticker != ticker_input.upper():
        st.caption(f"Käytetään tickeriä: {resolved_ticker}")

    ticker = resolved_ticker or ""
    current_ticker = ticker or "AAPL"
    previous_ticker = st.session_state.get("active_ticker")

    if previous_ticker != current_ticker:
        ticker_settings = _get_ticker_settings(current_ticker)
        manual_sector_by_ticker = st.session_state.setdefault("manual_sector_by_ticker", {})
        auto_sector, yahoo_sector_info = _resolve_sector_for_ticker(current_ticker)
        auto_sector = auto_sector if auto_sector in SECTOR_SETTINGS else "yleinen"
        if not manual_sector_by_ticker.get(current_ticker):
            st.session_state["sector_widget"] = auto_sector
            ticker_settings["sector"] = auto_sector
        st.session_state["auto_sector_name"] = auto_sector
        st.session_state["auto_sector_source"] = yahoo_sector_info
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
        st.session_state["pivot_window_widget"] = int(ticker_settings["pivot_window"])
        st.session_state["pivot_detection_method_widget"] = ticker_settings["pivot_detection_method"]
        st.session_state["pivot_detection_method_ui_widget"] = (
            "Käännealue" if ticker_settings["pivot_detection_method"] == "reversal zone" else "Tarkka pivot"
        )
        st.session_state["active_ticker"] = current_ticker

    st.caption("Ticker-kohtaiset asetukset tallennetaan tämän session ajaksi.")

    sector = st.selectbox("Sektori", options=list(SECTOR_SETTINGS.keys()), key="sector_widget", on_change=_mark_sector_manual)
    st.caption(f"Automaattisesti tunnistettu sektori: {st.session_state.get('auto_sector_name', 'yleinen')}")
    st.caption(f"Yahoo: {st.session_state.get('auto_sector_source') or 'Ei saatavilla'}")
    similarity_alert = st.slider("Similarity-alert", min_value=0.50, max_value=0.99, step=0.01, key="similarity_alert_widget")
    pivot_source = st.radio("Pivot source", options=["automatic", "manual"], horizontal=True, key="pivot_source_widget")
    pivot_detection_method_label = st.radio(
        "Käänteen tunnistustapa",
        options=["Tarkka pivot", "Käännealue"],
        horizontal=True,
        disabled=pivot_source != "automatic",
        key="pivot_detection_method_ui_widget",
    )
    pivot_detection_method = "reversal zone" if pivot_detection_method_label == "Käännealue" else "exact pivot"
    pivot_mode = st.radio("Pivot mode", options=["all", "bottom", "peak"], horizontal=True, disabled=pivot_source == "manual", key="pivot_mode_widget")
    manual_pivot_type = st.radio("Manual pivot type", options=["bottom", "peak"], horizontal=True, disabled=pivot_source != "manual", key="manual_pivot_type_widget")
    manual_pivot_dates_text = st.text_area(
        "Manual pivot dates",
        key="manual_pivot_dates_text_widget",
        placeholder="2023-10-04\n2024-10-31\n2025-04-25",
        disabled=pivot_source != "manual",
        help="Syötä päivämäärät riveittäin tai pilkulla eroteltuna (YYYY-MM-DD).",
    )
    pivot_window = st.select_slider(
        "Osuman merkittävyys",
        options=[5, 10, 15, 20, 30],
        key="pivot_window_widget",
        disabled=pivot_source != "automatic",
    )
    st.caption("Pieni arvo löytää nopeat swingit. Suuri arvo löytää suuremmat sykliset käänteet.")

    st.markdown("---")
    st.subheader("Käännepisteen painotukset")
    preset_placeholder = "Valitse metsästystapa"
    preset_options = {
        "pohjan metsästys": {"price": 0.10, "rsi": 0.35, "volume": 0.20, "volatility": 0.25, "trend": 0.10},
        "paniikkipohja": {"price": 0.05, "rsi": 0.30, "volume": 0.25, "volatility": 0.30, "trend": 0.10},
        "huipun metsästys": {"price": 0.30, "rsi": 0.30, "volume": 0.15, "volatility": 0.15, "trend": 0.10},
        "väsyvä huippu": {"price": 0.25, "rsi": 0.20, "volume": 0.10, "volatility": 0.10, "trend": 0.35},
    }
    preset_select_options = [preset_placeholder, *list(preset_options.keys())]
    if st.session_state.get("selected_preset_widget") not in preset_select_options:
        st.session_state["selected_preset_widget"] = preset_placeholder
    selected_preset = st.selectbox("Metsästystapa", options=preset_select_options, key="selected_preset_widget")
    last_applied_preset = st.session_state.get("last_applied_preset_widget")
    if selected_preset in preset_options and selected_preset != last_applied_preset:
        preset_weights = preset_options[selected_preset]
        st.session_state["price_weight_widget"] = float(preset_weights["price"])
        st.session_state["rsi_weight_widget"] = float(preset_weights["rsi"])
        st.session_state["volume_weight_widget"] = float(preset_weights["volume"])
        st.session_state["volatility_weight_widget"] = float(preset_weights["volatility"])
        st.session_state["trend_weight_widget"] = float(preset_weights["trend"])
        st.session_state["last_applied_preset_widget"] = selected_preset

    st.caption("Valitse haetko pohjaa vai huippua. Painotuksia voi säätää käsin.")

    price_weight = st.slider("Hintakäyrä", min_value=0.0, max_value=1.0, step=0.01, key="price_weight_widget")
    rsi_weight = st.slider("RSI", min_value=0.0, max_value=1.0, step=0.01, key="rsi_weight_widget")
    volume_weight = st.slider("Volyymi", min_value=0.0, max_value=1.0, step=0.01, key="volume_weight_widget")
    volatility_weight = st.slider("Volatiliteetti", min_value=0.0, max_value=1.0, step=0.01, key="volatility_weight_widget")
    trend_weight = st.slider("Trendi", min_value=0.0, max_value=1.0, step=0.01, key="trend_weight_widget")
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
            "pivot_window": pivot_window,
            "pivot_detection_method": pivot_detection_method,
        },
    )
    run = st.button("Suorita analyysi", type="primary", use_container_width=True)

if run:
    if not ticker:
        st.error("Tickeriä ei löytynyt. Kokeile kirjoittaa virallinen ticker, esim. VALMT.HE.")
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
                    pivot_window=pivot_window,
                    pivot_detection_method=pivot_detection_method,
                )
            except Exception as exc:
                st.exception(exc)
            else:
                st.success(
                    f"Analyysi valmis: {ticker} | sektori: {sector} | pivot source: {pivot_source} | moodi: {pivot_mode} | tunnistus: {pivot_detection_method}"
                )
                st.caption(f"Pivot significance window: {pivot_window}d")

                for note in notes:
                    st.caption(note)

                if not matches:
                    st.warning("Ei historiallisia osumia valituilla ehdoilla.")
                else:
                    top_match = matches[0]
                    show_alert_status = selected_preset in preset_options
                    is_alert = top_match.score >= similarity_alert
                    if show_alert_status and is_alert:
                        kind = "REBOUND WATCH" if top_match.pivot.pivot_type == "bottom" else "SHORT WATCH"
                        st.error(f"Alert status: ALERT — {kind} (top score {top_match.score:.3f})")
                    elif show_alert_status:
                        st.info(f"Alert status: INFO — no alert (top score {top_match.score:.3f})")
                    else:
                        st.caption("Valitse metsästystapa, jotta alert-status näytetään.")

                    st.subheader("Parhaat historialliset osumat")
                    table = build_matches_table(
                        matches, ticker=ticker, threshold=similarity_alert, pivot_source=pivot_source
                    )
                    table_height = min(420, 38 * len(table) + 40)
                    st.dataframe(
                        table,
                        use_container_width=True,
                        hide_index=True,
                        height=table_height,
                    )
                    st.caption(f"Detected pivots: {len(matches)} shown")

                    avg_ret_5 = table["return +5d"].mean()
                    avg_ret_10 = table["return +10d"].mean()
                    avg_ret_15 = table["return +15d"].mean()
                    metric_cols = st.columns(4)
                    metric_cols[0].metric("avg return +5d", f"{avg_ret_5:+.2f}%")
                    metric_cols[1].metric("avg return +10d", f"{avg_ret_10:+.2f}%")
                    metric_cols[2].metric("avg return +15d", f"{avg_ret_15:+.2f}%")
                    metric_cols[3].metric(
                        "top historical return",
                        f"{top_match.historical_return_after_pivot:+.2f}%",
                    )

                    st.subheader("5y pivot map")
                    pivot_map_fig = plot_5y_pivot_map(history=enriched, matches=matches)
                    st.plotly_chart(pivot_map_fig, use_container_width=True)

                    st.subheader("Plotly overlay")
                    current = enriched.iloc[-15:]
                    fig = plot_overlay(current=current, matches=matches)
                    fig.update_layout(legend_title_text=f"Similarity score ({sector})")
                    st.plotly_chart(fig, use_container_width=True)

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


                st.subheader("Viimeisimmät uutiset")
                st.caption("Näytetään viimeisen 90 päivän uutiset")
                news_source_note = None
                try:
                    company_name = _get_company_name(ticker)
                    latest_news = run_news_fetch(ticker=ticker, company_name=company_name, limit=5)
                    if latest_news:
                        news_source_note = latest_news[0].get("source")
                except Exception:
                    latest_news = []
                    st.caption("⚠️ Uutisten haussa tapahtui virhe.")

                if news_source_note:
                    st.caption(f"News source: {news_source_note}")

                if not latest_news:
                    st.info("Viimeisen 90 päivän uutisia ei löytynyt.")
                else:
                    for news in latest_news:
                        meta_parts = []
                        if news.get("publisher"):
                            meta_parts.append(str(news["publisher"]))
                        if news.get("published"):
                            meta_parts.append(str(news["published"]))
                        meta_text = " • ".join(meta_parts)

                        st.markdown(f"- [{news['title']}]({news['link']})")
                        if meta_text:
                            st.caption(meta_text)
else:
    st.info("Valitse asetukset vasemmalta ja suorita analyysi.")
