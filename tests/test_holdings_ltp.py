import unittest

import pandas as pd

from holdings_market_data import (
    _apply_live_ltp_to_holdings,
    fetch_live_ltp,
    fetch_live_ltp_by_symbol,
    instrument_lookup_symbols,
    resolve_tokens_from_tickers,
)


class FakeKite:
    def __init__(self, quotes):
        self.quotes = quotes
        self.requested_instruments = None

    def ltp(self, *instruments):
        self.requested_instruments = instruments
        return self.quotes


class HoldingsLtpTests(unittest.TestCase):
    def test_instrument_lookup_includes_exact_and_suffix_alias_symbols(self):
        self.assertEqual(
            instrument_lookup_symbols(["BIRET-RR", "INDIGRID-IV", "NSDL"]),
            ["BIRET", "BIRET-RR", "INDIGRID", "INDIGRID-IV", "NSDL"],
        )

    def test_live_quote_requests_keep_exact_suffix_symbols(self):
        kite = FakeKite(
            {
                "NSE:BIRET-RR": {"last_price": 100.0},
                "NSE:INDIGRID-IV": {"last_price": 150.0},
            }
        )

        ltp_df = fetch_live_ltp(
            kite,
            ["BIRET-RR", "INDIGRID-IV"],
            exchange_by_symbol={"BIRET-RR": "NSE", "INDIGRID-IV": "NSE"},
        )
        ltp_by_symbol = fetch_live_ltp_by_symbol(
            kite,
            ["BIRET-RR", "INDIGRID-IV"],
            exchange_by_symbol={"BIRET-RR": "NSE", "INDIGRID-IV": "NSE"},
        )

        self.assertEqual(
            kite.requested_instruments,
            (
                "NSE:BIRET-RR",
                "NSE:BIRET",
                "NSE:INDIGRID-IV",
                "NSE:INDIGRID",
            ),
        )
        self.assertEqual(
            dict(zip(ltp_df["Symbol"], ltp_df["LTP"])),
            {"BIRET-RR": 100.0, "INDIGRID-IV": 150.0},
        )
        self.assertEqual(ltp_by_symbol, {"BIRET-RR": 100.0, "INDIGRID-IV": 150.0})

    def test_live_quote_uses_base_alias_only_when_exact_quote_is_absent(self):
        kite = FakeKite(
            {
                "NSE:BIRET": {"last_price": 99.0},
                "NSE:INDIGRID-IV": {"last_price": 151.0},
                "NSE:INDIGRID": {"last_price": 150.0},
            }
        )

        ltp_by_symbol = fetch_live_ltp_by_symbol(
            kite,
            ["BIRET-RR", "INDIGRID-IV"],
            exchange_by_symbol={"BIRET-RR": "NSE", "INDIGRID-IV": "NSE"},
        )

        self.assertEqual(ltp_by_symbol, {"BIRET-RR": 99.0, "INDIGRID-IV": 151.0})

    def test_live_quote_uses_holding_exchange_for_nsdl(self):
        kite = FakeKite({"BSE:NSDL": {"last_price": 800.0}})

        ltp_by_symbol = fetch_live_ltp_by_symbol(
            kite,
            ["NSDL"],
            exchange_by_symbol={"NSDL": "BSE"},
        )

        self.assertEqual(kite.requested_instruments, ("BSE:NSDL",))
        self.assertEqual(ltp_by_symbol, {"NSDL": 800.0})

    def test_live_ltp_updates_exact_holding_symbol_not_base_alias(self):
        holdings = pd.DataFrame(
            [{"tradingsymbol": "BIRET-RR", "last_price": 0.0, "quantity": 2}]
        )
        ltp_df = pd.DataFrame([{"Symbol": "BIRET", "LTP": 101.0}])

        updated, missing = _apply_live_ltp_to_holdings(holdings, ltp_df)

        self.assertEqual(updated.loc[0, "last_price"], 0.0)
        self.assertEqual(missing, ["BIRET-RR"])

    def test_token_resolution_uses_exact_symbol_and_exchange(self):
        instruments = pd.DataFrame(
            [
                {"tradingsymbol": "BIRET-RR", "exchange": "NSE", "instrument_token": 101},
                {"tradingsymbol": "BIRET", "exchange": "NSE", "instrument_token": 102},
                {"tradingsymbol": "NSDL", "exchange": "NSE", "instrument_token": 201},
                {"tradingsymbol": "NSDL", "exchange": "BSE", "instrument_token": 202},
            ]
        )

        tokens, missing = resolve_tokens_from_tickers(
            ["BIRET-RR", "NSDL"],
            instruments,
            exchange_by_symbol={"BIRET-RR": "NSE", "NSDL": "BSE"},
        )

        self.assertEqual(tokens, {"BIRET-RR": 101, "NSDL": 202})
        self.assertEqual(missing, [])

    def test_token_resolution_reports_exchange_mismatch(self):
        instruments = pd.DataFrame(
            [{"tradingsymbol": "NSDL", "exchange": "NSE", "instrument_token": 201}]
        )

        tokens, missing = resolve_tokens_from_tickers(
            ["NSDL"],
            instruments,
            exchange_by_symbol={"NSDL": "BSE"},
        )

        self.assertEqual(tokens, {})
        self.assertEqual(missing, ["NSDL"])

    def test_token_resolution_uses_unique_base_alias_when_exact_symbol_is_absent(self):
        instruments = pd.DataFrame(
            [{"tradingsymbol": "INDIGRID", "exchange": "NSE", "instrument_token": 301}]
        )

        tokens, missing = resolve_tokens_from_tickers(
            ["INDIGRID-IV"],
            instruments,
            exchange_by_symbol={"INDIGRID-IV": "NSE"},
        )

        self.assertEqual(tokens, {"INDIGRID-IV": 301})
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
