import unittest

import pandas as pd

from kite_analytics import (
    build_metric_values,
    _format_position_line_chart_html,
    _format_price_position,
    _position_level_date,
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

    def test_available_history_adds_half_year_level_after_five_years(self):
        dates = pd.bdate_range(start="2021-05-17", end="2026-11-06")
        close = pd.Series(range(100, 100 + len(dates)), index=dates, dtype=float)
        history = pd.DataFrame(
            {"High": close + 2, "Low": close - 2, "Close": close},
            index=dates,
        )

        metrics = build_metric_values(history)
        position = _format_price_position(metrics, history)

        self.assertIn("5Y Low", metrics)
        self.assertIn("5.5Y Low", metrics)
        self.assertIn("5.5Y High", position)
        self.assertEqual(_position_level_date("5.5Y High", history), dates[-1])

    def test_equal_half_year_levels_keep_longer_coverage_label(self):
        points = _sort_position_chart_points(
            _position_line_chart_points(
                "LTP 1,500 | 5Y Low -16.67% 1,245.05 | 5.5Y Low -16.67% 1,245.05"
            )
        )

        self.assertEqual(
            [point["label"] for point in points],
            ["LTP", "5.5Y Low"],
        )

    def test_position_chart_marks_ltp_for_initial_scroll_focus(self):
        chart_html = _format_position_line_chart_html(
            "1Y Low -20.00% 80 | LTP 100 | 1Y High +20.00% 120"
        )

        self.assertEqual(chart_html.count("data-position-current='true'"), 1)
        self.assertIn("data-position-chart='true'", chart_html)
        self.assertIn("current.getBoundingClientRect().left", chart_html)

    def test_position_chart_shows_extreme_month_and_year(self):
        points = _position_line_chart_points(
            "LTP 100 | 1M High +10.00% 110 (2026-09-11)"
        )

        self.assertEqual(points[1]["date"], "2026-09-11")
        chart_html = _format_position_line_chart_html(
            "LTP 100 | 1M High +10.00% 110 (2026-09-11)"
        )
        self.assertIn("Sep 2026", chart_html)

    def test_monthly_extreme_date_uses_completed_month_window(self):
        current_month_start = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).normalize().replace(day=1)
        period_start = current_month_start - pd.DateOffset(months=1)
        frame = pd.DataFrame(
            {"High": [12, 20, 100], "Low": [10, 15, 90]},
            index=[
                period_start + pd.Timedelta(days=1),
                current_month_start - pd.Timedelta(days=1),
                current_month_start + pd.Timedelta(days=1),
            ],
        )

        self.assertEqual(
            _position_level_date("1M High", frame),
            current_month_start - pd.Timedelta(days=1),
        )
        position = _format_price_position(
            {"LTP": 25, "EMA10": 20, "1M High": 20, "1M Low": 10},
            frame,
        )

        self.assertIn(
            f"1M High +25.0% 20 ({(current_month_start - pd.Timedelta(days=1)):%Y-%m-%d})",
            position,
        )


if __name__ == "__main__":
    unittest.main()