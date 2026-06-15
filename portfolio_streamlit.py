from typing import Any

import pandas as pd
import streamlit as st

from getHldgBrk import (
    _active_breakdown_df,
    _holdings_breakdown_state_df,
    _ltp_match_symbol,
    _normalized_symbol_value,
    display_selected_holding_batches,
    enrich_holdings_breakdown_with_ltp,
)


def _format_display_value(value: Any, decimals: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.{decimals}f}"
    return str(value)


def _format_percent_value(value: Any, decimals: int = 2) -> str:
    formatted_value = _format_display_value(value, decimals)
    if formatted_value == "-":
        return formatted_value
    return f"{formatted_value}%"


def _pnl_color(value: Any) -> str:
    converted = pd.to_numeric(value, errors="coerce")
    if pd.isna(converted):
        return "#475569"
    if converted > 0:
        return "#047857"
    if converted < 0:
        return "#b91c1c"
    return "#475569"


def _style_pnl_columns(df: pd.DataFrame):
    percent_columns = {"batch_pnl_pct", "pnl_pct", "Batch P&L %", "P&L %", "DayChg %", "Daychg%"}
    pnl_columns = [
        column
        for column in [
            "batch_pnl",
            "batch_pnl_pct",
            "pnl",
            "pnl_pct",
            "Batch P&L",
            "Batch P&L %",
            "P&L",
            "P&L %",
            "DayChg %",
            "Daychg%",
        ]
        if column in df.columns
    ]
    formatters = {
        column: (_format_percent_value if column in percent_columns else _format_display_value)
        for column in df.columns
    }
    styler = df.style.format(formatters, na_rep="-")
    for column in pnl_columns:
        styler = styler.map(lambda value: f"color: {_pnl_color(value)}; font-weight: 600", subset=[column])
    return styler


def _dataframe_height(row_count: int, *, min_rows: int = 1, max_rows: int | None = None) -> int:
    visible_rows = max(row_count, min_rows)
    if max_rows is not None:
        visible_rows = min(visible_rows, max_rows)
    return 38 + (visible_rows * 35)


def _rng_symbol_color(range_pct: float | None) -> str:
    if range_pct is None:
        return ""
    if range_pct < 25:
        return "color: #dc2626; font-weight: 700"
    if range_pct < 50:
        return "color: #f97316; font-weight: 700"
    if range_pct < 75:
        return "color: #84cc16; font-weight: 700"
    return "color: #16a34a; font-weight: 700"


def _rng_color_by_symbol(dashboard_df: pd.DataFrame) -> dict[str, str]:
    colors: dict[str, str] = {}
    if dashboard_df.empty:
        return colors

    for symbol in dashboard_df.columns:
        range_pct = None
        for value in dashboard_df[symbol]:
            if not isinstance(value, str) or not value.startswith("Rng:"):
                continue
            try:
                range_pct = float(value.removeprefix("Rng:").split("%", 1)[0])
            except ValueError:
                range_pct = None
            break
        color = _rng_symbol_color(range_pct)
        if color:
            colors[str(symbol).strip().upper()] = color
    return colors


def _style_kite_holdings(display_df: pd.DataFrame, rng_colors: dict[str, str]):
    styler = _style_pnl_columns(display_df)
    if "Symbol" not in display_df.columns or not rng_colors:
        return styler
    return styler.map(lambda value: rng_colors.get(str(value).strip().upper(), ""), subset=["Symbol"])


def _sector_maps_from_breakdown(holdings_breakdown_df: pd.DataFrame) -> tuple[dict[str, str], dict[str, str]]:
    if holdings_breakdown_df.empty or not {"symbol", "sector"}.issubset(holdings_breakdown_df.columns):
        return {}, {}

    breakdown_df = holdings_breakdown_df.copy()
    if "row_type" in breakdown_df.columns:
        summary_rows = breakdown_df["row_type"].astype(str).str.upper().str.strip().eq("SUMMARY")
        if summary_rows.any():
            breakdown_df = breakdown_df[summary_rows]

    sector_by_symbol = (
        breakdown_df.assign(symbol_key=breakdown_df["symbol"].astype(str).str.upper().str.strip())
        .dropna(subset=["symbol_key", "sector"])
        .drop_duplicates("symbol_key")
        .set_index("symbol_key")["sector"]
        .astype(str)
        .str.strip()
        .to_dict()
    )
    sector_by_isin: dict[str, str] = {}
    if "isin" in breakdown_df.columns:
        sector_by_isin = (
            breakdown_df.assign(isin_key=breakdown_df["isin"].astype(str).str.upper().str.strip())
            .dropna(subset=["isin_key", "sector"])
            .drop_duplicates("isin_key")
            .set_index("isin_key")["sector"]
            .astype(str)
            .str.strip()
            .to_dict()
        )
        sector_by_isin.pop("", None)
    return sector_by_symbol, sector_by_isin


def _add_sector_to_holdings_display(display_df: pd.DataFrame, holdings_breakdown_df: pd.DataFrame) -> pd.DataFrame:
    if display_df.empty or "Symbol" not in display_df.columns:
        return display_df

    sector_by_symbol, sector_by_isin = _sector_maps_from_breakdown(holdings_breakdown_df)
    if not sector_by_symbol and not sector_by_isin:
        return display_df

    display_df = display_df.copy()
    symbol_key = display_df["Symbol"].astype(str).str.upper().str.strip()
    sector = symbol_key.map(sector_by_symbol)
    if "ISIN" in display_df.columns and sector_by_isin:
        isin_key = display_df["ISIN"].astype(str).str.upper().str.strip()
        sector = sector.fillna(isin_key.map(sector_by_isin))
    display_df["Sector"] = sector.replace("", pd.NA).fillna("Unmapped")
    ordered_columns = ["Sector"] + [column for column in display_df.columns if column != "Sector"]
    return display_df[ordered_columns].sort_values(["Sector", "Symbol"], kind="stable")


def _sector_holdings_summary_df(display_df: pd.DataFrame) -> pd.DataFrame:
    if display_df.empty or "Sector" not in display_df.columns:
        return pd.DataFrame()

    summary_df = (
        display_df.assign(
            Invested=pd.to_numeric(display_df.get("Invested"), errors="coerce"),
            Current=pd.to_numeric(display_df.get("Current"), errors="coerce"),
            **{"P&L": pd.to_numeric(display_df.get("P&L"), errors="coerce")},
        )
        .groupby("Sector", dropna=False)
        .agg(
            Holdings=("Symbol", "count"),
            Invested=("Invested", "sum"),
            Current=("Current", "sum"),
            **{"P&L": ("P&L", "sum")},
        )
        .reset_index()
    )
    summary_df["P&L %"] = summary_df["P&L"].where(summary_df["Invested"].ne(0)) / summary_df["Invested"] * 100
    total_invested = pd.to_numeric(summary_df["Invested"], errors="coerce").sum()
    summary_df["Weight %"] = summary_df["Invested"] / total_invested * 100 if total_invested else pd.NA
    return summary_df[["Sector", "Holdings", "Invested", "Weight %", "Current", "P&L", "P&L %"]].sort_values(
        "Current", ascending=False, kind="stable"
    )


def _kite_holdings_column_config() -> dict[str, Any]:
    return {
        "Sector": st.column_config.TextColumn("Sector", width="medium"),
        "Symbol": st.column_config.TextColumn("Symbol", width="small"),
        "ISIN": None,
        "Quantity": st.column_config.NumberColumn("Quantity", width="small", format="%d"),
        "Avg Price": st.column_config.NumberColumn("Avg Price", width="small", format="%.2f"),
        "Invested": st.column_config.NumberColumn("Invested", width="small", format="%.2f"),
        "Current": st.column_config.NumberColumn("Current", width="small", format="%.2f"),
        "LTP": st.column_config.NumberColumn("LTP", width="small", format="%.2f"),
        "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
        "P&L %": st.column_config.NumberColumn("P&L %", width="small", format="%.2f%%"),
        "Weight %": st.column_config.NumberColumn("Weight %", width="small", format="%.2f%%"),
        "DayChg %": st.column_config.NumberColumn("DayChg %", width="small", format="%.2f%%"),
    }


def _sector_summary_column_config() -> dict[str, Any]:
    return {
        "Sector": st.column_config.TextColumn("Sector", width="medium"),
        "Holdings": st.column_config.NumberColumn("Holdings", width="small", format="%d"),
        "Invested": st.column_config.NumberColumn("Invested", width="small", format="%.2f"),
        "Current": st.column_config.NumberColumn("Current", width="small", format="%.2f"),
        "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
        "P&L %": st.column_config.NumberColumn("P&L %", width="small", format="%.2f%%"),
        "Weight %": st.column_config.NumberColumn("Weight %", width="small", format="%.2f%%"),
    }


def _mtf_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    if "mtf" not in df.columns:
        return []
    return [value if isinstance(value, dict) else {} for value in df["mtf"].tolist()]


def _mtf_quantity_series(df: pd.DataFrame) -> pd.Series:
    if df.empty or "mtf" not in df.columns:
        return pd.Series(0, index=df.index, dtype=float)
    mtf_df = pd.json_normalize(_mtf_rows(df))
    return pd.to_numeric(
        mtf_df.get("quantity", pd.Series(0, index=range(len(df)), dtype=float)),
        errors="coerce",
    ).fillna(0).set_axis(df.index)


def _non_mtf_holdings_df(df: pd.DataFrame) -> pd.DataFrame:
    return df[_mtf_quantity_series(df).le(0)].copy()


def _cache_ltp_by_symbol(df: pd.DataFrame) -> None:
    if {"tradingsymbol", "last_price"}.issubset(df.columns):
        ltp_by_symbol: dict[str, Any] = {}
        for symbol, ltp in zip(df["tradingsymbol"], df["last_price"]):
            if pd.isna(ltp):
                continue
            symbol_key = _normalized_symbol_value(symbol)
            fallback_key = _ltp_match_symbol(symbol)
            if symbol_key:
                ltp_by_symbol[symbol_key] = ltp
            if fallback_key and fallback_key not in ltp_by_symbol:
                ltp_by_symbol[fallback_key] = ltp
        st.session_state["ltp_by_symbol"] = ltp_by_symbol
    else:
        st.session_state["ltp_by_symbol"] = {}


def _mtf_holdings_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "mtf" not in df.columns:
        return pd.DataFrame()

    mtf_df = pd.json_normalize(_mtf_rows(df)).add_prefix("mtf_")
    display_df = pd.DataFrame(
        {
            "Symbol": df.get("tradingsymbol", pd.Series(index=df.index, dtype=object)).reset_index(drop=True),
            "MTF Qty": mtf_df.get("mtf_quantity", pd.Series(index=mtf_df.index, dtype=float)),
            "MTF Avg Price": mtf_df.get("mtf_average_price", pd.Series(index=mtf_df.index, dtype=float)),
            "MTF Value": mtf_df.get("mtf_value", pd.Series(index=mtf_df.index, dtype=float)),
            "LTP": df.get("last_price", pd.Series(index=df.index, dtype=float)).reset_index(drop=True),
            "P&L": df.get("pnl", pd.Series(index=df.index, dtype=float)).reset_index(drop=True),
            "Daychg%": df.get("day_change_percentage", pd.Series(index=df.index, dtype=float)).reset_index(drop=True),
        }
    )
    display_df["MTF Qty"] = pd.to_numeric(display_df["MTF Qty"], errors="coerce").fillna(0)
    return display_df[display_df["MTF Qty"].gt(0)].copy()


def _display_mtf_holdings_df(df: pd.DataFrame) -> None:
    mtf_display_df = _mtf_holdings_display_df(df)
    if mtf_display_df.empty:
        return

    st.subheader("MTF Holdings")
    st.dataframe(
        _style_pnl_columns(mtf_display_df),
        width="stretch",
        height=_dataframe_height(len(mtf_display_df), max_rows=8),
        hide_index=True,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol", width="small"),
            "MTF Qty": st.column_config.NumberColumn("MTF Qty", width="small", format="%d"),
            "MTF Avg Price": st.column_config.NumberColumn("MTF Avg Price", width="small", format="%.2f"),
            "MTF Value": st.column_config.NumberColumn("MTF Value", width="small", format="%.2f"),
            "LTP": st.column_config.NumberColumn("LTP", width="small", format="%.2f"),
            "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
            "Daychg%": st.column_config.NumberColumn("Daychg%", width="small", format="%.2f%%"),
        },
    )


def _display_sector_weight_pie_chart(sector_summary_df: pd.DataFrame) -> None:
    if sector_summary_df.empty or not {"Sector", "Invested"}.issubset(sector_summary_df.columns):
        return

    chart_df = sector_summary_df.copy()
    chart_df["Invested"] = pd.to_numeric(chart_df["Invested"], errors="coerce")
    chart_df = chart_df.dropna(subset=["Invested"])
    chart_df = chart_df[chart_df["Invested"].gt(0)]
    if chart_df.empty:
        return

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        st.info("Install matplotlib to display the sector weightage chart.")
        return

    fig, ax = plt.subplots(figsize=(3.6, 3.0), dpi=120)
    ax.pie(
        chart_df["Invested"],
        labels=chart_df["Sector"],
        autopct=lambda pct: f"{pct:.1f}%" if pct >= 3 else "",
        startangle=90,
        textprops={"fontsize": 8},
    )
    ax.axis("equal")
    st.pyplot(fig, clear_figure=True)


def _component_number(value: Any) -> float:
    converted = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(converted) else float(converted)


def _component_int(value: Any) -> int:
    converted = pd.to_numeric(value, errors="coerce")
    return 0 if pd.isna(converted) else int(converted)


def _component_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _day_pnl_from_ltp_change(ltp: Any, day_change_pct: Any, quantity: Any) -> float:
    ltp_value = pd.to_numeric(ltp, errors="coerce")
    day_change_value = pd.to_numeric(day_change_pct, errors="coerce")
    quantity_value = pd.to_numeric(quantity, errors="coerce")
    if pd.isna(ltp_value) or pd.isna(day_change_value) or pd.isna(quantity_value):
        return 0.0
    denominator = 1 + (float(day_change_value) / 100)
    if denominator == 0:
        return 0.0
    previous_close = float(ltp_value) / denominator
    return (float(ltp_value) - previous_close) * float(quantity_value)


def _portfolio_component_batches(
    symbol: Any,
    isin: Any,
    holdings_breakdown_df: pd.DataFrame,
) -> list[dict[str, Any]]:
    if holdings_breakdown_df.empty or "row_type" not in holdings_breakdown_df.columns:
        return []

    enriched_df, _ = enrich_holdings_breakdown_with_ltp(holdings_breakdown_df, st.session_state.get("ltp_by_symbol", {}))
    active_df = _active_breakdown_df(enriched_df)
    if active_df.empty or "row_type" not in active_df.columns:
        return []

    row_type = active_df["row_type"].astype(str).str.upper().str.strip()
    symbol_key = _normalized_symbol_value(symbol)
    isin_key = str(isin or "").upper().strip()
    selected_rows = row_type.eq("BATCH") & active_df["symbol"].astype(str).str.upper().str.strip().eq(symbol_key)
    if isin_key and "isin" in active_df.columns:
        selected_rows = selected_rows | (row_type.eq("BATCH") & active_df["isin"].astype(str).str.upper().str.strip().eq(isin_key))

    batch_df = active_df[selected_rows].copy()
    if batch_df.empty:
        return []
    if "id" in batch_df.columns:
        batch_df = batch_df.sort_values("id", kind="stable")

    return [
        {
            "price": _component_number(batch.get("batch_price")),
            "qty": _component_int(batch.get("batch_qty")),
            "age": _component_text(batch.get("present_age") or batch.get("age_days")),
            "profitPct": _component_number(batch.get("batch_pnl_pct")),
        }
        for _, batch in batch_df.iterrows()
    ]


def _mtf_snapshot_rows(holdings_df: pd.DataFrame) -> list[dict[str, Any]]:
    mtf_display_df = _mtf_holdings_display_df(holdings_df)
    if mtf_display_df.empty:
        return []
    return [
        {
            "symbol": _component_text(row.get("Symbol")),
            "mtfQty": _component_int(row.get("MTF Qty")),
            "mtfAvgPrice": _component_number(row.get("MTF Avg Price")),
            "mtfValue": _component_number(row.get("MTF Value")),
            "ltp": _component_number(row.get("LTP")),
            "pnl": _component_number(row.get("P&L")),
            "dayChangePct": _component_number(row.get("Daychg%")),
        }
        for _, row in mtf_display_df.iterrows()
    ]


def _mtf_totals(holdings_df: pd.DataFrame) -> dict[str, float]:
    mtf_display_df = _mtf_holdings_display_df(holdings_df)
    if mtf_display_df.empty:
        return {"invested": 0.0, "current": 0.0, "pnl": 0.0, "dayPnl": 0.0}

    invested = pd.to_numeric(mtf_display_df.get("MTF Value"), errors="coerce").fillna(0).sum()
    pnl = pd.to_numeric(mtf_display_df.get("P&L"), errors="coerce").fillna(0).sum()
    current = invested + pnl
    day_pnl = sum(
        _day_pnl_from_ltp_change(row.get("LTP"), row.get("Daychg%"), row.get("MTF Qty"))
        for _, row in mtf_display_df.iterrows()
    )
    return {
        "invested": float(invested),
        "current": float(current),
        "pnl": float(pnl),
        "dayPnl": float(day_pnl),
    }


def build_portfolio_terminal_snapshot(
    holdings_df: pd.DataFrame,
    holdings_breakdown_df: pd.DataFrame,
    *,
    as_of: str,
) -> dict[str, Any]:
    empty_snapshot = {
        "asOf": as_of,
        "totals": {"invested": 0, "current": 0, "pnl": 0, "pnlPct": 0, "dayPnl": 0, "dayPnlPct": 0},
        "sectors": [],
        "mtfHoldings": _mtf_snapshot_rows(holdings_df),
    }
    if holdings_df.empty:
        return empty_snapshot

    df = _non_mtf_holdings_df(holdings_df.copy())
    if df.empty:
        return empty_snapshot

    df["invested"] = pd.to_numeric(df.get("average_price"), errors="coerce") * pd.to_numeric(df.get("quantity"), errors="coerce")
    df["current_value"] = pd.to_numeric(df.get("last_price"), errors="coerce") * pd.to_numeric(df.get("quantity"), errors="coerce")
    invested = pd.to_numeric(df["invested"], errors="coerce")
    df["pnl_pct"] = pd.to_numeric(df.get("pnl"), errors="coerce").where(invested.ne(0)) / invested * 100

    display_df = pd.DataFrame(
        {
            "Sector": "Unmapped",
            "Symbol": df.get("tradingsymbol", pd.Series(index=df.index, dtype=object)),
            "ISIN": df.get("isin", pd.Series(index=df.index, dtype=object)),
            "Quantity": df.get("quantity", pd.Series(index=df.index, dtype=float)),
            "Avg Price": df.get("average_price", pd.Series(index=df.index, dtype=float)),
            "Invested": df["invested"],
            "Current": df["current_value"],
            "LTP": df.get("last_price", pd.Series(index=df.index, dtype=float)),
            "P&L": df.get("pnl", pd.Series(index=df.index, dtype=float)),
            "P&L %": df["pnl_pct"],
            "DayChg %": df.get("day_change_percentage", pd.Series(index=df.index, dtype=float)),
        }
    )
    display_df = _add_sector_to_holdings_display(display_df, holdings_breakdown_df)

    non_mtf_day_pnl = sum(
        _day_pnl_from_ltp_change(holding.get("LTP"), holding.get("DayChg %"), holding.get("Quantity"))
        for _, holding in display_df.iterrows()
    )
    mtf_totals = _mtf_totals(holdings_df)
    total_invested = _component_number(display_df["Invested"].sum()) + mtf_totals["invested"]
    total_current = _component_number(display_df["Current"].sum()) + mtf_totals["current"]
    total_pnl = _component_number(display_df["P&L"].sum()) + mtf_totals["pnl"]
    total_day_pnl = non_mtf_day_pnl + mtf_totals["dayPnl"]
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0
    total_day_pnl_pct = (total_day_pnl / total_invested * 100) if total_invested else 0

    sectors: list[dict[str, Any]] = []
    for sector, sector_df in display_df.groupby("Sector", sort=False):
        sector_invested = _component_number(sector_df["Invested"].sum())
        sector_current = _component_number(sector_df["Current"].sum())
        sector_pnl = _component_number(sector_df["P&L"].sum())
        sector_pnl_pct = (sector_pnl / sector_invested * 100) if sector_invested else 0
        sector_weight_pct = (sector_invested / total_invested * 100) if total_invested else 0

        holdings: list[dict[str, Any]] = []
        for _, holding in sector_df.iterrows():
            holding_invested = _component_number(holding.get("Invested"))
            holdings.append(
                {
                    "symbol": _component_text(holding.get("Symbol")),
                    "quantity": _component_int(holding.get("Quantity")),
                    "averagePrice": _component_number(holding.get("Avg Price")),
                    "invested": holding_invested,
                    "weightPct": (holding_invested / sector_invested * 100) if sector_invested else 0,
                    "current": _component_number(holding.get("Current")),
                    "ltp": _component_number(holding.get("LTP")),
                    "pnl": _component_number(holding.get("P&L")),
                    "pnlPct": _component_number(holding.get("P&L %")),
                    "dayChangePct": _component_number(holding.get("DayChg %")),
                    "batches": _portfolio_component_batches(holding.get("Symbol"), holding.get("ISIN"), holdings_breakdown_df),
                }
            )

        sectors.append(
            {
                "sector": _component_text(sector),
                "holdingsCount": len(holdings),
                "invested": sector_invested,
                "weightPct": sector_weight_pct,
                "current": sector_current,
                "pnl": sector_pnl,
                "pnlPct": sector_pnl_pct,
                "holdings": holdings,
            }
        )

    return {
        "asOf": as_of,
        "totals": {
            "invested": total_invested,
            "current": total_current,
            "pnl": total_pnl,
            "pnlPct": total_pnl_pct,
            "dayPnl": total_day_pnl,
            "dayPnlPct": total_day_pnl_pct,
        },
        "sectors": sectors,
        "mtfHoldings": _mtf_snapshot_rows(holdings_df),
    }


def display_kite_holdings(
    df: pd.DataFrame,
    kite=None,
    *,
    selection_key: str | None = None,
    selected_batches_df: pd.DataFrame | None = None,
    selected_batches_error: str | None = None,
) -> str | None:
    if df.empty:
        st.session_state["ltp_by_symbol"] = {}
        st.warning("No holdings found.")
        return None

    all_holdings_df = df.copy()
    _cache_ltp_by_symbol(all_holdings_df)
    df = _non_mtf_holdings_df(all_holdings_df)
    if df.empty:
        _display_mtf_holdings_df(all_holdings_df)
        st.info("No non-MTF holdings found.")
        return None

    df["invested"] = pd.to_numeric(df["average_price"], errors="coerce") * pd.to_numeric(df["quantity"], errors="coerce")
    df["CurrentValue"] = pd.to_numeric(df["last_price"], errors="coerce") * pd.to_numeric(df["quantity"], errors="coerce")
    if "pnl_pct" not in df.columns and {"pnl", "average_price", "quantity"}.issubset(df.columns):
        invested = df["invested"]
        df["pnl_pct"] = pd.to_numeric(df["pnl"], errors="coerce").where(invested.ne(0)) / invested * 100

    display_cols = [
        "tradingsymbol",
        "isin",
        "quantity",
        "average_price",
        "invested",
        "CurrentValue",
        "last_price",
        "pnl",
        "pnl_pct",
        "day_change_percentage",
    ]
    display_df = df[[column for column in display_cols if column in df.columns]].rename(
        columns={
            "tradingsymbol": "Symbol",
            "isin": "ISIN",
            "quantity": "Quantity",
            "average_price": "Avg Price",
            "invested": "Invested",
            "CurrentValue": "Current",
            "last_price": "LTP",
            "pnl": "P&L",
            "pnl_pct": "P&L %",
            "day_change_percentage": "DayChg %",
        }
    )
    breakdown_df_for_sector = selected_batches_df if selected_batches_df is not None else _holdings_breakdown_state_df()
    display_df = _add_sector_to_holdings_display(display_df, breakdown_df_for_sector)

    col1, col2, col3 = st.columns(3)
    with col1:
        total_invested = pd.to_numeric(df["invested"], errors="coerce").sum() if "invested" in df.columns else 0
        st.metric("Total Invested", f"{total_invested:,.2f}")
    with col2:
        total_pnl = pd.to_numeric(df["pnl"], errors="coerce").sum() if "pnl" in df.columns else 0
        total_pnl_percent = (total_pnl / total_invested) * 100 if total_invested != 0 else 0
        st.metric("Total P&L", f"{total_pnl:,.2f}", delta=f"{total_pnl_percent:.2f}", format="%.2f%%")
    with col3:
        kite_holdings_download_filename = st.session_state.get("kite_holdings_download_filename", "")
        holdings_as_of = kite_holdings_download_filename.split("_")[1] if "_" in kite_holdings_download_filename else "Unknown"
        st.metric("As of", holdings_as_of)

    rng_colors = _rng_color_by_symbol(st.session_state.get("kite_holdings_dashboard_df", pd.DataFrame()))

    def render_holdings_table(table_df: pd.DataFrame, *, table_key: str | None = None):
        dataframe_kwargs = {}
        if table_key:
            dataframe_kwargs = {"key": table_key, "on_select": "rerun", "selection_mode": "single-row"}
        return st.dataframe(
            _style_kite_holdings(table_df, rng_colors),
            width="stretch",
            height=_dataframe_height(len(table_df), max_rows=15),
            hide_index=True,
            column_config=_kite_holdings_column_config(),
            **dataframe_kwargs,
        )

    def render_sector_summary() -> None:
        sector_summary_df = _sector_holdings_summary_df(display_df)
        if sector_summary_df.empty:
            return
        chart_column, summary_column = st.columns([1, 2])
        with chart_column:
            _display_sector_weight_pie_chart(sector_summary_df)
        with summary_column:
            st.dataframe(
                _style_pnl_columns(sector_summary_df),
                width="stretch",
                height=_dataframe_height(len(sector_summary_df), max_rows=8),
                hide_index=True,
                column_config=_sector_summary_column_config(),
            )

    batches_df = selected_batches_df if selected_batches_df is not None else pd.DataFrame()
    selected_symbol_state_key = f"{selection_key or 'kite_holdings'}_selected_holding_symbol"
    selected_isin_state_key = f"{selection_key or 'kite_holdings'}_selected_holding_isin"
    selected_sector_state_key = f"{selection_key or 'kite_holdings'}_selected_holding_sector"

    def render_selected_batches_panel(selected_symbol: str | None, selected_isin: str | None = None) -> None:
        if selected_batches_df is None and selected_batches_error is None:
            return
        if selected_batches_error:
            st.warning(f"Could not load holdings breakdown from Supabase: {selected_batches_error}")
        display_selected_holding_batches(selected_symbol, batches_df, selected_isin)

    def render_sector_grouped_holdings() -> str | None:
        active_symbol = st.session_state.get(selected_symbol_state_key)
        active_isin = st.session_state.get(selected_isin_state_key)
        active_sector = st.session_state.get(selected_sector_state_key)
        total_display_invested = pd.to_numeric(display_df.get("Invested"), errors="coerce").sum()
        for sector, sector_df in display_df.groupby("Sector", sort=False):
            sector_key = _normalized_symbol_value(sector).replace(" ", "_") or "UNMAPPED"
            sector_invested = pd.to_numeric(sector_df.get("Invested"), errors="coerce").sum()
            sector_weight = sector_invested / total_display_invested * 100 if total_display_invested else 0
            with st.expander(
                f"{sector} ({len(sector_df)} | Invested {sector_invested:,.2f} | Weight {sector_weight:.2f}%)",
                expanded=True,
            ):
                sector_table_column = st.container()
                sector_batches_column = None
                if selected_batches_df is not None or selected_batches_error is not None:
                    sector_table_column, sector_batches_column = st.columns([3, 1])
                holdings_table_df = sector_df.drop(columns=["Sector"], errors="ignore")
                holdings_table_df["Weight %"] = (
                    pd.to_numeric(holdings_table_df.get("Invested"), errors="coerce") / sector_invested * 100
                    if sector_invested
                    else pd.NA
                )
                if "Invested" in holdings_table_df.columns:
                    invested_column_index = holdings_table_df.columns.get_loc("Invested")
                    weight_column = holdings_table_df.pop("Weight %")
                    holdings_table_df.insert(invested_column_index + 1, "Weight %", weight_column)
                table_key = f"{selection_key}_{sector_key}" if selection_key else None
                with sector_table_column:
                    selection = render_holdings_table(holdings_table_df, table_key=table_key)
                if selection_key:
                    selected_rows = selection.selection.rows if selection.selection else []
                    if selected_rows:
                        selected_row_index = selected_rows[0]
                        if selected_row_index < len(sector_df):
                            selected_row = sector_df.iloc[selected_row_index]
                            active_symbol = str(selected_row["Symbol"]).upper().strip()
                            active_isin = str(selected_row.get("ISIN") or "").upper().strip()
                            active_sector = sector_key
                            st.session_state[selected_symbol_state_key] = active_symbol
                            st.session_state[selected_isin_state_key] = active_isin
                            st.session_state[selected_sector_state_key] = active_sector
                sector_symbols = set(sector_df["Symbol"].astype(str).str.upper().str.strip())
                sector_isins = set(sector_df["ISIN"].astype(str).str.upper().str.strip()) if "ISIN" in sector_df.columns else set()
                if (
                    sector_batches_column is not None
                    and active_symbol
                    and active_sector == sector_key
                    and (active_symbol in sector_symbols or (active_isin and active_isin in sector_isins))
                ):
                    with sector_batches_column:
                        render_selected_batches_panel(str(active_symbol).upper().strip(), active_isin)
        return str(active_symbol).upper().strip() if active_symbol else None

    selected_symbol = None
    has_sector_grouping = "Sector" in display_df.columns
    if has_sector_grouping:
        render_sector_summary()
    _display_mtf_holdings_df(all_holdings_df)

    selected_symbol = render_sector_grouped_holdings() if has_sector_grouping else None
    if not has_sector_grouping:
        selection = render_holdings_table(display_df, table_key=selection_key)

    if not selection_key:
        return None
    if not has_sector_grouping:
        selected_rows = selection.selection.rows if selection.selection else []
        if selected_rows:
            selected_row_index = selected_rows[0]
            if selected_row_index < len(display_df) and "Symbol" in display_df.columns:
                selected_row = display_df.iloc[selected_row_index]
                selected_symbol = str(selected_row["Symbol"]).upper().strip()
                st.session_state[selected_isin_state_key] = str(selected_row.get("ISIN") or "").upper().strip()

    if not selected_symbol:
        if not has_sector_grouping:
            render_selected_batches_panel(None)
        return None
    if not has_sector_grouping:
        render_selected_batches_panel(selected_symbol, st.session_state.get(selected_isin_state_key))
    return selected_symbol
