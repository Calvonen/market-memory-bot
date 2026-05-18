from __future__ import annotations

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import streamlit as st
import yfinance as yf

from market_memory.config import SECTOR_SETTINGS
from market_memory.data import fetch_ohlcv
from market_memory.fundamentals import fetch_quarterly_fundamentals
from market_memory.indicators import add_indicators
from market_memory.market_state import get_current_market_state
from market_memory.news import fetch_latest_news
from market_memory.pivots import Pivot, detect_pivots, detect_reversal_zones
from market_memory.sector_resolver import resolve_sector
from market_memory.similarity import MatchResult, find_best_matches, normalize_similarity_weights
from market_memory.universe import MARKET_TICKERS
from market_memory.visualization import plot_5y_pivot_map, plot_overlay


st.set_page_config(page_title="Market Memory", page_icon="📈", layout="wide")

st.markdown(
    """
    <style>
    div[data-testid="stHorizontalBlock"] div[data-testid="column"] {
        min-width: 180px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)




DATA_DIR = Path("data")
OPEN_TRADES_PATH = DATA_DIR / "open_trades.json"
CLOSED_TRADES_PATH = DATA_DIR / "closed_trades.json"


def _load_trades(path: Path) -> tuple[list[dict[str, object]], bool]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return [], False

    try:
        with path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except (json.JSONDecodeError, OSError):
        return [], True

    if not isinstance(loaded, list):
        return [], True

    return loaded, False


def _save_trades(open_trades: list[dict[str, object]], closed_trades: list[dict[str, object]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with OPEN_TRADES_PATH.open("w", encoding="utf-8") as file:
        json.dump(open_trades, file, ensure_ascii=False, indent=2)
    with CLOSED_TRADES_PATH.open("w", encoding="utf-8") as file:
        json.dump(closed_trades, file, ensure_ascii=False, indent=2)




def _build_trade_export_payload(open_trades: list[dict[str, object]], closed_trades: list[dict[str, object]]) -> dict[str, object]:
    return {
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "exported_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }


def _parse_trade_import_payload(raw_payload: bytes) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    try:
        parsed = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Trade-tiedostoa ei voitu lukea") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Trade-tiedostoa ei voitu lukea")

    imported_open_trades = parsed.get("open_trades")
    imported_closed_trades = parsed.get("closed_trades")

    if not isinstance(imported_open_trades, list) or not isinstance(imported_closed_trades, list):
        raise ValueError("Trade-tiedostoa ei voitu lukea")

    return imported_open_trades, imported_closed_trades
def _ensure_trade_state_loaded() -> None:
    load_warnings = st.session_state.setdefault("trade_load_warnings", [])

    if "open_trades" not in st.session_state:
        open_trades, open_failed = _load_trades(OPEN_TRADES_PATH)
        st.session_state["open_trades"] = open_trades
        if open_failed:
            load_warnings.append("Avoimia tradeja ei voitu lukea tiedostosta, aloitetaan tyhjällä listalla.")

    if "closed_trades" not in st.session_state:
        closed_trades, closed_failed = _load_trades(CLOSED_TRADES_PATH)
        st.session_state["closed_trades"] = closed_trades
        if closed_failed:
            load_warnings.append("Suljettuja tradeja ei voitu lukea tiedostosta, aloitetaan tyhjällä listalla.")


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

def _resolve_trade_ticker(user_input: str, selected_candidate_symbol: str | None = None) -> tuple[str | None, str | None]:
    resolved, candidates, _ = resolve_ticker_input(user_input)
    if resolved:
        return resolved, None
    if candidates:
        if selected_candidate_symbol:
            selected = selected_candidate_symbol.strip().upper()
            if any(str(item.get("symbol") or "").upper() == selected for item in candidates):
                return selected, None
        return None, "Valitse ticker listasta ennen trade:n lisäämistä."
    return None, "Tickeriä ei voitu ratkaista. Käytä tickeriä tai yrityksen virallista nimeä."

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
    "similarity_alert": 0.85,
    "selected_preset": "Valitse setup-tyyli",
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



def _resolve_similarity_threshold(pivot_source: str, pivot_detection_method: str) -> float:
    if pivot_source == "automatic" and pivot_detection_method == "exact pivot":
        return 0.90
    return 0.85


def _legacy_setup_name(setup_style: str) -> str:
    mapping = {
        "Rebound setup": "pohjan metsästys",
        "Short setup": "huipun metsästys",
        "Momentum long": "nousun jatkumo",
        "Momentum short": "laskun jatkumo",
    }
    return mapping.get(setup_style, setup_style)


def _resolve_signal_type(pivot_type: str, setup_style: str) -> str:
    if setup_style == "Momentum long":
        return "MOMENTUM LONG"
    if setup_style == "Momentum short":
        return "MOMENTUM SHORT"
    return "REBOUND WATCH" if pivot_type == "bottom" else "SHORT WATCH"


def generate_momentum_summary(
    current: pd.Series,
    top_match: MatchResult,
    market_state_rows: list[dict[str, str]],
    similarity_alert: float,
    selected_preset: str,
) -> tuple[str, str]:
    trend_state = next((row["Tila"] for row in market_state_rows if row.get("Mittari") == "Trendi"), "⚪ Mixed")
    vol_state = next((row["Tila"] for row in market_state_rows if row.get("Mittari") == "Volatiliteetti"), "⚪ Normal")
    signal_type = _resolve_signal_type(top_match.pivot.pivot_type, selected_preset)
    rsi = float(current.get("rsi", float("nan")))
    macd_hist = float(current.get("macd_hist", float("nan")))
    similarity = float(top_match.score)
    rr_15d = float(top_match.historical_return_after_pivot)

    bullish_signal = signal_type in {"REBOUND WATCH", "MOMENTUM LONG"}
    bearish_signal = signal_type in {"SHORT WATCH", "MOMENTUM SHORT"}
    bullish = "Bullish" in trend_state and macd_hist > 0 and bullish_signal
    bearish = "Bearish" in trend_state and macd_hist < 0 and bearish_signal

    if bullish:
        emoji = "🟢"
        sentences = [
            "Momentum pysyy vahvana ja trendi tukee nousun jatkumista.",
            f"RSI on {rsi:.1f} ja MACD-histogrammi on plussalla ({macd_hist:.3f}), joten ostovoima kantaa edelleen.",
            f"Similarity-osuma on {similarity:.3f} ({signal_type}), historiallinen +15d tuotto {rr_15d:+.1f}%, volatiliteetti {vol_state.lower()}.",
        ]
        if rsi > 70:
            sentences.append("RSI on jo kuuma, joten liikkeet voivat kiihtyä nopeasti molempiin suuntiin.")
    elif bearish:
        emoji = "🔴"
        sentences = [
            "Myyntipaine pysyy hallitsevana ja trendi jatkaa alaspäin.",
            f"RSI on {rsi:.1f} ja MACD-histogrammi painuu miinuksella ({macd_hist:.3f}), mikä pitää momentumin negatiivisena.",
            f"Similarity-osuma on {similarity:.3f} ({signal_type}), historiallinen +15d tuotto {rr_15d:+.1f}% ja volatiliteetti {vol_state.lower()}.",
        ]
    else:
        emoji = "🟡"
        confirmation_text = "vahvistus puuttuu vielä" if similarity < similarity_alert else "vahvistus alkaa rakentua"
        sentences = [
            "Mahdollinen rebound-rakenne on muodostumassa, mutta markkina hakee vielä suuntaa.",
            f"RSI on {rsi:.1f} ja MACD-histogrammi {macd_hist:.3f}, joten momentum kääntyy hitaasti mutta ei ole vielä täysin selkeä.",
            f"Similarity-osuma on {similarity:.3f} ({signal_type}), {confirmation_text}, volatiliteetti {vol_state.lower()} ja risk/reward {rr_15d:+.1f}% (historiallinen +15d).",
        ]

    return emoji, " ".join(sentences[:5])

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
def run_quarterly_fundamentals_fetch(ticker: str) -> pd.DataFrame:
    return fetch_quarterly_fundamentals(ticker)

@st.cache_data(show_spinner=False)
def run_news_fetch(ticker: str, company_name: str | None = None, limit: int = 5) -> list[dict[str, str | None]]:
    return fetch_latest_news(ticker=ticker, company_name=company_name, limit=limit)


def _normalize_to_day(value: object) -> pd.Timestamp | None:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


def _extract_future_date_candidates(text: str, today: pd.Timestamp) -> list[pd.Timestamp]:
    patterns = [
        r"\b(?:20\d{2}|19\d{2})[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b",
        r"\b(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:20\d{2}|19\d{2})\b",
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?\s+(?:20\d{2}|19\d{2})\b",
    ]

    candidates: list[pd.Timestamp] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            parsed = pd.to_datetime(match.group(0), errors="coerce")
            if pd.isna(parsed):
                continue
            normalized = _normalize_to_day(parsed)
            if normalized is not None and normalized >= today:
                candidates.append(normalized)
    return candidates


@st.cache_data(show_spinner=False)
def fetch_next_earnings_date(ticker: str, company_name: str | None = None) -> tuple[pd.Timestamp | None, str | None]:
    symbol = yf.Ticker(ticker)
    today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)

    calendar = getattr(symbol, "calendar", None)
    if isinstance(calendar, pd.DataFrame) and not calendar.empty:
        for column in calendar.columns:
            values = pd.to_datetime(calendar[column], errors="coerce").dropna()
            for value in values:
                normalized = _normalize_to_day(value)
                if normalized is not None and normalized >= today:
                    return normalized, None

    try:
        earnings_dates = symbol.get_earnings_dates(limit=8)
    except Exception:
        earnings_dates = None

    if isinstance(earnings_dates, pd.DataFrame) and not earnings_dates.empty:
        dates = pd.to_datetime(earnings_dates.index, errors="coerce").dropna()
        future_dates = []
        for dt in dates:
            normalized = _normalize_to_day(dt)
            if normalized is not None and normalized >= today:
                future_dates.append(normalized)
        if future_dates:
            return min(future_dates), None

    raw_queries = [
        f"{company_name} financial calendar" if company_name else None,
        f"{company_name} earnings date" if company_name else None,
        f"{ticker} earnings date",
    ]
    queries = [query for query in raw_queries if query]

    for query in queries:
        rss_url = f"https://news.google.com/rss/search?q={quote_plus(query)}"
        try:
            feed = feedparser.parse(rss_url)
        except Exception:
            continue
        entries = getattr(feed, "entries", []) or []
        for entry in entries[:8]:
            text_parts = [str(entry.get("title") or ""), str(entry.get("summary") or "")]
            text = " ".join(text_parts)
            candidates = _extract_future_date_candidates(text, today=today)
            if candidates:
                return min(candidates), None

    return None, None




@st.cache_data(show_spinner=False)
def _fetch_latest_price(ticker: str, refresh_key: str | None = None) -> float | None:
    try:
        history = yf.Ticker(ticker).history(period="5d", interval="1d")
    except Exception:
        return None
    if history.empty:
        return None
    close = history["Close"].dropna()
    if close.empty:
        return None
    return float(close.iloc[-1])


@st.cache_data(show_spinner=False)
def _fetch_trend_sma20(ticker: str, refresh_key: str | None = None) -> tuple[float | None, float | None]:
    try:
        history = yf.Ticker(ticker).history(period="3mo", interval="1d")
    except Exception:
        return None, None
    if history.empty or "Close" not in history:
        return None, None
    closes = history["Close"].dropna()
    if closes.empty:
        return None, None
    sma20 = closes.rolling(20).mean().iloc[-1]
    return float(closes.iloc[-1]), (float(sma20) if pd.notna(sma20) else None)


def _calc_trade_status(direction: str, current_price: float | None, stop_loss: float, target_price: float, trend_warning: bool) -> str:
    if current_price is None:
        return "NO PRICE"
    if direction == "long" and current_price <= stop_loss:
        return "STOP HIT"
    if direction == "short" and current_price >= stop_loss:
        return "STOP HIT"
    if direction == "long" and current_price >= target_price:
        return "TARGET HIT"
    if direction == "short" and current_price <= target_price:
        return "TARGET HIT"
    if trend_warning:
        return "WARNING"
    return "OK"


def _calc_trade_status_reason(status: str) -> str:
    if status == "OK":
        return "Hinta stopin ja targetin välissä"
    if status == "WARNING":
        return "Trendi liikkuu treidiä vastaan"
    if status == "STOP HIT":
        return "Stop loss saavutettu"
    if status == "TARGET HIT":
        return "Target saavutettu"
    return "Hintaa ei saatu haettua"


def _refresh_open_trade(trade: dict[str, object], refresh_key: str | None = None) -> None:
    ticker_symbol = str(trade.get("ticker") or "")
    direction = str(trade.get("direction") or "")
    entry = float(trade.get("entry_price") or 0.0)
    stop_loss = float(trade.get("stop_loss") or 0.0)
    target_price = float(trade.get("target_price") or 0.0)
    leverage = int(trade.get("leverage", 1))

    current_price = _fetch_latest_price(ticker_symbol, refresh_key=refresh_key)
    latest_close, sma20 = _fetch_trend_sma20(ticker_symbol, refresh_key=refresh_key)
    trend_against = False
    if latest_close is not None and sma20 is not None:
        trend_against = (direction == "long" and latest_close < sma20) or (direction == "short" and latest_close > sma20)

    pl_pct = None
    stop_distance_pct = None
    target_distance_pct = None
    rr = None
    if current_price is not None and entry != 0:
        if direction == "long":
            pl_pct = ((current_price - entry) / entry) * 100 * leverage
            stop_distance_pct = ((entry - stop_loss) / entry) * 100 * leverage
            target_distance_pct = ((target_price - current_price) / current_price) * 100 if current_price != 0 else None
            risk = entry - stop_loss
            reward = target_price - entry
        else:
            pl_pct = ((entry - current_price) / entry) * 100 * leverage
            stop_distance_pct = ((stop_loss - entry) / entry) * 100 * leverage
            target_distance_pct = ((current_price - target_price) / current_price) * 100 if current_price != 0 else None
            risk = stop_loss - entry
            reward = entry - target_price
        if risk > 0:
            rr = reward / risk

    status = _calc_trade_status(direction, current_price, stop_loss, target_price, trend_against)
    trade["current_price"] = current_price
    trade["pl_pct"] = pl_pct
    trade["stop_distance_pct"] = stop_distance_pct
    trade["target_distance_pct"] = target_distance_pct
    trade["risk_reward"] = rr
    trade["status"] = status
    trade["status_reason"] = _calc_trade_status_reason(status)


def _refresh_all_open_trades(trades: list[dict[str, object]], closed_trades: list[dict[str, object]]) -> None:
    refresh_key = pd.Timestamp.now(tz="UTC").isoformat()
    for trade in trades:
        _refresh_open_trade(trade, refresh_key=refresh_key)
    _save_trades(trades, closed_trades)
    st.session_state["trades_last_updated"] = pd.Timestamp.now(tz="UTC")

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


def _render_trade_mobile_card(trade: dict[str, object], idx: int) -> tuple[bool, bool]:
    ticker_symbol = str(trade.get("ticker") or "")
    direction = str(trade.get("direction") or "")
    entry = float(trade.get("entry_price") or 0.0)
    stop_loss = float(trade.get("stop_loss") or 0.0)
    target_price = float(trade.get("target_price") or 0.0)
    leverage = int(trade.get("leverage", 1))
    current_price = trade.get("current_price")
    pl_pct = trade.get("pl_pct")
    rr = trade.get("risk_reward")
    status = str(trade.get("status") or "NO PRICE")
    status_reason = str(trade.get("status_reason") or _calc_trade_status_reason(status))

    st.markdown("---")
    st.markdown(f"**Ticker:** {ticker_symbol}")
    st.write(f"**Suunta:** {direction}")
    st.write(f"**Vipu:** {leverage}x")
    st.write(f"**Entry:** {round(entry, 4)}")
    st.write(f"**Current:** {round(current_price, 4) if current_price is not None else '-'}")
    st.write(f"**P/L %:** {round(pl_pct, 2) if pl_pct is not None else '-'}")
    st.write(f"**Stop:** {round(stop_loss, 4)}")
    st.write(f"**Target:** {round(target_price, 4)}")
    st.write(f"**R/R:** {round(rr, 2) if rr is not None else '-'}")
    st.write(f"**Status:** {status}")
    st.write(f"**Syy:** {status_reason}")
    action_cols = st.columns(2)
    remove_clicked = action_cols[0].button("Poista", key=f"remove_trade_mobile_{idx}")
    close_clicked = action_cols[1].button("Sulje", key=f"close_trade_mobile_{idx}", disabled=current_price is None)
    return remove_clicked, close_clicked


st.title("Market Memory")
st.caption("Historiallisten markkinatilanteiden vertailu nykyiseen rakenteeseen")

_ensure_trade_state_loaded()

if "pending_view" in st.session_state:
    st.session_state["view"] = st.session_state.pop("pending_view")

if "pending_ticker" in st.session_state:
    st.session_state["ticker_input"] = st.session_state.pop("pending_ticker")

st.subheader("Asetukset", divider="gray")
top_cols = st.columns(4)
with top_cols[0]:
    if "ticker_input" not in st.session_state:
        st.session_state["ticker_input"] = st.session_state.get("active_ticker", "AAPL")
    ticker_input = st.text_input("Ticker tai yrityksen nimi", value=st.session_state["ticker_input"], max_chars=32, key="ticker_input").strip()
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
    if "pivot_mode_widget" not in st.session_state:
        st.session_state["pivot_mode_widget"] = ticker_settings["pivot_mode"]
    else:
        st.session_state["pending_pivot_mode"] = ticker_settings["pivot_mode"]
    st.session_state["manual_pivot_type_widget"] = ticker_settings["manual_pivot_type"]
    st.session_state["manual_pivot_dates_text_widget"] = ticker_settings["manual_pivot_dates_text"]
    preset_aliases = {
        "pohjan metsästys": "Rebound setup",
        "paniikkipohja": "Rebound setup",
        "huipun metsästys": "Short setup",
        "väsyvä huippu": "Momentum short",
    }
    selected_preset_value = preset_aliases.get(ticker_settings["selected_preset"], ticker_settings["selected_preset"])
    last_applied_value = ticker_settings.get("last_applied_preset", ticker_settings["selected_preset"])
    st.session_state["selected_preset_widget"] = selected_preset_value
    st.session_state["last_applied_preset_widget"] = preset_aliases.get(last_applied_value, last_applied_value)
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

with top_cols[1]:
    view_mode = st.radio("Näkymätila", options=["Desktop", "Mobiili"], key="view_mode", horizontal=True)
with top_cols[2]:
    if "pending_pivot_mode" in st.session_state:
        st.session_state["pivot_mode_widget"] = st.session_state.pop("pending_pivot_mode")
    elif "pivot_mode_widget" not in st.session_state:
        st.session_state["pivot_mode_widget"] = _get_ticker_settings(current_ticker)["pivot_mode"]
    pivot_mode = st.radio(
        "Pivot mode",
        options=["all", "bottom", "peak"],
        horizontal=True,
        disabled=st.session_state.get("pivot_source_widget", "automatic") == "manual",
        key="pivot_mode_widget",
    )
with top_cols[3]:
    preset_placeholder = "Valitse setup-tyyli"
    preset_options = {
        "Rebound setup": {"price": 0.10, "rsi": 0.35, "volume": 0.20, "volatility": 0.25, "trend": 0.10, "pivot_mode": "bottom"},
        "Short setup": {"price": 0.30, "rsi": 0.30, "volume": 0.15, "volatility": 0.15, "trend": 0.10, "pivot_mode": "peak"},
        "Momentum long": {"price": 0.25, "rsi": 0.15, "volume": 0.15, "volatility": 0.10, "trend": 0.35, "pivot_mode": "all"},
        "Momentum short": {"price": 0.25, "rsi": 0.15, "volume": 0.15, "volatility": 0.10, "trend": 0.35, "pivot_mode": "all"},
    }
    preset_select_options = [preset_placeholder, *list(preset_options.keys())]
    if st.session_state.get("selected_preset_widget") not in preset_select_options:
        st.session_state["selected_preset_widget"] = preset_placeholder
    selected_preset = st.selectbox("Setup-tyyli", options=preset_select_options, key="selected_preset_widget")

st.caption("Ticker-kohtaiset asetukset tallennetaan tämän session ajaksi.")
st.caption("### Markkina")
sector = st.selectbox("Sektori", options=list(SECTOR_SETTINGS.keys()), key="sector_widget", on_change=_mark_sector_manual)
st.caption(f"Automaattisesti tunnistettu sektori: {st.session_state.get('auto_sector_name', 'yleinen')}")
st.caption(f"Yahoo: {st.session_state.get('auto_sector_source') or 'Ei saatavilla'}")
st.caption("### Pivot-asetukset")
pivot_source = st.radio("Pivot source", options=["automatic", "manual"], horizontal=True, key="pivot_source_widget")
pivot_detection_method_label = st.session_state.get("pivot_detection_method_ui_widget", "Tarkka pivot")
pivot_detection_method = "reversal zone" if pivot_detection_method_label == "Käännealue" else "exact pivot"
similarity_alert = _resolve_similarity_threshold(pivot_source=pivot_source, pivot_detection_method=pivot_detection_method)

last_applied_preset = st.session_state.get("last_applied_preset_widget")
if selected_preset in preset_options and selected_preset != last_applied_preset:
    preset_weights = preset_options[selected_preset]
    st.session_state["price_weight_widget"] = float(preset_weights["price"])
    st.session_state["rsi_weight_widget"] = float(preset_weights["rsi"])
    st.session_state["volume_weight_widget"] = float(preset_weights["volume"])
    st.session_state["volatility_weight_widget"] = float(preset_weights["volatility"])
    st.session_state["trend_weight_widget"] = float(preset_weights["trend"])
    st.session_state["pivot_mode_widget"] = preset_weights["pivot_mode"]
    st.session_state["last_applied_preset_widget"] = selected_preset

st.caption("Valitse setup-tyyli. Painotuksia voi säätää käsin.")

with st.expander("Lisäasetukset", expanded=False):
    pivot_detection_method_label = st.radio(
        "Käänteen tunnistustapa",
        options=["Tarkka pivot", "Käännealue"],
        horizontal=True,
        disabled=pivot_source != "automatic",
        key="pivot_detection_method_ui_widget",
    )
    pivot_detection_method = "reversal zone" if pivot_detection_method_label == "Käännealue" else "exact pivot"
    similarity_alert = _resolve_similarity_threshold(pivot_source=pivot_source, pivot_detection_method=pivot_detection_method)
    pivot_window = st.select_slider(
        "Osuman merkittävyys",
        options=[5, 10, 15, 20, 30],
        key="pivot_window_widget",
        disabled=pivot_source != "automatic",
    )
    if pivot_source == "manual":
        manual_pivot_type = st.radio("Manual pivot type", options=["bottom", "peak"], horizontal=True, key="manual_pivot_type_widget")
        manual_pivot_dates_text = st.text_area(
            "Manual pivot dates",
            key="manual_pivot_dates_text_widget",
            placeholder="2023-10-04\n2024-10-31\n2025-04-25",
            help="Syötä päivämäärät riveittäin tai pilkulla eroteltuna (YYYY-MM-DD).",
        )
    else:
        manual_pivot_type = st.session_state.get("manual_pivot_type_widget", "bottom")
        manual_pivot_dates_text = st.session_state.get("manual_pivot_dates_text_widget", "")
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
            "selected_preset_legacy": _legacy_setup_name(selected_preset),
            "last_applied_preset": st.session_state.get("last_applied_preset_widget", selected_preset),
            "similarity_weights": similarity_weights,
            "pivot_window": pivot_window,
            "pivot_detection_method": pivot_detection_method,
        },
    )

run = st.button("Suorita analyysi", type="primary", use_container_width=True)

run_from_scanner = bool(st.session_state.pop("run_single_analysis", False))
run = run or run_from_scanner

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
                        kind = _resolve_signal_type(top_match.pivot.pivot_type, selected_preset)
                        st.error(f"Alert status: ALERT — {kind} (top score {top_match.score:.3f})")
                    elif show_alert_status:
                        st.info(f"Alert status: INFO — no alert (top score {top_match.score:.3f})")
                    else:
                        st.caption("Valitse setup-tyyli, jotta alert-status näytetään.")

                    market_state_rows = get_current_market_state(enriched)
                    current_row = enriched.iloc[-1]
                    momentum_emoji, momentum_summary = generate_momentum_summary(
                        current=current_row,
                        top_match=top_match,
                        market_state_rows=market_state_rows,
                        similarity_alert=similarity_alert,
                        selected_preset=selected_preset,
                    )
                    st.markdown(
                        (
                            "<div style='border-left: 8px solid #334155; padding: 0.75rem 1rem; "
                            "border-radius: 0.25rem; background: rgba(148, 163, 184, 0.08); margin-bottom: 0.75rem;'>"
                            "<strong>Momentum Trader</strong><br>"
                            f"{momentum_emoji} {momentum_summary}"
                            "</div>"
                        ),
                        unsafe_allow_html=True,
                    )

                    st.subheader("Nykyinen markkinatila")
                    market_state_df = pd.DataFrame(market_state_rows, columns=["Mittari", "Tila"])
                    st.table(market_state_df)

                    st.subheader("Parhaat historialliset osumat")
                    table = build_matches_table(
                        matches, ticker=ticker, threshold=similarity_alert, pivot_source=pivot_source
                    )
                    display_table = table
                    if view_mode == "Mobiili":
                        display_table = table[
                            ["Ticker", "Similarity", "Pivot date", "Pivot type", "Alert status", "return +5d", "return +15d"]
                        ]
                    table_height = 320 if view_mode == "Mobiili" else min(420, 38 * len(display_table) + 40)
                    st.dataframe(
                        display_table,
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
                    if view_mode == "Mobiili":
                        pivot_map_fig.update_layout(height=320)
                    st.plotly_chart(pivot_map_fig, use_container_width=True)

                    st.subheader("Plotly overlay")
                    current = enriched.iloc[-15:]
                    fig = plot_overlay(current=current, matches=matches)
                    fig.update_layout(legend_title_text=f"Similarity score ({sector})")
                    if view_mode == "Mobiili":
                        fig.update_layout(height=320)
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

                st.subheader("Seuraava tulosjulkistus")
                company_name = _get_company_name(ticker)
                next_earnings_date, _ = fetch_next_earnings_date(ticker, company_name=company_name)
                if next_earnings_date is None:
                    st.info("Seuraavaa tulosjulkistusta ei löytynyt automaattisesti.")
                else:
                    today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
                    days_left = int((next_earnings_date - today).days)

                    if days_left < 7:
                        status_color = "#dc2626"
                    elif days_left < 30:
                        status_color = "#f97316"
                    else:
                        status_color = "#16a34a"

                    st.markdown(
                        (
                            f"<div style='border-left: 8px solid {status_color}; padding: 0.75rem 1rem; "
                            "border-radius: 0.25rem; background: rgba(148, 163, 184, 0.08);'>"
                            f"<strong>Päivämäärä:</strong> {next_earnings_date.date().isoformat()}<br>"
                            f"<strong>Päiviä jäljellä:</strong> {days_left}"
                            "</div>"
                        ),
                        unsafe_allow_html=True,
                    )

                st.subheader("Kvartaalitiedot")
                try:
                    quarterly = run_quarterly_fundamentals_fetch(ticker)
                except Exception:
                    quarterly = pd.DataFrame()

                if quarterly.empty:
                    st.info("Kvartaalitietoja ei löytynyt tälle tickerille.")
                else:
                    quarterly_display = quarterly
                    if view_mode == "Mobiili":
                        keep_cols = [col for col in ["date", "revenue", "eps", "netIncome", "operatingIncome"] if col in quarterly.columns]
                        if keep_cols:
                            quarterly_display = quarterly[keep_cols]
                    st.dataframe(quarterly_display, use_container_width=True, hide_index=True)

                st.subheader("Viimeisimmät uutiset")
                st.caption("Näytetään viimeisen 90 päivän uutiset")
                news_source_note = None
                try:
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


def _analyze_scanner_ticker(
    ticker: str,
    selected_preset: str,
    similarity_alert: float,
    pivot_mode: str,
    pivot_source: str,
    manual_pivot_type: str,
    manual_pivot_dates_text: str,
    similarity_weights: dict[str, float],
    pivot_window: int,
    pivot_detection_method: str,
) -> dict[str, object] | None:
    auto_sector, _ = _resolve_sector_for_ticker(ticker)
    sector = auto_sector if auto_sector in SECTOR_SETTINGS else "yleinen"
    enriched, matches, _ = run_analysis(
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
    if not matches:
        return None

    best = matches[0]
    market_state_rows = dict(get_current_market_state(enriched))
    current = enriched.iloc[-1]
    signal_type = _resolve_signal_type(best.pivot.pivot_type, selected_preset)
    return {
        "ticker": ticker,
        "company_name": _get_company_name(ticker) or "",
        "sector": sector,
        "best_similarity": round(best.score, 3),
        "signal_type": signal_type,
        "RSI": round(float(current.get("RSI14", float("nan"))), 2),
        "trend_state": market_state_rows.get("Trend", "-"),
        "volatility_state": market_state_rows.get("Volatility", "-"),
        "volume_ratio": round(float(current.get("Volume_Ratio", float("nan"))), 2),
        "avg_return_5d": round(float(pd.Series([m.return_plus_5d for m in matches]).dropna().mean()), 2),
        "avg_return_15d": round(float(pd.Series([m.return_plus_15d for m in matches]).dropna().mean()), 2),
    }


if "active_ticker" not in st.session_state:
    st.session_state["active_ticker"] = "AAPL"
if "view" not in st.session_state:
    st.session_state["view"] = "Yksittäinen osake"
if "scanner_results" not in st.session_state:
    st.session_state["scanner_results"] = []

st.radio(
    "Näkymä",
    options=["Yksittäinen osake", "Scanner", "Avoimet tradet"],
    horizontal=True,
    key="view",
)

if st.session_state["view"] == "Scanner":
    st.subheader("Scanner")
    market = st.selectbox("Valitse markkina", options=list(MARKET_TICKERS.keys()), key="scanner_market")
    st.caption(f"Scanning {len(MARKET_TICKERS[market])} liquid stocks")
    if st.button("Suorita scanner", key="run_scanner", use_container_width=True):
        rows: list[dict[str, object]] = []
        skipped: list[str] = []
        analyzed_count = 0
        failed_count = 0
        with st.spinner("Ajetaan scanner..."):
            for scanner_ticker in MARKET_TICKERS[market]:
                analyzed_count += 1
                try:
                    row = _analyze_scanner_ticker(
                        ticker=scanner_ticker,
                        selected_preset=selected_preset,
                        similarity_alert=similarity_alert,
                        pivot_mode=pivot_mode,
                        pivot_source=pivot_source,
                        manual_pivot_type=manual_pivot_type,
                        manual_pivot_dates_text=manual_pivot_dates_text,
                        similarity_weights=similarity_weights,
                        pivot_window=pivot_window,
                        pivot_detection_method=pivot_detection_method,
                    )
                    if row:
                        rows.append(row)
                except Exception:
                    failed_count += 1
                    skipped.append(scanner_ticker)
                    continue
        st.session_state["scanner_results"] = rows
        st.session_state["scanner_stats"] = {
            "analyzed_count": analyzed_count,
            "failed_count": failed_count,
            "skipped": skipped,
        }

    scanner_df = pd.DataFrame(st.session_state.get("scanner_results", []))
    if scanner_df.empty:
        st.info("Suorita scanner nähdäksesi tulokset.")
    else:
        scanner_df = scanner_df.sort_values("best_similarity", ascending=False).reset_index(drop=True)
        scanner_display = scanner_df.rename(
            columns={
                "ticker": "ticker",
                "company_name": "company name",
                "sector": "sector",
                "best_similarity": "best similarity",
                "signal_type": "signal type",
                "trend_state": "trend state",
                "volatility_state": "volatility state",
                "volume_ratio": "volume ratio",
                "avg_return_5d": "avg return +5d",
                "avg_return_15d": "avg return +15d",
            }
        )
        if view_mode == "Mobiili":
            scanner_display = scanner_display[
                ["ticker", "signal type", "best similarity", "avg return +5d", "avg return +15d"]
            ]
            st.dataframe(scanner_display, use_container_width=True, hide_index=True, height=320)
        else:
            st.dataframe(scanner_display, use_container_width=True, hide_index=True)
        ticker_labels = {
            f"{row['ticker']} — {row['company_name'] or row['ticker']}": row["ticker"] for row in scanner_df.to_dict("records")
        }
        selected_label = st.selectbox("Valitse analysoitava ticker", options=list(ticker_labels.keys()), key="scanner_selected_label")
        _, analyze_col = st.columns([3, 1])
        with analyze_col:
            if st.button("Analysoi valittu", key="analyze_selected", use_container_width=True):
                selected_ticker = ticker_labels[selected_label]
                st.session_state["pending_ticker"] = selected_ticker
                st.session_state["active_ticker"] = selected_ticker
                st.session_state["pending_view"] = "Yksittäinen osake"
                st.rerun()

    stats = st.session_state.get("scanner_stats")
    if stats:
        st.caption(f"Analysoitiin {stats['analyzed_count']} osaketta • epäonnistui {stats['failed_count']}")
        if stats["skipped"]:
            st.caption(f"Ohitettiin virheen vuoksi: {', '.join(stats['skipped'])}")


if st.session_state["view"] == "Avoimet tradet":
    _ensure_trade_state_loaded()
    trades = st.session_state["open_trades"]
    closed_trades = st.session_state["closed_trades"]

    st.subheader("Avoimet tradet")

    trade_load_warnings = st.session_state.pop("trade_load_warnings", [])
    for warning_message in trade_load_warnings:
        st.warning(warning_message, icon="⚠️")

    last_updated = st.session_state.get("trades_last_updated")
    if last_updated is not None:
        last_updated_ts = pd.Timestamp(last_updated)
        if last_updated_ts.tzinfo is None:
            last_updated_ts = last_updated_ts.tz_localize("UTC")
        else:
            last_updated_ts = last_updated_ts.tz_convert("UTC")
        last_updated_ts = last_updated_ts.tz_convert(ZoneInfo("Europe/Helsinki"))
        st.caption(f"Viimeksi päivitetty: {last_updated_ts.strftime('%d.%m.%Y %H:%M')}")

    refresh_clicked = st.button("Päivitä hinnat", type="primary")

    if refresh_clicked and trades:
        _refresh_all_open_trades(trades, closed_trades)
        st.success("Avoimien tradejen hinnat päivitetty.")
    elif refresh_clicked:
        st.info("Ei avoimia tradeja päivitettäväksi.")

    export_payload = _build_trade_export_payload(trades, closed_trades)
    st.download_button(
        "Lataa tradet",
        data=json.dumps(export_payload, ensure_ascii=False, indent=2),
        file_name="market_memory_trades.json",
        mime="application/json",
        use_container_width=True,
    )

    imported_trade_file = st.file_uploader("Tuo tradet", type=["json"], key="trade_import_file")
    if imported_trade_file is not None:
        try:
            imported_open_trades, imported_closed_trades = _parse_trade_import_payload(imported_trade_file.getvalue())
        except ValueError:
            st.error("Trade-tiedostoa ei voitu lukea")
        else:
            st.session_state["open_trades"] = imported_open_trades
            st.session_state["closed_trades"] = imported_closed_trades
            _save_trades(imported_open_trades, imported_closed_trades)
            st.success("Tradet tuotu onnistuneesti")
            st.rerun()

    if view_mode == "Mobiili":
        ticker_input_new = st.text_input(
            "Ticker / yritys",
            max_chars=32,
            key="trade_ticker_input_widget",
            placeholder="Kirjoita yrityksen nimi tai ticker",
        ).strip()
    else:
        c1, c2, c3, c4 = st.columns(4)
        ticker_input_new = c1.text_input(
            "Ticker / yritys",
            max_chars=32,
            key="trade_ticker_input_widget",
            placeholder="Kirjoita yrityksen nimi tai ticker",
        ).strip()
    resolved_ticker_new, ticker_candidates_new, ticker_error_new = resolve_ticker_input(ticker_input_new)
    selected_candidate_trade = None
    if ticker_candidates_new and not resolved_ticker_new:
        candidate_options = {
            f"{item['name']} ({item['symbol']}) - {item['location'] or 'N/A'}": item["symbol"]
            for item in ticker_candidates_new
        }
        selected_label_trade = (c1.selectbox if view_mode == "Desktop" else st.selectbox)(
            "Valitse ticker",
            options=["Valitse..."] + list(candidate_options.keys()),
            key="trade_ticker_candidate_widget",
        )
        if selected_label_trade != "Valitse...":
            selected_candidate_trade = candidate_options[selected_label_trade]
    if view_mode == "Mobiili":
        direction_new = st.selectbox("Suunta", options=["long", "short"], key="trade_direction_widget")
        entry_price_new = st.number_input("Entry price", min_value=0.0, value=100.0, step=0.01, key="trade_entry_price_widget")
        leverage_new = st.selectbox("Vipu / leverage", options=[1, 2, 3, 5, 10], index=0, key="trade_leverage_widget")
        entry_date_new = st.date_input("Entry date", key="trade_entry_date_widget")
        stop_loss_new = st.number_input("Stop loss", min_value=0.0, value=95.0, step=0.01, key="trade_stop_loss_widget")
        target_price_new = st.number_input("Target price", min_value=0.0, value=110.0, step=0.01, key="trade_target_price_widget")
        position_size_new = st.number_input("Position size", min_value=0.0, value=1.0, step=0.01, key="trade_position_size_widget")
    else:
        direction_new = c2.selectbox("Suunta", options=["long", "short"], key="trade_direction_widget")
        entry_price_new = c3.number_input("Entry price", min_value=0.0, value=100.0, step=0.01, key="trade_entry_price_widget")
        leverage_new = c4.selectbox("Vipu / leverage", options=[1, 2, 3, 5, 10], index=0, key="trade_leverage_widget")

        c5, c6, c7, c8 = st.columns(4)
        entry_date_new = c5.date_input("Entry date", key="trade_entry_date_widget")
        stop_loss_new = c6.number_input("Stop loss", min_value=0.0, value=95.0, step=0.01, key="trade_stop_loss_widget")
        target_price_new = c7.number_input("Target price", min_value=0.0, value=110.0, step=0.01, key="trade_target_price_widget")
        position_size_new = c8.number_input("Position size", min_value=0.0, value=1.0, step=0.01, key="trade_position_size_widget")

    submit_trade = st.button("Lisää trade", type="primary")
    if submit_trade:
        if not ticker_input_new:
            st.error("Ticker on pakollinen.")
        elif entry_price_new <= 0:
            st.error("Entry price pitää olla suurempi kuin 0.")
        else:
            resolved_ticker_new, resolve_error = _resolve_trade_ticker(ticker_input_new, selected_candidate_trade)
            if ticker_error_new and not ticker_candidates_new:
                st.error(ticker_error_new)
            elif not resolved_ticker_new:
                st.error(resolve_error or "Tickeriä ei voitu ratkaista.")
            else:
                trades.append(
                    {
                        "ticker_input": ticker_input_new,
                        "ticker": resolved_ticker_new,
                        "direction": direction_new,
                        "entry_price": float(entry_price_new),
                        "entry_date": str(entry_date_new),
                        "stop_loss": float(stop_loss_new),
                        "target_price": float(target_price_new),
                        "position_size": float(position_size_new),
                        "leverage": int(leverage_new),
                    }
                )
                _refresh_open_trade(trades[-1], refresh_key=pd.Timestamp.now(tz="UTC").isoformat())
                st.session_state["trades_last_updated"] = pd.Timestamp.now(tz="UTC")
                _save_trades(trades, closed_trades)
                st.success(f"Trade lisätty: {ticker_input_new} -> {resolved_ticker_new} ({direction_new})")

    if not trades:
        st.info("Ei avoimia tradeja vielä.")
    else:
        remove_index = None
        close_index = None

        if view_mode == "Desktop":
            table_columns = [
                "Toiminnot", "Ticker", "Suunta", "Vipu", "Entry", "Current", "P/L %",
                "Stop", "Target", "R/R", "Status", "Syy",
            ]
            header_cols = st.columns([2.2, 1.1, 0.9, 0.7, 1, 1, 0.9, 1, 1, 0.8, 1, 1.4])
            for col, header in zip(header_cols, table_columns):
                col.markdown(f"**{header}**")

        for idx, trade in enumerate(trades):
            ticker_symbol = str(trade.get("ticker") or "")
            direction = str(trade["direction"])
            entry = float(trade["entry_price"])
            stop_loss = float(trade["stop_loss"])
            target_price = float(trade["target_price"])
            leverage = int(trade.get("leverage", 1))

            current_price = trade.get("current_price")
            pl_pct = trade.get("pl_pct")
            rr = trade.get("risk_reward")
            status = str(trade.get("status") or "NO PRICE")
            status_reason = str(trade.get("status_reason") or _calc_trade_status_reason(status))

            if view_mode == "Mobiili":
                remove_clicked, close_clicked = _render_trade_mobile_card(trade=trade, idx=idx)
                if remove_clicked:
                    remove_index = idx
                if close_clicked:
                    close_index = idx
                close_disabled = current_price is None
            else:
                row_cols = st.columns([2.2, 1.1, 0.9, 0.7, 1, 1, 0.9, 1, 1, 0.8, 1, 1.4])
                action_cols = row_cols[0].columns(2)
                if action_cols[0].button("Poista", key=f"remove_trade_{idx}"):
                    remove_index = idx
                close_disabled = current_price is None
                if action_cols[1].button("Sulje", key=f"close_trade_{idx}", disabled=close_disabled):
                    close_index = idx

                row_cols[1].write(ticker_symbol)
                row_cols[2].write(direction)
                row_cols[3].write(leverage)
                row_cols[4].write(round(entry, 4))
                row_cols[5].write(round(current_price, 4) if current_price is not None else "-")
                row_cols[6].write(round(pl_pct, 2) if pl_pct is not None else "-")
                row_cols[7].write(round(stop_loss, 4))
                row_cols[8].write(round(target_price, 4))
                row_cols[9].write(round(rr, 2) if rr is not None else "-")
                row_cols[10].write(status)
                row_cols[11].write(status_reason)

            if close_disabled:
                st.caption(f"{ticker_symbol}: NO PRICE – Tradea ei voi sulkea ilman nykyhintaa.")

        if remove_index is not None:
            removed = trades.pop(remove_index)
            _save_trades(trades, closed_trades)
            st.success(f"Trade poistettu: {removed.get('ticker_input', removed.get('ticker', ''))}")
            st.rerun()

        if close_index is not None:
            closed = trades.pop(close_index)
            close_price = _fetch_latest_price(str(closed.get("ticker") or ""))
            if close_price is None:
                st.warning("NO PRICE: Tradea ei voitu sulkea juuri nyt.")
                trades.insert(close_index, closed)
            else:
                entry = float(closed["entry_price"])
                lev = int(closed.get("leverage", 1))
                if str(closed["direction"]) == "long":
                    final_pl = ((close_price - entry) / entry) * 100 * lev if entry != 0 else None
                else:
                    final_pl = ((entry - close_price) / entry) * 100 * lev if entry != 0 else None
                closed_trades.append(closed | {
                    "close_price": float(close_price),
                    "close_date": str(pd.Timestamp.now(tz="UTC").date()),
                    "final_pl_pct": final_pl,
                })
                _save_trades(trades, closed_trades)
                st.success("Trade suljettu.")
            st.rerun()

    st.subheader("Suljetut tradet")
    if not closed_trades:
        st.info("Ei suljettuja tradeja vielä.")
    else:
        closed_rows = []
        for trade in closed_trades:
            closed_rows.append({
                "user ticker": str(trade.get("ticker_input") or ""),
                "resolved ticker": str(trade.get("ticker") or ""),
                "suunta": str(trade.get("direction") or ""),
                "leverage": int(trade.get("leverage", 1)),
                "entry": round(float(trade.get("entry_price", 0.0)), 4),
                "close price": round(float(trade.get("close_price", 0.0)), 4),
                "close date": str(trade.get("close_date") or ""),
                "final P/L %": round(float(trade.get("final_pl_pct")), 2) if trade.get("final_pl_pct") is not None else None,
            })
        st.dataframe(pd.DataFrame(closed_rows), use_container_width=True, hide_index=True)
