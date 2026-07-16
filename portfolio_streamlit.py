from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from kite_analytics import position_line_chart_points_from_dashboard_column

from getHldgBrk import (
    _active_breakdown_df,
    _ltp_match_symbol,
    _normalized_symbol_value,
    enrich_holdings_breakdown_with_ltp,
)


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
            "Initial Margin": mtf_df.get("mtf_initial_margin", pd.Series(index=mtf_df.index, dtype=float)),
            "LTP": df.get("last_price", pd.Series(index=df.index, dtype=float)).reset_index(drop=True),
            "P&L": df.get("pnl", pd.Series(index=df.index, dtype=float)).reset_index(drop=True),
            "Daychg%": df.get("day_change_percentage", pd.Series(index=df.index, dtype=float)).reset_index(drop=True),
        }
    )
    display_df["MTF Qty"] = pd.to_numeric(display_df["MTF Qty"], errors="coerce").fillna(0)
    mtf_value = pd.to_numeric(display_df["MTF Value"], errors="coerce").fillna(0)
    ltp = pd.to_numeric(display_df["LTP"], errors="coerce").fillna(0)
    display_df["P&L"] = (ltp * display_df["MTF Qty"]) - mtf_value
    return display_df[display_df["MTF Qty"].gt(0)].copy()


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


def _mtf_summary_by_symbol(holdings_breakdown_df: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    if holdings_breakdown_df is None or holdings_breakdown_df.empty:
        return {}
    required_columns = {"row_type", "symbol", "trade_date", "trade_type"}
    if not required_columns.issubset(holdings_breakdown_df.columns):
        return {}

    active_df = _active_breakdown_df(holdings_breakdown_df)
    if active_df.empty:
        return {}

    row_type = active_df["row_type"].astype(str).str.upper().str.strip()
    trade_type = active_df["trade_type"].astype(str).str.upper().str.strip()
    mtf_summary_df = active_df[row_type.eq("SUMMARY") & trade_type.eq("MTF")].copy()
    if mtf_summary_df.empty:
        return {}

    summary_by_symbol: dict[str, dict[str, Any]] = {}
    for _, row in mtf_summary_df.iterrows():
        symbol = _normalized_symbol_value(row.get("symbol"))
        if not symbol or symbol in summary_by_symbol:
            continue
        buy_date = pd.to_datetime(row.get("trade_date"), errors="coerce")
        if pd.isna(buy_date):
            continue
        buy_date_value = buy_date.date()
        summary_by_symbol[symbol] = {
            "buyDate": buy_date_value.isoformat(),
            "holdingDays": max((date.today() - buy_date_value).days, 0),
        }
    return summary_by_symbol


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
    if "trade_date" in batch_df.columns:
        batch_df = (
            batch_df.assign(_trade_date_sort=pd.to_datetime(batch_df["trade_date"], errors="coerce"))
            .sort_values("_trade_date_sort", ascending=True, na_position="last", kind="stable")
            .drop(columns="_trade_date_sort")
        )
    elif "id" in batch_df.columns:
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


def _mtf_snapshot_rows(
    holdings_df: pd.DataFrame,
    holdings_breakdown_df: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    mtf_display_df = _mtf_holdings_display_df(holdings_df)
    if mtf_display_df.empty:
        return []
    summary_by_symbol = _mtf_summary_by_symbol(holdings_breakdown_df)
    return [
        {
            "symbol": _component_text(row.get("Symbol")),
            "mtfQty": _component_int(row.get("MTF Qty")),
            "mtfAvgPrice": _component_number(row.get("MTF Avg Price")),
            "mtfValue": _component_number(row.get("MTF Value")),
            "initialMargin": _component_number(row.get("Initial Margin")),
            "ltp": _component_number(row.get("LTP")),
            "pnl": _component_number(row.get("P&L")),
            "dayChangePct": _component_number(row.get("Daychg%")),
            **summary_by_symbol.get(_normalized_symbol_value(row.get("Symbol")), {}),
        }
        for _, row in mtf_display_df.iterrows()
    ]


def build_mtf_holdings_snapshot(
    holdings_df: pd.DataFrame | None,
    holdings_breakdown_df: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    if holdings_df is None or holdings_df.empty:
        return []
    return _mtf_snapshot_rows(holdings_df, holdings_breakdown_df)


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
    dashboard_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    empty_snapshot = {
        "asOf": as_of,
        "totals": {"invested": 0, "current": 0, "pnl": 0, "pnlPct": 0, "dayPnl": 0, "dayPnlPct": 0},
        "sectors": [],
        "mtfHoldings": _mtf_snapshot_rows(holdings_df, holdings_breakdown_df),
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
            symbol = _component_text(holding.get("Symbol"))
            position_chart = []
            if dashboard_df is not None and not dashboard_df.empty and symbol in dashboard_df.columns:
                position_chart = position_line_chart_points_from_dashboard_column(dashboard_df[symbol])
            holdings.append(
                {
                    "symbol": symbol,
                    "quantity": _component_int(holding.get("Quantity")),
                    "averagePrice": _component_number(holding.get("Avg Price")),
                    "invested": holding_invested,
                    "weightPct": (holding_invested / sector_invested * 100) if sector_invested else 0,
                    "current": _component_number(holding.get("Current")),
                    "ltp": _component_number(holding.get("LTP")),
                    "pnl": _component_number(holding.get("P&L")),
                    "pnlPct": _component_number(holding.get("P&L %")),
                    "dayChangePct": _component_number(holding.get("DayChg %")),
                    "positionChart": position_chart,
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
        "mtfHoldings": _mtf_snapshot_rows(holdings_df, holdings_breakdown_df),
    }
