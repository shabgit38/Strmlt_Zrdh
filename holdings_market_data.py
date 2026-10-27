from typing import Any

import pandas as pd

from getHldgBrk import _ltp_match_symbol, _normalized_symbol_value


def instrument_lookup_symbols(symbols: list[str]) -> list[str]:
    lookup_symbols = {
        _normalized_symbol_value(symbol)
        for symbol in symbols
        if _normalized_symbol_value(symbol)
    }
    for symbol in tuple(lookup_symbols):
        fallback_symbol = _ltp_match_symbol(symbol)
        if fallback_symbol != symbol:
            lookup_symbols.add(fallback_symbol)
    return sorted(lookup_symbols)


def resolve_tokens_from_tickers(
    tickers: list[str],
    instruments_df: pd.DataFrame,
    exchange_by_symbol: dict[str, str] | None = None,
) -> tuple[dict[str, int], list[str]]:
    """Resolve exact instrument symbols, using a suffix alias only as a fallback."""
    resolved: dict[str, int] = {}
    missing: list[str] = []
    normalized = instruments_df.copy()
    normalized["tradingsymbol"] = normalized["tradingsymbol"].astype(str).str.strip().str.upper()

    for ticker in tickers:
        normalized_ticker = _normalized_symbol_value(ticker)
        matches = normalized[normalized["tradingsymbol"] == normalized_ticker]
        if matches.empty:
            fallback_ticker = _ltp_match_symbol(normalized_ticker)
            if fallback_ticker != normalized_ticker:
                matches = normalized[normalized["tradingsymbol"] == fallback_ticker]
        if matches.empty:
            missing.append(ticker)
            continue

        if "exchange" in matches.columns:
            requested_exchange = str(
                (exchange_by_symbol or {}).get(normalized_ticker) or ""
            ).strip().upper()
            exchanges = matches["exchange"].astype(str).str.strip().str.upper()
            if requested_exchange:
                matches = matches[exchanges == requested_exchange]
            elif len(matches) > 1:
                nse_matches = matches[exchanges == "NSE"]
                if not nse_matches.empty:
                    matches = nse_matches
                else:
                    missing.append(ticker)
                    continue
        if matches.empty:
            missing.append(ticker)
            continue
        resolved[ticker] = int(matches.iloc[0]["instrument_token"])
    return resolved, missing


def _historic_quote_instrument(symbol: str, exchange: str) -> str:
    if exchange == "INDICES":
        index_symbols = {
            "NIFTY 50": "NSE:NIFTY 50",
            "NIFTY BANK": "NSE:NIFTY BANK",
            "SENSEX": "BSE:SENSEX",
        }
        return index_symbols.get(symbol, f"NSE:{symbol}")
    return f"{exchange}:{symbol}"


def fetch_live_ltp(
    kite,
    symbols: list[str],
    exchange_by_symbol: dict[str, str] | None = None,
) -> pd.DataFrame:
    normalized_symbols = sorted(
        {_normalized_symbol_value(symbol) for symbol in symbols if _normalized_symbol_value(symbol)}
    )
    if not normalized_symbols:
        return pd.DataFrame(columns=["Symbol", "LTP"])

    normalized_exchanges = {
        _normalized_symbol_value(symbol): str(exchange).strip().upper()
        for symbol, exchange in (exchange_by_symbol or {}).items()
        if _normalized_symbol_value(symbol) and str(exchange).strip()
    }
    quote_instrument_by_symbol: dict[str, tuple[str, str | None]] = {}
    instruments: list[str] = []
    for symbol in normalized_symbols:
        exchange = normalized_exchanges.get(symbol, "NSE")
        exact_instrument = _historic_quote_instrument(symbol, exchange)
        quote_instrument_by_symbol[symbol] = (exact_instrument, None)
        instruments.append(exact_instrument)
        fallback_symbol = _ltp_match_symbol(symbol)
        if fallback_symbol != symbol:
            fallback_instrument = _historic_quote_instrument(fallback_symbol, exchange)
            quote_instrument_by_symbol[symbol] = (exact_instrument, fallback_instrument)
            instruments.append(fallback_instrument)

    instruments = list(dict.fromkeys(instruments))
    data = kite.ltp(*instruments)
    rows: list[dict[str, Any]] = []
    for symbol, (exact_instrument, fallback_instrument) in quote_instrument_by_symbol.items():
        quote_data = data.get(exact_instrument)
        if (
            (not isinstance(quote_data, dict) or quote_data.get("last_price") is None)
            and fallback_instrument
        ):
            quote_data = data.get(fallback_instrument)
        if isinstance(quote_data, dict) and quote_data.get("last_price") is not None:
            rows.append({"Symbol": symbol, "LTP": float(quote_data["last_price"])})
    return pd.DataFrame(rows, columns=["Symbol", "LTP"])


def fetch_live_ltp_by_symbol(
    kite,
    symbols: list[str],
    exchange_by_symbol: dict[str, str] | None = None,
) -> dict[str, float]:
    ltp_df = fetch_live_ltp(kite, symbols, exchange_by_symbol=exchange_by_symbol)
    if ltp_df.empty:
        return {}
    return {
        str(row["Symbol"]).strip().upper(): float(row["LTP"])
        for _, row in ltp_df.iterrows()
        if pd.notna(row.get("LTP"))
    }


def _apply_live_ltp_to_holdings(
    holdings_df: pd.DataFrame, ltp_df: pd.DataFrame
) -> tuple[pd.DataFrame, list[str]]:
    if holdings_df.empty or "tradingsymbol" not in holdings_df.columns or ltp_df.empty:
        return holdings_df, []

    updated_df = holdings_df.copy()
    symbol_key = updated_df["tradingsymbol"].astype(str).str.strip().str.upper()
    ltp_by_symbol = ltp_df.set_index("Symbol")["LTP"]
    live_ltp = pd.to_numeric(symbol_key.map(ltp_by_symbol), errors="coerce")
    matched = live_ltp.notna()
    if matched.any():
        updated_df.loc[matched, "last_price"] = live_ltp[matched]
        if {"average_price", "quantity"}.issubset(updated_df.columns):
            average_price = pd.to_numeric(updated_df["average_price"], errors="coerce")
            quantity = pd.to_numeric(updated_df["quantity"], errors="coerce")
            last_price = pd.to_numeric(updated_df["last_price"], errors="coerce")
            invested = average_price * quantity
            updated_df["pnl"] = (last_price - average_price) * quantity
            updated_df["pnl_pct"] = updated_df["pnl"].where(invested.ne(0)) / invested * 100

    missing_symbols = sorted(set(symbol_key[~matched].dropna()) - {""})
    return updated_df, missing_symbols
