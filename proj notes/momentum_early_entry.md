# Momentum Dashboard — Early Entry Signal Upgrade

## 1. Objective

Upgrade the existing Momentum Dashboard so it can identify **early entry opportunities in strong stocks**, rather than mainly identifying stocks that are already near/all-time highs.

### Core principle

Do **not** replace the existing 100-point Momentum/Pullback Score.

Separate the system into three layers:

```text
Momentum Score → Is the stock strong?
        ↓
Entry Setup    → Is an attractive setup forming?
        ↓
Entry Trigger  → Has the move actually started?
```

The existing score remains the primary **stock-quality filter**. The new Setup/Trigger layer provides **entry timing**.

---

# 2. Existing Logic — Preserve

## Data preparation

- Current holdings universe.
- Two years of daily history.
- Relative strength versus configured benchmark, currently Nifty.
- Live holding LTP where available.
- Sort by `pullback_score`, descending.
- Missing scores last.

## Existing Pullback Score — 100 points

| Component | Points |
|---|---:|
| EMA10 > EMA20 > EMA50 > EMA100 > EMA200 | 20 |
| EMA10 extension between -3% and +7% | 15 |
| Price inside EMA20 → EMA20 + 0.5 × ATR14 | 20 |
| RSI 50–70 | 10 |
| Volume below VolumeMA20 | 15 |
| 50-day Z-score -2 to +2.5 | 5 |
| Positive RS vs Nifty | 15 |
| **Total** | **100** |

## Existing summary groups

```text
Strong Entry:
    pullback_score >= 80
    AND price > EMA20

Watchlist - Below EMA20:
    pullback_score >= 80
    AND price <= EMA20

Near Entry:
    65 <= score < 80

Wait:
    45 <= score < 65

Avoid:
    score < 45
    OR missing/invalid
```

Do not remove these groups immediately. The new implementation should initially run alongside them so the new signals can be validated.

---

# 3. Problem With the Existing Model

The current score rewards established momentum:

```text
Bullish EMA stack
+ positive relative strength
+ price above EMA20
+ controlled extension
```

Consequently, high-scoring stocks can already be:

- close to 52-week/3-month highs,
- close to ATH,
- materially above EMA20,
- technically strong but late for a fresh entry.

The dashboard therefore needs to distinguish:

```text
Strong Stock
```

from:

```text
Strong Stock + Fresh Entry Opportunity
```

---

# 4. New Architecture

Implement the following conceptual pipeline:

```text
                    MOMENTUM ENGINE
                          │
                  Existing Score
                          │
                          ▼
                    STOCK QUALITY
                          │
                          ▼
                    ENTRY SETUP
                          │
          ┌───────────────┼───────────────┐
          │               │               │
      Pullback       Consolidation      Support
          │               │               │
          └───────────────┼───────────────┘
                          ▼
                    ENTRY TRIGGER
                          │
          ┌───────────────┼───────────────┐
          │               │               │
     Weekly Breakout   EMA Bounce     Reclaim
          │               │               │
          └───────────────┼───────────────┘
                          ▼
                    ENTRY STATUS
```

---

# 5. New Derived Data

Add these daily/rolling fields.

## Structural highs

```python
prev_week_high
prev_week_low
prev_week_close

prev_month_high
prev_month_low
prev_month_close

high_20d
high_60d
```

Important:

- `prev_week_high` must refer to the **completed previous week**, not the current week.
- `prev_month_high` must refer to the **completed previous calendar month**.
- Avoid look-ahead bias.
- `high_20d` and `high_60d` should use completed prior periods when used as breakout triggers.

## Existing indicators

Reuse existing:

```text
EMA10
EMA20
EMA50
EMA100
EMA200
ATR14
RSI14
VolumeMA20
Z-score 50
RS vs Nifty
```

## New helper fields

```text
atr_pct
volume_ratio
ema20_distance_pct
ema50_distance_pct

recent_pullback
days_since_pullback

weekly_breakout
monthly_breakout
high20_breakout
high60_breakout

ema20_bounce
ema50_reclaim

pullback_pickup
entry_trigger
entry_setup
entry_status
trigger_age
```

---

# 6. Recent Pullback Detection

The key improvement is to preserve the memory of a pullback.

A stock may have touched support 2–5 sessions ago and already moved away from it today. The current pullback score may no longer recognize that setup.

## MVP definition

A stock has a recent pullback if, during the previous 5 trading sessions:

```text
Low <= EMA20 + 0.5 × ATR14
```

Suggested implementation:

```python
pullback_zone_upper = ema20 + 0.5 * atr14

recent_pullback = (
    low.rolling(5).min() <= pullback_zone_upper
)
```

The implementation must avoid accidental inclusion of today's breakout candle when measuring the prior setup.

Preferred conceptual definition:

```text
recent_pullback =
    at least one of the previous 5 completed sessions
    touched the EMA20 pullback zone
```

---

# 7. Pullback → Pickup Signal

This is the highest-priority new signal.

## MVP conditions

```text
recent_pullback
AND Close > PreviousDayHigh
AND Close > EMA10
```

Optional stronger confirmation:

```text
VolumeRatio >= 1.0
```

Name:

```text
Pullback Pickup
```

## Strong version

```text
recent_pullback
AND Close > PreviousDayHigh
AND Close > EMA10
AND Close > PrevWeekHigh
```

Name:

```text
Pullback Pickup + Weekly Breakout
```

Do not require every confirmation in the initial version. Keep the basic and strong versions distinguishable.

---

# 8. Weekly High Breakout

This should be one of the primary early-entry signals.

## Trigger

```text
Close > PrevWeekHigh
AND PreviousClose <= PrevWeekHigh
```

This detects a new breakout rather than simply identifying a stock that has remained above the level for several days.

## Confirmation

Calculate:

```text
volume_ratio = Volume / VolumeMA20
```

Then optionally classify:

```text
volume_ratio >= 1.2
```

as volume-confirmed.

Suggested labels:

```text
Weekly Breakout
Weekly Breakout + Volume
```

Do not require volume confirmation for the base signal.

---

# 9. Previous Month Breakout

## Trigger

```text
Close > PrevMonthHigh
AND PreviousClose <= PrevMonthHigh
```

Label:

```text
Monthly Breakout
```

This is a stronger structural confirmation but generally later than the weekly breakout.

---

# 10. 20-Day and 60-Day Breakouts

Add:

```text
20D Breakout
60D Breakout
```

Use completed prior-period highs.

Example:

```python
prior_high_20d = high.shift(1).rolling(20).max()
breakout_20d = (
    close > prior_high_20d
    & (close.shift(1) <= prior_high_20d)
)
```

Do the equivalent for 60 days.

These should be tracked even if they are not initially promoted to the main dashboard.

---

# 11. EMA20 Bounce

Detect a shallow trend pullback.

## MVP

```text
Low <= EMA20 + 0.25 × ATR14
AND Close > EMA20
AND Close > Open
AND Close > PreviousClose
```

Recommended trend filter:

```text
EMA20 > EMA50
AND EMA50 > EMA200
```

Label:

```text
EMA20 Bounce
```

This is a setup/trigger combination, not a new momentum score component.

---

# 12. EMA50 Reclaim

For deeper corrections:

```text
PreviousClose < EMA50
AND Close > EMA50
AND EMA50 > EMA200
AND RS_vs_Nifty > 0
```

Label:

```text
EMA50 Reclaim
```

Treat this as a lower-confidence signal than a clean EMA20 pullback in the initial version.

---

# 13. Consolidation / Volatility Contraction

Do not over-engineer this initially.

Calculate:

```python
atr_pct = atr14 / close
atr_pct_ma20 = atr_pct.rolling(20).mean()

volatility_contraction = (
    atr_pct < atr_pct_ma20
)
```

Then combine with a breakout:

```text
volatility_contraction
AND Close > PrevWeekHigh
```

Label:

```text
Contraction Breakout
```

This should be Phase 2 rather than a mandatory Phase 1 trigger.

---

# 14. Volume Logic

The current score uses:

```text
Volume < VolumeMA20
```

Keep this because low volume during a pullback can indicate reduced selling pressure.

But the trigger layer should use volume differently.

## Pullback

```text
VolumeRatio < 1
```

Interpretation:

```text
Selling pressure/consolidation is controlled.
```

## Breakout

```text
VolumeRatio >= 1.2
```

Interpretation:

```text
Breakout has stronger participation.
```

Do not modify the existing 15-point score until the new system has been backtested.

---

# 15. Entry Setup Classification

Create one primary `entry_setup`.

Suggested priority:

```text
1. Pullback
2. EMA20 Support
3. EMA50 Support
4. Consolidation
5. Breakout
6. No Setup
```

Example:

```python
if recent_pullback:
    entry_setup = "Pullback"
elif ema20_nearby:
    entry_setup = "EMA20 Support"
elif ema50_nearby:
    entry_setup = "EMA50 Support"
elif volatility_contraction:
    entry_setup = "Consolidation"
else:
    entry_setup = "No Setup"
```

The exact implementation can differ, but setup detection must be deterministic and mutually understandable.

---

# 16. Entry Trigger Classification

Create a separate `entry_trigger`.

Suggested priority:

```text
1. Pullback Pickup + Weekly Breakout
2. Pullback Pickup
3. Weekly Breakout + Volume
4. Weekly Breakout
5. Monthly Breakout
6. EMA20 Bounce
7. EMA50 Reclaim
8. Contraction Breakout
9. No Trigger
```

Priority matters because one stock may satisfy several signals on the same day.

The dashboard should show the **highest-value signal**, while retaining boolean fields for analysis.

---

# 17. Entry Status

Replace the idea of `Strong Entry` as the only actionable classification with a combination of:

```text
Momentum State
+
Entry State
```

## Momentum State

Keep:

```text
Strong
Moderate
Weak
```

based on the existing score.

## Entry State

Use:

```text
Pullback
Pullback Pickup
EMA20 Bounce
EMA50 Reclaim
Weekly Breakout
Monthly Breakout
Consolidating
Extended
No Setup
```

---

# 18. Extended Detection

The system needs to explicitly identify when a stock is strong but late.

Calculate:

```text
EMA20 distance %
EMA10 distance %
ATR-normalized distance
distance from 60D high
distance from ATH
```

MVP:

```text
extended =
    close > ema20 + 1.5 × ATR14
```

Do not automatically mark such stocks as bad investments.

Instead:

```text
Momentum = Strong
Entry State = Extended
```

This is information, not a trading decision.

---

# 19. Trigger Age

Track how recently a signal occurred.

Fields:

```text
days_since_pullback
days_since_weekly_breakout
days_since_monthly_breakout
trigger_age
```

Example:

```text
Weekly Breakout
Trigger Age: Today
```

versus:

```text
Weekly Breakout
Trigger Age: 8 days
```

This is important for distinguishing a fresh setup from an already-developed move.

---

# 20. Entry Opportunity Score — Optional Phase 2

Do NOT initially merge this into the existing 100-point score.

After validation, create a separate score if useful.

Example:

```text
Entry Opportunity Score — 100

Recent Pullback              25
Fresh Trigger                25
Support Quality              15
Breakout Volume              15
Distance from EMA20          10
Distance from 60D High       10
```

This score should answer:

```text
"How attractive is the timing?"
```

while the original score answers:

```text
"How strong is the stock?"
```

Keep these conceptually separate.

---

# 21. Dashboard Design

## Main table

Add columns:

| Column | Purpose |
|---|---|
| Symbol | Stock |
| LTP | Current price |
| Momentum | Existing score |
| Momentum State | Strong/Moderate/Weak |
| Setup | Pullback/support/consolidation |
| Trigger | Current entry signal |
| Trigger Age | Freshness |
| EMA20 Dist | Position relative to EMA20 |
| 1W High | Distance/breakout |
| 1M High | Distance/breakout |
| 60D High | Distance/breakout |
| Volume Ratio | Breakout participation |
| RS | Relative strength |

Avoid showing every technical field by default. Put secondary metrics into an expandable detail view/card.

---

# 22. Suggested Summary Cards

Replace the dashboard's dependence on only:

```text
Strong Entry
Pullback
Weak
Near High
```

with something like:

```text
Strong Momentum       11
Fresh Entry Signals    4
Pullback Pickups       2
Weekly Breakouts       3
EMA20 Bounces          2
Extended               5
```

These are counts, not mutually exclusive categories unless explicitly defined.

---

# 23. Example Stock Card

```text
HDFCBANK

Momentum       82   STRONG
Setup          Pullback
Trigger        Weekly Breakout
Trigger Age    TODAY

LTP            ₹1,984
EMA20          ₹1,948     +1.85%
Prev Week High ₹1,978     BROKEN
Prev Month High₹2,025     -2.0%
60D High       ₹2,110     -6.0%

Volume         1.34×
RS vs Nifty    +4.8%
```

The important insight is:

```text
Strong stock
+
recent pullback
+
fresh breakout
+
still below major resistance
=
potentially early entry
```

---

# 24. Important Rule: Avoid Look-Ahead Bias

All previous-period levels must use only information available before the current trading session.

Correct:

```python
prev_week_high
```

must represent the completed previous week.

Incorrect:

```python
current_week_high
```

when evaluating a signal during the current week.

Likewise:

```text
previous month high
```

must not include the current month's candles.

This is critical for meaningful backtesting.

---

# 25. Data Model

Extend the momentum result object/dataframe with fields approximately like:

```python
{
    "pullback_score": float,

    "momentum_state": str,

    "prev_week_high": float,
    "prev_month_high": float,
    "high_20d": float,
    "high_60d": float,

    "atr_pct": float,
    "volume_ratio": float,

    "recent_pullback": bool,
    "days_since_pullback": int | None,

    "weekly_breakout": bool,
    "monthly_breakout": bool,
    "high20_breakout": bool,
    "high60_breakout": bool,

    "ema20_bounce": bool,
    "ema50_reclaim": bool,

    "pullback_pickup": bool,
    "contraction_breakout": bool,

    "entry_setup": str,
    "entry_trigger": str,
    "trigger_age": int | None,
    "entry_status": str
}
```

Use the project's existing naming conventions if different.

---

# 26. Implementation Structure

Prefer keeping signal logic modular.

Suggested structure:

```text
momentum/
├── indicators.py
├── momentum_score.py
├── entry_levels.py
├── entry_setups.py
├── entry_triggers.py
├── classification.py
└── dashboard_data.py
```

If the current project is simpler, do not restructure the entire application merely to match this layout.

Minimum viable separation:

```text
calculate_indicators()
calculate_momentum_score()
calculate_entry_levels()
detect_entry_setups()
detect_entry_triggers()
classify_momentum()
build_dashboard_rows()
```

---

# 27. Phase 1 — MVP

Implement only:

### Data

- Previous week high
- Previous month high
- 20D high
- 60D high
- Volume ratio

### Setup

- Recent EMA20 pullback

### Triggers

- Pullback Pickup
- Weekly Breakout
- Monthly Breakout
- EMA20 Bounce
- EMA50 Reclaim

### Metadata

- Trigger age
- Distance from EMA20
- Distance from 60D high
- Distance from ATH

### UI

- Momentum State
- Entry Setup
- Entry Trigger
- Trigger Age

Do not add a new composite score yet.

---

# 28. Phase 2 — Validation and Refinement

Backtest the signals against the existing holdings universe.

For each trigger measure:

```text
5-day forward return
10-day forward return
20-day forward return
Maximum favorable excursion
Maximum adverse excursion
Win rate
Average return
Median return
```

Also compare:

```text
Signal triggered
vs
No signal
```

Break results down by:

```text
Momentum Score bucket
<45
45–64
65–79
>=80
```

This will reveal whether a signal works primarily in already-strong stocks.

---

# 29. Phase 3 — Improve Entry Quality

After enough observations, test:

- breakout volume thresholds,
- ATR distance thresholds,
- pullback lookback period,
- EMA20 vs EMA50 support,
- volatility contraction,
- breakout/retest,
- swing-high/swing-low structure,
- anchored VWAP,
- relative strength persistence.

Do not add indicators simply because they are technically interesting.

Each addition should improve a measurable outcome.

---

# 30. Phase 4 — Entry Ranking

Eventually the dashboard can rank opportunities like:

```text
1. Strong Momentum + Pullback Pickup
2. Strong Momentum + Weekly Breakout
3. Strong Momentum + EMA20 Bounce
4. Moderate Momentum + Weekly Breakout
5. Strong Momentum + Consolidation
6. Strong Momentum + Extended
```

This becomes the beginning of a genuine **Momentum Decision System** rather than a static technical screener.

---

# 31. Acceptance Criteria

The implementation is complete when:

- [ ] Existing 100-point score produces the same results as before.
- [ ] Existing score groups remain available for comparison.
- [ ] Previous-week high is calculated without look-ahead.
- [ ] Previous-month high is calculated without look-ahead.
- [ ] Recent EMA20 pullbacks are detected.
- [ ] Pullback Pickup signals are detected.
- [ ] Weekly breakouts are detected only when a fresh cross occurs.
- [ ] Monthly breakouts are detected only when a fresh cross occurs.
- [ ] EMA20 Bounce is detected.
- [ ] EMA50 Reclaim is detected.
- [ ] Volume ratio is available.
- [ ] Trigger age is available.
- [ ] Strong-but-extended stocks are distinguishable from fresh setups.
- [ ] Multiple simultaneous triggers have deterministic priority.
- [ ] Missing/insufficient historical data does not crash the dashboard.
- [ ] No current-period high/low information leaks into historical signals.
- [ ] Existing dashboard functionality remains intact.
- [ ] New signals can be independently backtested.

---

# 32. Codex Implementation Instruction

Implement this as an **incremental upgrade**, not a rewrite.

### Preserve

- Existing momentum calculations.
- Existing 100-point score.
- Existing benchmark/RS calculation.
- Existing holdings universe.
- Existing live LTP handling.
- Existing dashboard functionality.

### Add

```text
Entry Levels
Entry Setups
Entry Triggers
Trigger Age
Entry Status
```

### Do not

- Replace the existing score with a new score.
- Arbitrarily change existing thresholds.
- Add many indicators before testing the MVP.
- Use current-week/current-month highs for historical breakout signals.
- Treat every breakout as a buy recommendation.
- Remove the existing classifications before comparing old vs new behavior.

### Desired final behavior

The dashboard should answer four questions quickly:

```text
1. Is this stock strong?
2. Is it currently forming an attractive setup?
3. Has the setup triggered?
4. Is the trigger still early or already extended?
```

The desired output is therefore:

```text
MOMENTUM
Strong

SETUP
Recent EMA20 Pullback

TRIGGER
Weekly High Breakout

FRESHNESS
Today

POSITION
+2.0% above EMA20
-6.0% below 60D High
```

This is the intended evolution from a **momentum scoring dashboard** into a **momentum entry-timing system**.
