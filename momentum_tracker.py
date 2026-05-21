import json
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from kite_analytics import build_metric_values, load_analytics_history
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error

st.set_page_config(layout="wide")

SUPABASE_INDICES_TABLE_NAME = "Indices_constituents"
CARD_COLUMNS = 3


def _supabase_headers(supabase_key: str) -> dict[str, str]:
    return {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }


@st.cache_data(ttl=24 * 60 * 60)
def load_instrument_token_from_supabase(tickers: list[str]) -> pd.DataFrame:
    normalized_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not normalized_tickers:
        return pd.DataFrame()

    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in Streamlit secrets or environment variables."
        )

    ticker_filter = ",".join(f"tradingsymbol.eq.{quote(ticker, safe='')}" for ticker in normalized_tickers)
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?select=*&or=({ticker_filter})"
    )
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase instrument lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase instrument lookup failed: {exc.reason}") from exc

    instrument_token_df = pd.DataFrame(records)
    if instrument_token_df.empty:
        return pd.DataFrame(columns=["tradingsymbol", "instrument_token"])

    if "tradingsymbol" in instrument_token_df.columns:
        instrument_token_df["tradingsymbol"] = (
            instrument_token_df["tradingsymbol"].astype(str).str.strip().str.upper()
        )
    return instrument_token_df


@st.cache_data(ttl=24 * 60 * 60)
def load_indices_from_supabase() -> dict[str, str]:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_INDICES_TABLE_NAME").strip() or SUPABASE_INDICES_TABLE_NAME

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in Streamlit secrets or environment variables."
        )

    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        "?select=Index,Constituents&order=Index.asc"
    )
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase indices lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase indices lookup failed: {exc.reason}") from exc

    indices: dict[str, str] = {}
    for record in records:
        index_name = str(record.get("Index") or "").strip()
        constituents = str(record.get("Constituents") or "").strip()
        if index_name and constituents:
            indices[index_name] = constituents
    return indices


def resolve_tokens_from_tickers(tickers: list[str], instruments_df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    if instruments_df.empty or "tradingsymbol" not in instruments_df.columns:
        return [], tickers

    resolved: list[dict] = []
    missing: list[str] = []
    normalized = instruments_df.copy()
    normalized["tradingsymbol"] = normalized["tradingsymbol"].astype(str).str.strip().str.upper()

    for ticker in tickers:
        matches = normalized[normalized["tradingsymbol"] == ticker]
        if matches.empty:
            missing.append(ticker)
            continue
        resolved.append(
            {
                "symbol": ticker,
                "instrument_token": int(matches.iloc[0]["instrument_token"]),
            }
        )
    return resolved, missing


def _as_float(value: Any) -> float | None:
    converted = pd.to_numeric(value, errors="coerce")
    if pd.isna(converted):
        return None
    return float(converted)


def calculate_range_position(levels: dict[str, Any]) -> float | None:
    lows = [_as_float(value) for key, value in levels.items() if "Low" in key]
    highs = [_as_float(value) for key, value in levels.items() if "High" in key]
    lows = [value for value in lows if value is not None]
    highs = [value for value in highs if value is not None]
    ltp = _as_float(levels.get("LTP"))

    if ltp is None or not lows or not highs:
        return None

    range_low = min(lows)
    range_high = max(highs)
    if range_high == range_low:
        return None

    position = ((ltp - range_low) / (range_high - range_low)) * 100
    return round(min(max(position, 0), 100), 1)


def calculate_distance_pct(ltp: Any, reference_value: Any) -> float | None:
    ltp_value = _as_float(ltp)
    reference = _as_float(reference_value)
    if ltp_value is None or reference is None or reference == 0:
        return None
    return round(((ltp_value - reference) / reference) * 100, 2)


def get_trend_label(levels: dict[str, Any]) -> str:
    ltp = _as_float(levels.get("LTP"))
    ema20 = _as_float(levels.get("EMA20"))
    ema50 = _as_float(levels.get("EMA50"))
    ema100 = _as_float(levels.get("EMA100"))
    ema200 = _as_float(levels.get("EMA200"))

    if None in [ltp, ema20, ema200]:
        return "Unknown"
    if None not in [ema50, ema100] and ltp > ema20 > ema50 > ema100 > ema200:
        return "Strong Bullish"
    if ltp > ema20 and ltp > ema200:
        return "Bullish"
    if ltp < ema20 and ltp > ema200:
        return "Pullback"
    if ltp < ema20 and ltp < ema200:
        return "Weak"
    return "Unknown"


def _trend_color(label: str) -> str:
    return {
        "Strong Bullish": "#15803d",
        "Bullish": "#16a34a",
        "Pullback": "#b45309",
        "Weak": "#dc2626",
        "Unknown": "#6b7280",
    }.get(label, "#6b7280")


def _nearest_level(levels: dict[str, Any], label_fragment: str, ltp: float) -> tuple[str, float] | None:
    candidates: list[tuple[str, float]] = []
    for label, value in levels.items():
        if label_fragment not in label:
            continue
        numeric_value = _as_float(value)
        if numeric_value is not None:
            candidates.append((label, numeric_value))
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs(item[1] - ltp))


def build_structure_bar_figure(levels: dict[str, Any]) -> go.Figure:
    ltp = _as_float(levels.get("LTP"))
    fig = go.Figure()

    if ltp is None:
        fig.update_layout(height=105, margin=dict(l=6, r=6, t=4, b=4))
        return fig

    nearest_low = _nearest_level(levels, "Low", ltp)
    nearest_high = _nearest_level(levels, "High", ltp)
    if not nearest_low or not nearest_high or nearest_high[1] == nearest_low[1]:
        fig.update_layout(height=105, margin=dict(l=6, r=6, t=4, b=4))
        return fig

    range_low = min(nearest_low[1], nearest_high[1])
    range_high = max(nearest_low[1], nearest_high[1])
    range_width = range_high - range_low

    def normalize(value: float) -> float:
        return ((value - range_low) / range_width) * 100

    ltp_position = normalize(ltp)
    ltp_marker_position = min(max(ltp_position, 0), 100)

    fig.add_trace(
        go.Scatter(
            x=[0, 100],
            y=[0, 0],
            mode="lines",
            line=dict(color="#cbd5e1", width=14),
            hoverinfo="none",
            showlegend=False,
        )
    )

    ema_specs: list[tuple[str, float, str]] = []
    for label, color in [
        ("EMA20", "#f59e0b"),
        ("EMA50", "#64748b"),
        ("EMA100", "#475569"),
        ("EMA200", "#111827"),
    ]:
        value = _as_float(levels.get(label))
        if value is not None and range_low <= value <= range_high:
            ema_specs.append((label, value, color))

    for label, value, color in ema_specs:
        fig.add_trace(
            go.Scatter(
                x=[normalize(value)],
                y=[0],
                mode="markers",
                marker=dict(color=color, size=9, symbol="line-ns", line=dict(color="white", width=1)),
                name=label,
                text=[f"{label}: {value:.2f}"],
                hovertemplate="%{text}<extra></extra>",
                showlegend=False,
            )
        )

    fig.add_trace(
        go.Scatter(
            x=[ltp_marker_position, ltp_marker_position],
            y=[-0.42, 0.42],
            mode="lines+markers+text",
            line=dict(color="#dc2626", width=3),
            marker=dict(color="#dc2626", size=9),
            text=["", f"LTP {ltp:.2f}<br>{ltp_position:.1f}%"],
            textposition="top center",
            textfont=dict(color="#991b1b", size=11),
            name="LTP",
            hovertemplate=f"LTP: {ltp:.2f}<br>Position: {ltp_position:.1f}%<extra></extra>",
            showlegend=False,
        )
    )

    annotations = [
        dict(
            x=0,
            y=-0.48,
            text=f"Low {range_low:.2f}",
            showarrow=False,
            font=dict(size=10, color="#1d4ed8"),
            xanchor="left",
        ),
        dict(
            x=100,
            y=-0.48,
            text=f"High {range_high:.2f}",
            showarrow=False,
            font=dict(size=10, color="#6d28d9"),
            xanchor="right",
        )
    ]
    for label, value, color in ema_specs:
        annotations.append(
            dict(
                x=normalize(value),
                y=0.42,
                text=label.replace("EMA", "E"),
                showarrow=False,
                font=dict(size=9, color=color),
                xanchor="center",
                yanchor="bottom",
            )
        )

    fig.update_layout(
        height=112,
        margin=dict(l=8, r=8, t=28, b=10),
        xaxis=dict(
            range=[-3, 103],
            visible=False,
            showgrid=False,
            zeroline=False,
        ),
        yaxis=dict(visible=False, range=[-0.7, 0.7]),
        annotations=annotations,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _format_currency(value: Any) -> str:
    numeric_value = _as_float(value)
    if numeric_value is None:
        return "-"
    return f"{numeric_value:,.2f}"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def render_stock_card(symbol: str, levels: dict[str, Any]) -> None:
    ltp = levels.get("LTP")
    trend_label = get_trend_label(levels)
    range_position = calculate_range_position(levels)
    ema20_distance = calculate_distance_pct(ltp, levels.get("EMA20"))
    ema200_distance = calculate_distance_pct(ltp, levels.get("EMA200"))
    trend_color = _trend_color(trend_label)

    with st.container(border=True):
        header_col, ltp_col = st.columns([1.2, 1])
        header_col.markdown(f"**{symbol}**")
        ltp_col.markdown(f"<div style='text-align:right;font-weight:700'>Rs {_format_currency(ltp)}</div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:-0.25rem;">
              <span style="color:{trend_color};font-weight:700;font-size:0.9rem;">{trend_label}</span>
              <span style="color:{trend_color};font-size:0.9rem;">Range: {range_position if range_position is not None else "-"}%</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.plotly_chart(build_structure_bar_figure(levels), width="stretch", config={"displayModeBar": False})
        st.caption(f"EMA20 {_format_pct(ema20_distance)} | EMA200 {_format_pct(ema200_distance)}")
        with st.expander("Raw levels"):
            raw_df = pd.DataFrame(
                [{"Level": key, "Value": value} for key, value in sorted(levels.items())]
            )
            st.dataframe(raw_df, width="stretch", hide_index=True)


def render_cards_grid(cards: list[dict]) -> None:
    if not cards:
        st.info("No momentum data to display.")
        return

    for start in range(0, len(cards), CARD_COLUMNS):
        columns = st.columns(CARD_COLUMNS)
        for column, card in zip(columns, cards[start : start + CARD_COLUMNS]):
            with column:
                render_stock_card(card["symbol"], card["levels"])


def build_momentum_cards(kite, token_rows: list[dict]) -> tuple[list[dict], list[str]]:
    as_of_date = datetime.now().date().isoformat()
    cards: list[dict] = []
    failed_symbols: list[str] = []

    for row in token_rows:
        symbol = str(row.get("symbol") or row.get("tradingsymbol") or "").strip().upper()
        token = row.get("instrument_token")
        if not symbol or pd.isna(token):
            continue
        try:
            analytics_df = load_analytics_history(kite, token, as_of_date)
            levels = build_metric_values(analytics_df)
        except Exception:
            failed_symbols.append(symbol)
            continue
        if levels:
            cards.append({"symbol": symbol, "levels": levels})
        else:
            failed_symbols.append(symbol)
    return cards, failed_symbols


def _parse_tickers(tickers_input: str) -> list[str]:
    return [item.strip().upper() for item in tickers_input.split(",") if item.strip()]


def _run_custom_or_index_tracker(kite, tickers: list[str]) -> None:
    if not tickers:
        st.warning("Enter at least one ticker symbol.")
        return

    instruments_df = load_instrument_token_from_supabase(tickers)
    token_rows, missing_tickers = resolve_tokens_from_tickers(tickers, instruments_df)
    if missing_tickers:
        st.warning("Skipped tickers with no instrument token: " + ", ".join(missing_tickers[:15]))
    cards, failed_symbols = build_momentum_cards(kite, token_rows)
    if failed_symbols:
        st.warning("Could not load momentum data for: " + ", ".join(failed_symbols[:15]))
    st.session_state["momentum_cards"] = cards


def main() -> None:
    st.title("Momentum Tracker")

    try:
        indices = load_indices_from_supabase()
    except Exception as exc:
        indices = {}
        st.warning(f"Could not load index constituents: {exc}")

    source = st.radio(
        "Source",
        ["Kite holdings", "Index constituents", "Custom tickers"],
        horizontal=True,
    )

    try:
        kite = None
        if source == "Kite holdings":
            if st.button("Fetch Kite holdings", type="primary"):
                kite, _, _ = bootstrap_kite_app("Momentum Tracker")
                holdings = kite.holdings()
                token_rows = [
                    {
                        "symbol": row.get("tradingsymbol"),
                        "instrument_token": row.get("instrument_token"),
                    }
                    for row in holdings
                ]
                cards, failed_symbols = build_momentum_cards(kite, token_rows)
                if failed_symbols:
                    st.warning("Could not load momentum data for: " + ", ".join(failed_symbols[:15]))
                st.session_state["momentum_cards"] = cards

        elif source == "Index constituents":
            index_names = list(indices.keys())
            selected_index = st.selectbox("Select index", index_names) if index_names else None
            if st.button("Build tracker", type="primary", disabled=not selected_index):
                kite, _, _ = bootstrap_kite_app("Momentum Tracker")
                tickers = _parse_tickers(indices.get(selected_index, ""))
                _run_custom_or_index_tracker(kite, tickers)

        else:
            tickers_input = st.text_area(
                "Tickers",
                help="Enter one or more stock ticker symbols separated by commas.",
                placeholder="RELIANCE, INFY, TCS",
            )
            if st.button("Build tracker", type="primary"):
                kite, _, _ = bootstrap_kite_app("Momentum Tracker")
                _run_custom_or_index_tracker(kite, _parse_tickers(tickers_input))

    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your Kite session expired. Please login again.")
            st.rerun()
        st.error(f"Could not build momentum tracker: {exc}")

    render_cards_grid(st.session_state.get("momentum_cards", []))


if __name__ == "__main__":
    main()
