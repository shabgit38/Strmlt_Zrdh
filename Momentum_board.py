import streamlit as st
import pandas as pd

from getHistoricData import (
    get_stock_data,
    get_weekly_close,
    get_monthly_close,
    get_high_low_resampled,
    get_weekly_ohlc,
)
from indicators import (
    add_ema,
    pivot_points,
    weekly_pivot_points,
    momentum_score,
    momentum_label,
    momentum_summary,
)



# TICKER PARSING
# Appends .NS suffix for NSE India.  User may also type the suffix explicitly
# (e.g. RELIANCE.NS) — we detect and skip double-suffixing.


tickers = [normalise_ticker(t) for t in raw_input.split(",") if t.strip()]

if not tickers:
    st.warning("Enter at least one ticker symbol.")
    st.stop()

# LAYOUT CONSTANTS
EMA_COLS = ["EMA10", "EMA20", "EMA50", "EMA100", "EMA200"]
ROW_PX   = 35    # approximate height of one table row in pixels
HDR_PX   = 38    # table header height in pixels


# HELPER: INSERT CURRENT PRICE ROW
# Given a list of (label, value) sorted descending by value, inserts a
# "▶ Current" row adjacent to whichever level is numerically closest.

def _insert_current(ordered: list[tuple[str, float]], current_price: float) -> list[dict]:
    closest = min(range(len(ordered)), key=lambda i: abs(ordered[i][1] - current_price))
    rows = []
    for i, (label, val) in enumerate(ordered):
        if i == closest:
            if current_price >= val:
                rows.append({"Level": "▶ Current", "Value": round(current_price, 2)})
                rows.append({"Level": label,        "Value": round(val, 2)})
            else:
                rows.append({"Level": label,        "Value": round(val, 2)})
                rows.append({"Level": "▶ Current",  "Value": round(current_price, 2)})
        else:
            rows.append({"Level": label, "Value": round(val, 2)})
    return rows


# TABLE BUILDERS
# Each function returns a styled DataFrame ready to pass to st.dataframe().

def build_ohlc_ema_table(df, current_price: float) -> pd.DataFrame:
    """OHLC of last session + all 5 EMAs, sorted descending with ▶ Current."""
    last = df.iloc[-1]
    items = {
        "Open":   round(float(last["Open"]),   2),
        "High":   round(float(last["High"]),   2),
        "Low":    round(float(last["Low"]),    2),
        "EMA10":  round(float(last["EMA10"]),  2),
        "EMA20":  round(float(last["EMA20"]),  2),
        "EMA50":  round(float(last["EMA50"]),  2),
        "EMA100": round(float(last["EMA100"]), 2),
        "EMA200": round(float(last["EMA200"]), 2),
    }
    ordered = sorted(items.items(), key=lambda x: x[1], reverse=True)
    return pd.DataFrame(_insert_current(ordered, current_price)).set_index("Level")


def build_pivot_table(pivots: dict, current_price: float) -> pd.DataFrame:
    """
    Build a pivot levels table (daily or weekly) from a precomputed pivot dict.
    Sorted descending R5→S5 with ▶ Current inserted at the right position.
    """
    ordered = [
        ("R5",    pivots["R5"]),
        ("R4",    pivots["R4"]),
        ("R3",    pivots["R3"]),
        ("R2",    pivots["R2"]),
        ("R1",    pivots["R1"]),
        ("Pivot", pivots["Pivot"]),
        ("S1",    pivots["S1"]),
        ("S2",    pivots["S2"]),
        ("S3",    pivots["S3"]),
        ("S4",    pivots["S4"]),
        ("S5",    pivots["S5"]),
    ]
    return pd.DataFrame(_insert_current(ordered, current_price)).set_index("Level")


def build_hl_table(df, current_price: float) -> pd.DataFrame:
    """
    Period High/Low table for 1W, 1M, 3M, 6M, 1Y.
    Adds a '% Range' column showing where current price sits within each
    period's H/L range (0% = at low, 100% = at high).
    Weekly and monthly closes are included as reference points.
    """
    wc  = get_weekly_close(df)
    mc  = get_monthly_close(df)
    hl  = get_high_low_resampled(df)

    # Build the price-ladder entries for ▶ Current insertion
    items: dict[str, float] = {}
    if wc is not None:
        items["W Close"] = round(wc, 2)
    if mc is not None:
        items["M Close"] = round(mc, 2)
    for period in ["1W", "1M", "3M", "6M", "1Y"]:
        h, l = hl[period]
        items[f"{period} High"] = round(float(h), 2)
        items[f"{period} Low"]  = round(float(l), 2)

    ordered = sorted(items.items(), key=lambda x: x[1], reverse=True)
    rows    = _insert_current(ordered, current_price)
    df_out  = pd.DataFrame(rows).set_index("Level")

    # For each period High/Low pair, compute where current price sits in that
    # range as a percentage.  Non-period rows (W Close, M Close, ▶ Current)
    # get "—" as they are single prices, not ranges.
    pct_map: dict[str, str] = {}
    for period in ["1W", "1M", "3M", "6M", "1Y"]:
        h, l = hl[period]
        rng  = float(h) - float(l)
        if rng > 0:
            pct = (current_price - float(l)) / rng * 100
            pct_map[f"{period} High"] = f"{pct:.1f}%"
            pct_map[f"{period} Low"]  = f"{pct:.1f}%"
        else:
            pct_map[f"{period} High"] = "—"
            pct_map[f"{period} Low"]  = "—"

    df_out["% Range"] = [pct_map.get(lvl, "—") for lvl in df_out.index]
    return df_out


# SCORE BREAKDOWN TABLE
# Converts the momentum_score() dict into a two-column DataFrame for display
# in the expandable breakdown section of each ticker card.

def build_score_breakdown(score_dict: dict) -> pd.DataFrame:
    """
    Return a DataFrame summarising each scoring component with its value
    and the maximum possible contribution.
    """
    rows = [
        {"Component": "C1  Price vs EMAs",      "Score": score_dict["c1_ema_pos"],      "Max": "±5"},
        {"Component": "C2  EMA Stack Order",     "Score": score_dict["c2_ema_stack"],    "Max": "±3"},
        {"Component": "C3  Daily Pivot Zone",    "Score": score_dict["c3_daily_pivot"],  "Max": "±4"},
        {"Component": "C4  Weekly Pivot Zone",   "Score": score_dict["c4_weekly_pivot"], "Max": "±2"},
        {"Component": "C5  6M Range Position",   "Score": score_dict["c5_6m_range"],     "Max": "±3"},
        {"Component": "C6  3M Range Position",   "Score": score_dict["c6_3m_range"],     "Max": "±2"},
        {"Component": "C7  1M Range Position",   "Score": score_dict["c7_1m_range"],     "Max": "±1"},
        {"Component": "TOTAL",                   "Score": score_dict["total"],           "Max": "±20"},
    ]
    return pd.DataFrame(rows).set_index("Component")


# SUMMARY LINE  (text-based position description, shown above tables)

def generate_summary(df, current_price: float, momentum_text: str) -> str:
    """
    Human-readable momentum score explanation text.
    """
    return momentum_text

# DATA FETCH LOOP
# Fetches data for all tickers, stores results keyed by ticker.
# Errors are stored as Exception objects so rendering can show them gracefully.

progress   = st.progress(0, text="Fetching data…")
stock_data: dict = {}

for i, ticker in enumerate(tickers):
    try:
        df = get_stock_data(ticker)
        if df.empty:
            stock_data[ticker] = None
        else:
            stock_data[ticker] = add_ema(df)
    except Exception as e:
        stock_data[ticker] = e
    progress.progress((i + 1) / len(tickers), text=f"Fetched {ticker}")

progress.empty()


# COMPUTE SCORES FOR VALID TICKERS
# We compute scores here (before rendering) so we can sort tickers by score.

scores: dict[str, float] = {}    # ticker → total momentum score

for ticker, data in stock_data.items():
    if data is None or isinstance(data, Exception):
        scores[ticker] = float("-inf")   # push failed tickers to the bottom
        continue
    try:
        df            = data
        current_price = float(df.iloc[-1]["Close"])
        d_pivots      = pivot_points(df)
        w_ohlc        = get_weekly_ohlc(df)
        w_pivots      = weekly_pivot_points(w_ohlc)
        hl            = get_high_low_resampled(df)
        sc            = momentum_score(df, current_price, d_pivots, w_pivots, hl)
        scores[ticker] = sc["total"]
    except Exception:
        scores[ticker] = float("-inf")

# Sort tickers strongest momentum first
ranked_tickers = sorted(tickers, key=lambda t: scores[t], reverse=True)


# RENDER — one card per ticker, ranked by score

for ticker in ranked_tickers:
    data = stock_data[ticker]
    st.markdown(f"### {ticker}")

    if data is None:
        st.warning(f"{ticker}: no data returned")
        st.divider()
        continue
    if isinstance(data, Exception):
        st.error(f"{ticker}: {data}")
        st.divider()
        continue

    df            = data
    current_price = float(df.iloc[-1]["Close"])

    try:
        d_pivots  = pivot_points(df)
        w_ohlc    = get_weekly_ohlc(df)
        w_pivots  = weekly_pivot_points(w_ohlc)
        hl        = get_high_low_resampled(df)
        sc        = momentum_score(df, current_price, d_pivots, w_pivots, hl)
        label, colour = momentum_label(sc["total"])

        ohlc_ema_df = build_ohlc_ema_table(df, current_price)
        daily_piv_df  = build_pivot_table(d_pivots, current_price)
        weekly_piv_df = build_pivot_table(w_pivots, current_price) if w_pivots else None
        hl_df       = build_hl_table(df, current_price)
        momentum_text = momentum_summary(sc, label)
        summary     = generate_summary(df, current_price, momentum_text)
        breakdown   = build_score_breakdown(sc)

    except Exception as e:
        st.error(f"{ticker} error: {e}")
        st.divider()
        continue

    # Coloured pill showing label + numeric score at a glance.
    # Colour is driven by momentum_label() and maps to the 5-tier system.
    badge_html = (
        f'<span style="background:{colour};color:#fff;padding:3px 10px;'
        f'border-radius:12px;font-weight:bold;font-size:0.95em">'
        f'{label}&nbsp;&nbsp;{sc["total"]:+.2f}</span>'
    )
    st.markdown(badge_html, unsafe_allow_html=True)

    st.markdown(summary)

    with st.expander("Show / Hide Tables", expanded=False):

        # Row 1: OHLC+EMAs | Daily Pivots | Weekly Pivots
        col1, col2, col3 = st.columns(3)

        with col1:
            st.caption("OHLC & EMAs  (last session)")
            st.dataframe(
                ohlc_ema_df.style.format("{:.2f}", na_rep="—"),
                width='stretch',
                height=len(ohlc_ema_df) * ROW_PX + HDR_PX,
            )
        
        with col2:
            st.caption("Weekly Pivot Levels  (prev completed week H/L/C)")
            if weekly_piv_df is not None:
                st.dataframe(
                    weekly_piv_df.style.format("{:.2f}", na_rep="—"),
                    width='stretch',
                    height=len(weekly_piv_df) * ROW_PX + HDR_PX,
                )
            else:
                st.info("Insufficient data for weekly pivots.")

        with col3:
            st.caption("Period High / Low  (with % range position)")
            st.dataframe(
                hl_df.style.format({"Value": "{:.2f}"}, na_rep="—"),
                width='stretch',
                height=len(hl_df) * ROW_PX + HDR_PX,
            )

        

    st.divider()

# FOOTER
st.caption(
    "OHLC/EMAs = last session · Daily pivots = prev session H/L/C · "
    "Weekly pivots = prev completed Mon–Fri week · "
    "Period H/L = rolling window · Tickers ranked by momentum score (strongest first)"
)
