import unittest

from kite_analytics import (
    _format_position_line_chart_html,
    _position_line_chart_points,
    _sort_position_chart_points,
)


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

    def test_position_chart_marks_ltp_for_initial_scroll_focus(self):
        chart_html = _format_position_line_chart_html(
            "1Y Low -20.00% 80 | LTP 100 | 1Y High +20.00% 120"
        )

        self.assertEqual(chart_html.count("data-position-current='true'"), 1)
        self.assertIn("data-position-chart='true'", chart_html)
        self.assertIn("current.getBoundingClientRect().left", chart_html)


if __name__ == "__main__":
    unittest.main()