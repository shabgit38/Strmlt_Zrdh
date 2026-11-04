import unittest
from unittest.mock import patch

from setAlerts import (
    _alert_distance_percentage,
    _alert_reset_skip_reason,
    _create_alert_with_condition_adjustment,
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

    def test_uses_one_percent_ltp_fallback_when_no_lower_ema_meets_gap(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 101}, "<="),
            (99.0, True),
        )

    def test_selects_nearest_upper_ema_beyond_minimum_gap_for_upward_condition(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 99, "EMA20": 101.5, "EMA50": 103}, ">="),
            (101.5, False),
        )

    def test_selects_nearest_lower_ema_that_meets_minimum_gap(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 99.5, "EMA20": 98.5, "EMA50": 95}, "<="),
            (98.5, False),
        )

    def test_upward_reset_uses_one_percent_fallback_when_no_upper_ema_meets_gap(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 100.5, "EMA20": 99}, ">"),
            (101.0, True),
        )

    def test_ema_price_is_rounded_outward_to_preserve_minimum_gap(self):
        target_price, used_fallback = _reset_alert_trigger_target(
            100,
            {"EMA10": 98.999, "EMA20": 100.5},
            "<",
        )
        self.assertEqual(target_price, 98.99)
        self.assertFalse(used_fallback)
        self.assertGreaterEqual((100 - target_price) / 100, 0.01)

    def test_distance_matches_alerts_column_percentage(self):
        self.assertEqual(
            _alert_distance_percentage(
                {"rhs_type": "constant", "ltp": 100, "rhs_constant": 103},
            ),
            3.0,
        )

    def test_enabled_reset_moves_trigger_one_percent_to_safe_side(self):
        self.assertEqual(_enabled_alert_reset_target(100, ">="), 101.0)
        self.assertEqual(_enabled_alert_reset_target(100, "<="), 99.0)

    def test_create_adjusts_already_met_downward_condition_and_retries_once(self):
        fields = {
            "name": "Test",
            "lhs_exchange": "NSE",
            "lhs_tradingsymbol": "TEST",
            "operator": "<=",
            "rhs_constant": "101",
        }
        kite = type("Kite", (), {"ltp": lambda self, instrument: {instrument: {"last_price": 100}}})()
        with patch(
            "setAlerts.create_alert",
            side_effect=[RuntimeError("The rule condition is already met"), {"uuid": "created"}],
        ) as create:
            response, payload, adjustment = _create_alert_with_condition_adjustment(
                kite,
                "key",
                "token",
                fields,
            )

        self.assertEqual(response, {"uuid": "created"})
        self.assertEqual(payload["rhs_constant"], "99.00")
        self.assertEqual(adjustment, {"previous": 101.0, "target": 99.0, "ltp": 100.0, "direction": "below"})
        self.assertEqual(create.call_count, 2)

    def test_create_adjusts_already_met_upward_condition_above_ltp(self):
        fields = {
            "name": "Test",
            "lhs_exchange": "NSE",
            "lhs_tradingsymbol": "TEST",
            "operator": ">=",
            "rhs_constant": "99",
        }
        kite = type("Kite", (), {"ltp": lambda self, instrument: {instrument: {"last_price": 100}}})()
        with patch(
            "setAlerts.create_alert",
            side_effect=[RuntimeError("The rule condition is already met"), {"uuid": "created"}],
        ):
            _, payload, adjustment = _create_alert_with_condition_adjustment(
                kite,
                "key",
                "token",
                fields,
            )

        self.assertEqual(payload["rhs_constant"], "101.00")
        self.assertEqual(adjustment["direction"], "above")

    def test_create_does_not_retry_other_api_errors(self):
        fields = {
            "name": "Test",
            "lhs_exchange": "NSE",
            "lhs_tradingsymbol": "TEST",
            "operator": "<=",
            "rhs_constant": "99",
        }
        with patch("setAlerts.create_alert", side_effect=RuntimeError("API unavailable")) as create:
            with self.assertRaisesRegex(RuntimeError, "API unavailable"):
                _create_alert_with_condition_adjustment(object(), "key", "token", fields)

        create.assert_called_once()

    def test_uses_upper_ema_when_it_meets_minimum_gap_for_upward_comparator(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 101}, ">="),
            (101.0, False),
        )

    def test_equal_comparator_uses_upper_ema_side(self):
        self.assertEqual(
            _reset_alert_trigger_target(100, {"EMA10": 99, "EMA20": 101.25}, "=="),
            (101.25, False),
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
