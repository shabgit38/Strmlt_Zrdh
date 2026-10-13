import unittest
from unittest.mock import patch

import pandas as pd
from kiteconnect import KiteConnect

from kite_analytics import get_kite_historical_data


class HistoricalDataTests(unittest.TestCase):
    def setUp(self):
        self.kite = KiteConnect(api_key="test")

    def test_daily_history_keeps_last_candle_per_date(self):
        candles = [
            {
                "date": "2026-01-02 00:00:00",
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
                "volume": 100,
            },
            {
                "date": "2026-01-02 15:30:00",
                "open": 11,
                "high": 13,
                "low": 10,
                "close": 12,
                "volume": 200,
            },
            {
                "date": "2026-01-05 00:00:00",
                "open": 12,
                "high": 14,
                "low": 11,
                "close": 13,
                "volume": 300,
            },
        ]
        with patch.object(self.kite, "historical_data", return_value=candles):
            history = get_kite_historical_data(
                self.kite,
                123,
                "day",
                "2026-01-01 00:00:00",
                "2026-01-06 23:59:59",
            )

        self.assertEqual(len(history), 2)
        self.assertEqual(history.index.normalize().nunique(), 2)
        self.assertEqual(history.iloc[0]["Close"], 12)
        self.assertEqual(history.iloc[0]["Volume"], 200)

    def test_intraday_history_keeps_distinct_times_on_same_date(self):
        candles = [
            {
                "date": "2026-01-02 09:15:00",
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
                "volume": 100,
            },
            {
                "date": "2026-01-02 09:16:00",
                "open": 11,
                "high": 13,
                "low": 10,
                "close": 12,
                "volume": 200,
            },
        ]
        with patch.object(self.kite, "historical_data", return_value=candles):
            history = get_kite_historical_data(
                self.kite,
                123,
                "minute",
                "2026-01-02 09:15:00",
                "2026-01-02 09:16:00",
            )

        self.assertEqual(len(history), 2)
        self.assertEqual(history.index.normalize().nunique(), 1)


if __name__ == "__main__":
    unittest.main()
