from typing import Any

import pandas as pd
import streamlit as st


def classify_day_move(day_change_pct: Any) -> str:
    value = pd.to_numeric(day_change_pct, errors="coerce")
    if pd.isna(value):
        return "Neutral"
    if value >= 2:
        return "Strong Gainer"
    if value >= 1:
        return "Gainer"
    if value <= -2:
        return "Strong Loser"
    if value <= -1:
        return "Loser"
    return "Neutral"


def build_portfolio_day_movers_df(holdings_df: pd.DataFrame) -> pd.DataFrame:
    required_columns = {"tradingsymbol", "last_price", "day_change_percentage", "quantity"}
    if holdings_df.empty or not required_columns.issubset(holdings_df.columns):
        return pd.DataFrame()

    df = holdings_df.copy()
    df["Ticker"] = df["tradingsymbol"].astype(str).str.strip().str.upper()
    df["ltp"] = pd.to_numeric(df["last_price"], errors="coerce")
    df["day_change_pct"] = pd.to_numeric(df["day_change_percentage"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    denominator = 1 + (df["day_change_pct"] / 100)
    df["previous_close"] = df["ltp"].where(denominator.ne(0)) / denominator
    df["day_change_abs"] = df["ltp"] - df["previous_close"]
    df["today_pnl"] = df["day_change_abs"] * df["quantity"]
    df["current_value"] = df["ltp"] * df["quantity"]

    total_current_value = pd.to_numeric(df["current_value"], errors="coerce").sum()
    if total_current_value:
        df["portfolio_weight"] = df["current_value"] / total_current_value
    else:
        df["portfolio_weight"] = pd.NA
    df["impact_score"] = df["day_change_pct"].abs() * df["portfolio_weight"]
    df["move_label"] = df["day_change_pct"].apply(classify_day_move)

    return df.dropna(subset=["Ticker", "ltp", "day_change_pct"])


def build_portfolio_day_movers_summary(holdings_df: pd.DataFrame, limit: int = 3) -> pd.DataFrame:
    movers_df = build_portfolio_day_movers_df(holdings_df)
    if movers_df.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    top_gainer = movers_df.sort_values("day_change_pct", ascending=False).head(limit)
    top_loser = movers_df.sort_values("day_change_pct", ascending=True).head(limit)
    top_contributor = movers_df.sort_values("today_pnl", ascending=False).head(limit)
    top_drag = movers_df.sort_values("today_pnl", ascending=True).head(limit)

    for label, row_df, value_column, value_label in [
        ("Top Gainer", top_gainer, "day_change_pct", "DayChg %"),
        ("Top Loser", top_loser, "day_change_pct", "DayChg %"),
        ("Top Contributor", top_contributor, "today_pnl", "Today P&L"),
        ("Top Drag", top_drag, "today_pnl", "Today P&L"),
    ]:
        if row_df.empty:
            continue
        for rank, (_, row) in enumerate(row_df.iterrows(), start=1):
            rows.append(
                {
                    "Metric": label,
                    "Rank": rank,
                    "Ticker": row.get("Ticker"),
                    "DayChg %": row.get("day_change_pct"),
                    value_label: row.get(value_column),
                    "Move": row.get("move_label"),
                }
            )
    return pd.DataFrame(rows)


def build_returns_day_movers_summary(returns_df: pd.DataFrame, limit: int = 3) -> pd.DataFrame:
    if returns_df.empty or not {"Ticker", "Today Return %"}.issubset(returns_df.columns):
        return pd.DataFrame()

    df = returns_df[["Ticker", "Today Return %"]].copy()
    df["Ticker"] = df["Ticker"].astype(str).str.strip().str.upper()
    df["Today Return %"] = pd.to_numeric(df["Today Return %"], errors="coerce")
    df = df.dropna(subset=["Ticker", "Today Return %"])
    if df.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for label, row_df in [
        ("Top Gainers", df.sort_values("Today Return %", ascending=False).head(limit)),
        ("Top Losers", df.sort_values("Today Return %", ascending=True).head(limit)),
    ]:
        for rank, (_, row) in enumerate(row_df.iterrows(), start=1):
            rows.append(
                {
                    "Metric": label,
                    "Rank": rank,
                    "Ticker": row["Ticker"],
                    "DayChg %": row["Today Return %"],
                    "Move": classify_day_move(row["Today Return %"]),
                }
            )
    return pd.DataFrame(rows)


def _format_signed_rupees(value: Any) -> str:
    converted = pd.to_numeric(value, errors="coerce")
    numeric_value = 0.0 if pd.isna(converted) else float(converted)
    sign = "+" if numeric_value >= 0 else "-"
    return f"{sign}Rs {abs(numeric_value):,.0f}"


def _format_pct(value: Any) -> str:
    numeric_value = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric_value):
        return "-"
    return f"{float(numeric_value):+.2f}%"


def _render_grouped_summary(summary_df: pd.DataFrame, metric_order: list[str]) -> None:
    columns = st.columns(len(metric_order))
    for column, metric in zip(columns, metric_order):
        metric_df = summary_df[summary_df["Metric"].eq(metric)].sort_values("Rank")
        with column:
            st.markdown(f"**{metric}**")
            if metric_df.empty:
                st.caption("-")
                continue
            for _, row in metric_df.iterrows():
                day_change_text = _format_pct(row.get("DayChg %"))
                if pd.notna(row.get("Today P&L")):
                    value_text = f"{_format_signed_rupees(row.get('Today P&L'))} ({day_change_text})"
                else:
                    value_text = day_change_text
                st.caption(f"{int(row.get('Rank') or 0)}. {row.get('Ticker')} {value_text}")


def display_portfolio_day_movers_summary(holdings_df: pd.DataFrame) -> None:
    summary_df = build_portfolio_day_movers_summary(holdings_df)
    if summary_df.empty:
        st.info("No day change data available for gainers/losers.")
        return

    _render_grouped_summary(
        summary_df,
        ["Top Gainer", "Top Loser", "Top Contributor", "Top Drag"],
    )


def display_returns_day_movers_summary(returns_df: pd.DataFrame) -> None:
    summary_df = build_returns_day_movers_summary(returns_df)
    if summary_df.empty:
        st.info("No today's return data available for gainers/losers.")
        return

    _render_grouped_summary(summary_df, ["Top Gainers", "Top Losers"])
