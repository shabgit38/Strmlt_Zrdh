import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import getHldgBrk


class HoldingsBreakdownOrderTests(unittest.TestCase):
    def test_full_batch_exit_marks_summary_exited(self):
        summary = pd.Series({"id": 10, "symbol": "ABC", "ltp": 12.5})
        holdings_df = pd.DataFrame(
            [
                {
                    "row_type": "BATCH",
                    "symbol": "ABC",
                    "batch_qty": 5,
                    "batch_price": 10.0,
                    "holding_status": "Exited",
                    "exit_qty": 5,
                }
            ]
        )

        with (
            patch.object(getHldgBrk, "load_holdings_breakdown_from_supabase", return_value=holdings_df),
            patch.object(getHldgBrk, "update_holdings_breakdown_row") as update_row,
        ):
            getHldgBrk._recalculate_summary_from_supabase_batches(summary, {})

        record = update_row.call_args.args[1]
        self.assertEqual(record["total_qty"], 0)
        self.assertEqual(record["holding_status"], "Exited")

    def test_partial_batch_exit_clears_summary_exited_status(self):
        summary = pd.Series(
            {"id": 10, "symbol": "ABC", "ltp": 12.5, "holding_status": "Exited"}
        )
        holdings_df = pd.DataFrame(
            [
                {
                    "row_type": "BATCH",
                    "symbol": "ABC",
                    "batch_qty": 3,
                    "batch_price": 10.0,
                },
                {
                    "row_type": "BATCH",
                    "symbol": "ABC",
                    "batch_qty": 2,
                    "batch_price": 10.0,
                    "holding_status": "Exited",
                    "exit_qty": 2,
                },
            ]
        )

        with (
            patch.object(getHldgBrk, "load_holdings_breakdown_from_supabase", return_value=holdings_df),
            patch.object(getHldgBrk, "update_holdings_breakdown_row") as update_row,
        ):
            getHldgBrk._recalculate_summary_from_supabase_batches(summary, {})

        record = update_row.call_args.args[1]
        self.assertEqual(record["total_qty"], 3)
        self.assertIsNone(record["holding_status"])

    def test_sell_order_uses_event_trade_date_for_exit(self):
        today = date.today()
        timestamp = f"{today.isoformat()} 10:00:00"
        symbol_df = pd.DataFrame(
            [
                {"row_type": "SUMMARY", "symbol": "ABC"},
                {
                    "row_type": "BATCH",
                    "symbol": "ABC",
                    "id": 1,
                    "batch_qty": 5,
                    "trade_date": today.isoformat(),
                },
            ]
        )
        order = {
            "status": "COMPLETE",
            "order_id": "order-1",
            "tradingsymbol": "ABC",
            "filled_quantity": 2,
            "average_price": 12.5,
            "order_timestamp": timestamp,
            "transaction_type": "SELL",
        }

        with (
            patch.object(getHldgBrk.st, "session_state", {}),
            patch.object(
                getHldgBrk,
                "load_holdings_breakdown_from_supabase",
                return_value=pd.DataFrame(),
            ),
            patch.object(
                getHldgBrk,
                "load_holdings_breakdown_for_symbols",
                return_value=symbol_df,
            ),
            patch.object(getHldgBrk, "_apply_batch_exit") as apply_batch_exit,
            patch.object(getHldgBrk, "_recalculate_summary_from_supabase_batches"),
            patch.object(getHldgBrk, "sync_exited_holdings_index"),
        ):
            affected_symbols = getHldgBrk.update_holdings_breakdown_from_orders([order])

        self.assertEqual(affected_symbols, ["ABC"])
        apply_batch_exit.assert_called_once()
        self.assertEqual(apply_batch_exit.call_args.kwargs["exit_date"], today)


if __name__ == "__main__":
    unittest.main()