import unittest

from setAlerts import (
    _alert_distance_percentage,
    _alert_reset_skip_reason,
    _enabled_alert_reset_target,
    _nearest_lower_ema,
    _reset_alert_trigger_target,
)


class AlertResetTests(unittest.TestCase):
    def test_selects_closest_ema_at_or_below_current_price(self):
        self.assertEqual(
            _nearest_lower_ema(
                100,
                {"EMA10": 101, "EMA20": 99, "EMA50": 90, "EMA100": 80, "EMA200": 70},
            ),
            ("EMA20", 99.0),
        )

    def test_returns_no_ema_when_all_levels_are_above_current_price(self):
        self.assertIsNone(_nearest_lower_ema(100, {"EMA10": 101, "EMA20": 102}))

    def test_uses_half_percent_ltp_fallback_when_no_lower_ema_exists(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 101}, "<="),
            (99.5, True, False),
        )

    def test_moves_trigger_above_ltp_when_upward_condition_is_already_met(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 99}, ">="),
            (100.5, False, True),
        )

    def test_keeps_lower_ema_when_downward_condition_is_not_met(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 99}, "<="),
            (99.0, False, False),
        )

    def test_distance_matches_alerts_column_percentage(self):
        self.assertEqual(
            _alert_distance_percentage(
                {"rhs_type": "constant", "ltp": 100, "rhs_constant": 103},
            ),
            3.0,
        )

    def test_enabled_reset_moves_trigger_half_percent_to_safe_side(self):
        self.assertEqual(_enabled_alert_reset_target(100, ">="), 100.5)
        self.assertEqual(_enabled_alert_reset_target(100, "<="), 99.5)

    def test_uses_safe_fallback_side_for_upward_comparator(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 101}, ">="),
            (100.5, True, True),
        )

    def test_only_simple_constant_ltp_alerts_are_resettable(self):
        alert = {
            "type": "simple",
            "rhs_type": "constant",
            "lhs_attribute": "LastTradedPrice",
        }
        self.assertIsNone(_alert_reset_skip_reason(alert))
        self.assertEqual(
            _alert_reset_skip_reason({**alert, "type": "ato"}),
            "unsupported alert type",
        )
        self.assertEqual(
            _alert_reset_skip_reason({**alert, "rhs_type": "instrument"}),
            "only constant-price alerts are supported",
        )


if __name__ == "__main__":
    unittest.main()
