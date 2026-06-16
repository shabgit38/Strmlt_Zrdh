# Code Logic Reference

## DayChg % Source In Kite Holdings Table

`DayChg %` is received directly from the Kite holdings API as the
`day_change_percentage` field.

In `getHoldings.py`, `display_kite_holdings()` includes the raw API column:

```text
day_change_percentage
```

The code only renames it for display:

```text
day_change_percentage -> DayChg %
```

There is no local calculation for `DayChg %` in the holdings table.

Locally calculated holdings fields include:

```text
invested = average_price * quantity
CurrentValue = last_price * quantity
pnl_pct = pnl / invested * 100
```

## Today Return % Logic In Returns Dataframe

`Today Return %` is calculated locally in `kite_analytics.py` inside
`compute_period_returns()`.

Formula used:

```text
(today_ltp - previous_close) / previous_close * 100
```

Considerations:

- The value is added only when the latest Kite historical daily candle date
  is today's date.
- At least two historical rows are required, because the calculation needs
  the previous daily candle close.
- `today_ltp` uses the live Kite holdings `last_price` when
  `build_historic_dashboard_frames()` receives `ltp_key="last_price"`.
- If live `last_price` is not available, `today_ltp` falls back to the latest
  historical candle `Close`.
- `previous_close` is read from the previous row in the historical dataframe:
  `df.iloc[-2]["Close"]`.
- If today's candle is not available, `previous_close` is missing, or
  `previous_close` is zero, `Today Return %` is left blank/`None`.
- This is a previous-close daily return, not an open-to-current-day return.


### getholdingd.py - price ladder
in Price Ladder, LTP currently comes from the latest historical Close inside build_metric_values(), not necessarily the live Kite last_price passed into returns. Formula is correct; only the price source may differ depending on whether today’s daily candle is available.

### momentum_tracker.py 
this file is a test on momentum card layout with horizontal price chart

### momentum_score.py is referenced in two places:
getHoldings.py (line 21)
check_momentum_score.py (line 11)
feature_testing.py is not impoted or referenced by other Python files

### Built a Python snapshot converter 
at [getHoldings.py (line 537)] using current Kite holdings plus loaded breakdown rows.

# Implemented the LTP consistency change.
What changed:
-LTP now represents live quote data when available.
-Historical fallback is exposed as Latest Close, not mislabeled as LTP.
Historic price ladder now accepts/passes live LTP and uses it for range position / EMA distance / day mover current-price calculations.
-Momentum scoring now accepts live_ltp_by_symbol; current-price features use live LTP when available, while historical returns/momentum lookbacks still use the historical close series.
-Momentum displays now show both LTP and Latest Close.
-momentum_tracker.py now fetches live quotes for cards and falls back to Latest Close labeling if live quote fetch fails