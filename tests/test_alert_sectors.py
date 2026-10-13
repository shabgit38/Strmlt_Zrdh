import unittest

from setAlerts import _sector_label_for_index, _sector_map_from_index_records


class AlertSectorTests(unittest.TestCase):
    def test_sector_index_name_maps_to_display_sector(self):
        self.assertEqual(_sector_label_for_index("NIFTY IT"), "Information Technology")
        self.assertEqual(_sector_label_for_index("NIFTY PRIVATE BANK"), "Banking")
        self.assertIsNone(_sector_label_for_index("NIFTY 50"))

    def test_sector_map_normalizes_symbols_and_combines_sector_memberships(self):
        sector_by_symbol = _sector_map_from_index_records(
            [
                {"Index": "NIFTY IT", "Constituents": "INFY, TCS"},
                {"Index": "NIFTY BANK", "Constituents": "INFY, HDFCBANK"},
                {"Index": "NIFTY 50", "Constituents": "INFY, RELIANCE"},
            ]
        )

        self.assertEqual(
            sector_by_symbol,
            {
                "INFY": "Banking, Information Technology",
                "TCS": "Information Technology",
                "HDFCBANK": "Banking",
            },
        )


if __name__ == "__main__":
    unittest.main()
