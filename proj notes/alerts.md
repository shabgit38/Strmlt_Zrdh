# Alerts tab

The Alerts tab fetches Kite alerts and displays status, symbol, sector, LTP, trigger, distance, and update date. Use search, status and sector filters, or sortable columns to find alerts. Simple constant-price alerts can be created, edited, and deleted; unsupported alert types are view-only.

When Kite rejects a new alert because its condition is already met, the app fetches the instrument's current LTP, moves the trigger 1% to the non-triggering side (`<`/`<=` below LTP; `>`/`>=` above LTP), and retries creation once. The success message reports the adjusted trigger. Other errors are not retried.

## Reset alerts

After alerts are loaded, **Reset alerts** is enabled when there is at least one disabled alert or one eligible enabled alert more than 2% from its trigger. It asks for confirmation before making changes.

- **Disabled alerts:** Supported simple `LastTradedPrice` alerts with constant triggers are set to the nearest EMA at least 1% on the non-triggering side of current LTP (EMA10/20/50/100/200). For `<`/`<=`, use the lower side; for `>`/`>=`, use the upper side. Equality uses the upper side. If no EMA meets the gap, the fallback is 1% from LTP on that side. The alert is re-enabled.
- **Enabled alerts:** Every supported simple constant-price alert whose trigger is not already at the 1% LTP target is moved to that target using the LTP shown in the table. The comparator determines the safe side: above for `>`/`>=` and below for `<`/`<=`; equality uses above. Alerts already at the target are left unchanged.
- **Comparator safety:** The existing comparator is preserved. If the proposed trigger would already satisfy the alert condition at current LTP, the trigger is moved 1% to the non-triggering side: above LTP for `>=`/`>` and below LTP for `<=`/`<`. Equality alerts use the above-LTP side.
- **Unsupported or unavailable data:** ATO, non-constant, unsupported price-attribute, or missing-data alerts are skipped. The result message reports reset, fallback, adjusted, skipped, and failed counts; details are shown for skipped or failed alerts.

Enabled-alert eligibility and LTP use the currently loaded table data; click **Get alerts** first to refresh it. Disabled alerts and their LTPs are fetched during reset. Each successful reset updates its alert through Kite.
