import unittest

from portfolio_terminal_component import _target_strikes_for_spot


class PortfolioTerminalStrikeTests(unittest.TestCase):
    def test_strikes_use_100_point_intervals_across_five_percent_range(self):
        strikes = _target_strikes_for_spot(24000)

        self.assertEqual(strikes, [float(strike) for strike in range(22800, 25201, 100)])

    def test_strikes_round_range_edges_to_100_point_steps(self):
        strikes = _target_strikes_for_spot(24090)

        self.assertEqual(strikes, [float(strike) for strike in range(22800, 25301, 100)])


if __name__ == "__main__":
    unittest.main()
