import unittest
from unittest.mock import patch

import pandas as pd

import portfolio_streamlit
from portfolio_terminal.backend import portfolio_snapshot


MIXED_HOLDING = {
    "tradingsymbol": "TATASTEEL",
    "isin": "INE081A01020",
    "quantity": 100,
    "average_price": 150.0,
    "last_price": 160.0,
    "pnl": 1000.0,
    "day_change_percentage": 1.0,
    "mtf": {
        "quantity": 40,
        "average_price": 155.0,
        "value": 6200.0,
        "initial_margin": 2000.0,
    },
}

BREAKDOWN_ROW = {
    "row_type": "SUMMARY",
    "symbol": "TATASTEEL",
    "isin": "INE081A01020",
    "sector": "Metals",
}


class MixedMtfHoldingsTests(unittest.TestCase):
    @patch.object(portfolio_streamlit, "_portfolio_component_batches", return_value=[])
    def test_streamlit_snapshot_keeps_delivery_and_mtf_portions(self, _build_batches):
        snapshot = portfolio_streamlit.build_portfolio_terminal_snapshot(
            pd.DataFrame([MIXED_HOLDING]),
            pd.DataFrame([BREAKDOWN_ROW]),
            as_of="2026-08-14",
        )

        self.assertEqual(snapshot["sectors"][0]["sector"], "Metals")
        self.assertEqual(snapshot["sectors"][0]["holdings"][0]["symbol"], "TATASTEEL")
        self.assertEqual(snapshot["sectors"][0]["holdings"][0]["quantity"], 100)
        self.assertEqual(snapshot["mtfHoldings"][0]["mtfQty"], 40)
        self.assertAlmostEqual(snapshot["totals"]["invested"], 21200.0)
        self.assertAlmostEqual(snapshot["totals"]["current"], 22400.0)

    def test_streamlit_snapshot_retains_totals_for_mtf_only_portfolio(self):
        mtf_only = {
            **MIXED_HOLDING,
            "quantity": 0,
            "average_price": 0.0,
            "pnl": 0.0,
        }

        snapshot = portfolio_streamlit.build_portfolio_terminal_snapshot(
            pd.DataFrame([mtf_only]),
            pd.DataFrame([BREAKDOWN_ROW]),
            as_of="2026-08-14",
        )

        self.assertEqual(snapshot["sectors"], [])
        self.assertEqual(snapshot["mtfHoldings"][0]["mtfQty"], 40)
        self.assertAlmostEqual(snapshot["totals"]["invested"], 6200.0)
        self.assertAlmostEqual(snapshot["totals"]["current"], 6400.0)

    @patch.object(portfolio_snapshot, "_load_holdings_breakdown", return_value=[BREAKDOWN_ROW])
    @patch.object(portfolio_snapshot, "_kite_client")
    def test_backend_snapshot_keeps_delivery_and_mtf_portions(self, kite_client, _load_breakdown):
        kite_client.return_value.holdings.return_value = [MIXED_HOLDING]

        snapshot = portfolio_snapshot.build_live_portfolio_snapshot()

        self.assertEqual(snapshot["sectors"][0]["sector"], "Metals")
        self.assertEqual(snapshot["sectors"][0]["holdings"][0]["quantity"], 100)
        self.assertEqual(snapshot["mtfHoldings"][0]["mtfQty"], 40)
        self.assertAlmostEqual(snapshot["totals"]["invested"], 21200.0)
        self.assertAlmostEqual(snapshot["totals"]["current"], 22400.0)


if __name__ == "__main__":
    unittest.main()
