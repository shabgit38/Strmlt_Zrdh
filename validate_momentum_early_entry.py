import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_analytics import load_analytics_history
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error
from momentum_early_entry import calculate_early_entry_frame
from momentum_score import calculate_momentum_scores_from_kite


DEFAULT_BENCHMARK_SYMBOL = "NIFTY 50"
SUPABASE_INDICES_TABLE_NAME = "Indices_constituents"


@st.cache_data(ttl=24 * 60 * 60)
def load_index_constituents() -> pd.DataFrame:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_INDICES_TABLE_NAME").strip() or SUPABASE_INDICES_TABLE_NAME

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        "?select=Index,Constituents&order=Index.asc"
    )
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    request = Request(endpoint, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase indices lookup failed with HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase indices lookup failed: {exc.reason}") from exc

    return pd.DataFrame(records)


@st.cache_data(ttl=24 * 60 * 60)
def load_instrument_rows(symbols: list[str]) -> pd.DataFrame:
    normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    if not normalized_symbols:
        return pd.DataFrame()

    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    symbol_filter = ",".join(f"tradingsymbol.eq.{quote(symbol, safe='')}" for symbol in normalized_symbols)
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?select=tradingsymbol,instrument_token,exchange&or=({symbol_filter})"
    )
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    request = Request(endpoint, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase instrument lookup failed with HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase instrument lookup failed: {exc.reason}") from exc

    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["tradingsymbol", "instrument_token", "exchange"])
    df["tradingsymbol"] = df["tradingsymbol"].astype(str).str.strip().str.upper()
    if "exchange" in df.columns:
        df["exchange"] = df["exchange"].astype(str).str.strip().str.upper()
    return df


def index_map(indices_df: pd.DataFrame) -> dict[str, list[str]]:
    indices: dict[str, list[str]] = {}
    if indices_df.empty:
        return indices
    for _, row in indices_df.iterrows():
        index_name = str(row.get("Index") or "").strip()
        constituents = str(row.get("Constituents") or "")
        symbols = list(dict.fromkeys(item.strip().upper() for item in constituents.split(",") if item.strip()))
        if index_name and symbols:
            indices[index_name] = symbols
    return indices


def resolve_token(symbol: str, instruments_df: pd.DataFrame) -> int | None:
    symbol = symbol.strip().upper()
    if instruments_df.empty or "tradingsymbol" not in instruments_df.columns:
        return None
    matches = instruments_df[instruments_df["tradingsymbol"] == symbol]
    if matches.empty:
        return None
    if "exchange" in matches.columns:
        nse_matches = matches[matches["exchange"] == "NSE"]
        if not nse_matches.empty:
            matches = nse_matches
    token = pd.to_numeric(matches.iloc[0]["instrument_token"], errors="coerce")
    return int(token) if pd.notna(token) else None


def build_token_rows(symbols: list[str], instruments_df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    missing: list[str] = []
    for symbol in symbols:
        token = resolve_token(symbol, instruments_df)
        if token is None:
            missing.append(symbol)
        else:
            rows.append({"Ticker": symbol, "instrument_token": token})
    return rows, missing


def fetch_stock_history(kite, token_rows: list[dict], as_of_date: str) -> tuple[dict[str, pd.DataFrame], list[str]]:
    stock_data: dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    for row in token_rows:
        symbol = str(row.get("Ticker") or "").strip().upper()
        token = row.get("instrument_token")
        try:
            history = load_analytics_history(kite, token, as_of_date)
        except Exception:
            failed.append(symbol)
            continue
        if history.empty:
            failed.append(symbol)
            continue
        stock_data[symbol] = history
    return stock_data, failed


def build_display_frame(momentum_df: pd.DataFrame, early_df: pd.DataFrame) -> pd.DataFrame:
    if momentum_df.empty:
        return early_df
    merged = momentum_df.merge(early_df, on="ticker", how="left")
    visible_columns = [
        "ticker",
        "ltp",
        "latest_close",
        "pullback_score",
        "entry_signal",
        "momentum_state",
        "entry_setup",
        "entry_trigger",
        "trigger_age",
        "extended",
        "recent_pullback",
        "days_since_pullback",
        "ema20_distance_pct",
        "dist_60d_high_pct",
        "dist_ath_pct",
        "prev_week_high",
        "prev_month_high",
        "high_20d",
        "high_60d",
        "volume_ratio",
        "volume_ratio_entry",
        "weekly_breakout",
        "monthly_breakout",
        "ema20_bounce",
        "ema50_reclaim",
        "pullback_pickup",
        "data_status",
    ]
    return merged[[column for column in visible_columns if column in merged.columns]]


st.set_page_config(layout="wide")
st.title("Momentum Early Entry Validation")
st.caption("Experimental real-OHLC validator. Displays a table only and does not write to dashboard files.")

if "request_token" in st.query_params and "access_token" not in st.session_state:
    bootstrap_kite_app("Momentum Early Entry Validation")

kite, _, _ = bootstrap_kite_app("Momentum Early Entry Validation")

try:
    indices_df = load_index_constituents()
    indices = index_map(indices_df)
except Exception as exc:
    indices = {}
    st.error(f"Could not load index constituents: {exc}")

index_names = list(indices.keys())
selected_index = st.selectbox(
    "Select index",
    index_names,
    index=(index_names.index("Main indices") if "Main indices" in index_names else 0) if index_names else None,
    disabled=not bool(index_names),
)
benchmark_symbol = st.text_input("Momentum benchmark", value=DEFAULT_BENCHMARK_SYMBOL)
limit = st.number_input("Max symbols to fetch", min_value=1, max_value=500, value=75, step=25)

if selected_index:
    selected_symbols = indices.get(selected_index, [])[: int(limit)]
    st.caption(f"{len(selected_symbols)} symbols selected from {selected_index}.")
else:
    selected_symbols = []

if st.button("Run Early Entry Validation", type="primary", disabled=not bool(selected_symbols)):
    benchmark = benchmark_symbol.strip().upper()
    if not benchmark:
        st.warning("Enter a benchmark symbol.")
        st.stop()

    try:
        as_of_date = datetime.now().date().isoformat()
        with st.spinner("Resolving symbols and fetching real Kite OHLC..."):
            instruments_df = load_instrument_rows(selected_symbols + [benchmark])
            token_rows, missing_symbols = build_token_rows(selected_symbols, instruments_df)
            benchmark_token = resolve_token(benchmark, instruments_df)

            if missing_symbols:
                st.warning(f"Missing tokens: {', '.join(missing_symbols[:25])}{'...' if len(missing_symbols) > 25 else ''}")
            if benchmark_token is None:
                st.error(f"Missing benchmark token: {benchmark}")
                st.stop()
            if not token_rows:
                st.error("No stock tokens resolved.")
                st.stop()

            momentum_df, momentum_failed = calculate_momentum_scores_from_kite(
                kite,
                token_rows,
                benchmark_token,
                as_of_date,
            )
            stock_data, history_failed = fetch_stock_history(kite, token_rows, as_of_date)
            early_df = calculate_early_entry_frame(stock_data, momentum_df)
            display_df = build_display_frame(momentum_df, early_df)

        st.session_state["early_entry_validation_df"] = display_df
        st.session_state["early_entry_validation_failed"] = sorted(set(momentum_failed + history_failed))
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again.")
            st.rerun()
        st.error(f"Early entry validation failed: {exc}")

failed = st.session_state.get("early_entry_validation_failed", [])
if failed:
    st.warning(f"Failed symbols: {', '.join(failed[:25])}{'...' if len(failed) > 25 else ''}")

display_df = st.session_state.get("early_entry_validation_df")
if isinstance(display_df, pd.DataFrame) and not display_df.empty:
    st.dataframe(
        display_df.style.format(
            {
                "ltp": "{:.2f}",
                "latest_close": "{:.2f}",
                "pullback_score": "{:.1f}",
                "trigger_age": "{:.0f}",
                "days_since_pullback": "{:.0f}",
                "ema20_distance_pct": "{:.2f}%",
                "dist_60d_high_pct": "{:.2f}%",
                "dist_ath_pct": "{:.2f}%",
                "prev_week_high": "{:.2f}",
                "prev_month_high": "{:.2f}",
                "high_20d": "{:.2f}",
                "high_60d": "{:.2f}",
                "volume_ratio": "{:.2f}",
                "volume_ratio_entry": "{:.2f}",
            },
            na_rep="-",
        ),
        width="stretch",
        hide_index=True,
    )
