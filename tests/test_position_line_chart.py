import unittest

from kite_analytics import _position_line_chart_points, _sort_position_chart_points


class PositionLineChartTests(unittest.TestCase):
    def test_equal_yearly_lows_keep_longer_coverage_label(self):
        points = _sort_position_chart_points(
            _position_line_chart_points(
                "LTP 1,500 | <5Y Low -16.67% 1,245.05 | 4Y Low -16.67% 1,245.05"
            )
        )

        self.assertEqual(
            [point["label"] for point in points],
            ["LTP", "<5Y Low"],
        )

    def test_equal_yearly_highs_keep_longer_coverage_label(self):
        points = _sort_position_chart_points(
            _position_line_chart_points(
                "LTP 1,500 | 4Y High -16.67% 1,800 | <5Y High -16.67% 1,800"
            )
        )

        self.assertEqual(
            [point["label"] for point in points],
            ["<5Y High", "LTP"],
        )


if __name__ == "__main__":
    unittest.main()