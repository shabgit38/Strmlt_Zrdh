import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# EMA CALCULATION
# Exponential Moving Averages for 10, 20, 50, 100, 200 periods.
# With 2Y of daily data (~500 bars) EMA200 is fully warmed up.
# adjust=False matches the standard recursive EMA used by most charting tools.
# ─────────────────────────────────────────────────────────────────────────────

def add_ema(df):
    for span in [5, 10, 20, 50, 100, 200]:
        df[f"EMA{span}"] = df["Close"].ewm(span=span, adjust=False).mean()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# DAILY PIVOT POINTS
# Classical floor pivot formula using the PREVIOUS completed session's H/L/C.
# We use iloc[-2] (not iloc[-1]) because iloc[-1] is today's live candle —
# its High/Low changes until market close, making levels unstable intraday.
# The prior closed bar gives the stable reference floor traders actually use.
# ─────────────────────────────────────────────────────────────────────────────

def pivot_points(df) -> dict:
    """
    Returns Pivot + S1-S5 + R1-R5 anchored to yesterday's confirmed bar.
    """
    last = df.iloc[-2]                      # ← previous completed session
    H, L, C = float(last["High"]), float(last["Low"]), float(last["Close"])
    rng = H - L

    P  = (H + L + C) / 3
    R1 = 2 * P - L
    S1 = 2 * P - H
    R2 = P + rng
    S2 = P - rng
    R3 = H + 2 * (P - L)
    S3 = L - 2 * (H - P)
    R4 = R3 + rng
    S4 = S3 - rng
    R5 = R4 + rng
    S5 = S4 - rng

    return {
        "Pivot": P,
        "R1": R1, "R2": R2, "R3": R3, "R4": R4, "R5": R5,
        "S1": S1, "S2": S2, "S3": S3, "S4": S4, "S5": S5,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY PIVOT POINTS
# Classical floor pivot formula applied to the last *completed* Mon–Fri week.
# Weekly pivots act as stronger support/resistance than daily pivots because
# they represent a full week of price discovery.
# The input `weekly_ohlc` is the dict returned by data_fetch.get_weekly_ohlc().
# ─────────────────────────────────────────────────────────────────────────────

def weekly_pivot_points(weekly_ohlc: dict) -> dict | None:
    """
    Compute classical floor pivot points from the last completed week's
    High, Low, Close.

    Formula (identical to daily pivots, different reference bar):
        P  = (H + L + C) / 3
        R1 = 2P - L       S1 = 2P - H
        R2 = P + (H - L)  S2 = P - (H - L)
        R3 = H + 2(P - L) S3 = L - 2(H - P)
        R4 = R3 + (H - L) S4 = S3 - (H - L)
        R5 = R4 + (H - L) S5 = S4 - (H - L)

    Returns None if weekly_ohlc is None (insufficient data).
    """
    if weekly_ohlc is None:
        return None

    H   = weekly_ohlc["High"]
    L   = weekly_ohlc["Low"]
    C   = weekly_ohlc["Close"]
    rng = H - L                             # weekly range

    P  = (H + L + C) / 3
    R1 = 2 * P - L
    S1 = 2 * P - H
    R2 = P + rng
    S2 = P - rng
    R3 = H + 2 * (P - L)
    S3 = L - 2 * (H - P)
    R4 = R3 + rng
    S4 = S3 - rng
    R5 = R4 + rng
    S5 = S4 - rng

    return {
        "Pivot": P,
        "R1": R1, "R2": R2, "R3": R3, "R4": R4, "R5": R5,
        "S1": S1, "S2": S2, "S3": S3, "S4": S4, "S5": S5,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PIVOT ZONE SCORE  (shared helper)
# Given a price and a pivot dict, returns a score based on which zone the
# price sits in.  Used for both daily (weight ±4) and weekly (weight ±2).
# ─────────────────────────────────────────────────────────────────────────────

def _pivot_zone_score(price: float, pivots: dict, max_score: float) -> float:
    """
    Map price to one of 8 pivot zones and return a score scaled to max_score.

    Zones (descending):  above R3 / R2-R3 / R1-R2 / Pivot-R1
                         S1-Pivot / S2-S1 / S3-S2 / below S3

    The full ±4 scale is used for daily pivots (max_score=4).
    Weekly pivots pass max_score=2 to halve the weight.
    """
    P  = pivots["Pivot"]
    R1, R2, R3 = pivots["R1"], pivots["R2"], pivots["R3"]
    S1, S2, S3 = pivots["S1"], pivots["S2"], pivots["S3"]

    if   price > R3:  zone_frac =  1.00   # strongest bull zone
    elif price > R2:  zone_frac =  0.75
    elif price > R1:  zone_frac =  0.50
    elif price > P:   zone_frac =  0.25
    elif price > S1:  zone_frac = -0.25
    elif price > S2:  zone_frac = -0.50
    elif price > S3:  zone_frac = -0.75
    else:             zone_frac = -1.00   # strongest bear zone

    return round(zone_frac * max_score, 2)


# ─────────────────────────────────────────────────────────────────────────────
# RANGE POSITION SCORE  (shared helper)
# Measures where the current price sits within a period H/L range as a %.
# A price near the period high scores positively; near the low, negatively.
# ─────────────────────────────────────────────────────────────────────────────

def _range_pct_score(price: float, high: float, low: float, max_score: float) -> float:
    """
    Score = (price - low) / (high - low), mapped to ±max_score.

    Buckets:
        > 75% of range  →  +max_score        (price near period high)
        60–75%          →  +max_score * 0.5
        40–60%          →   0                (mid-range, neutral)
        25–40%          →  -max_score * 0.5
        < 25%           →  -max_score        (price near period low)

    Returns 0 if high == low (flat range, no information).
    """
    rng = high - low
    if rng == 0:
        return 0.0

    pct = (price - low) / rng               # 0.0 = at period low, 1.0 = at period high

    if   pct > 0.75:  frac =  1.0
    elif pct > 0.60:  frac =  0.5
    elif pct > 0.40:  frac =  0.0
    elif pct > 0.25:  frac = -0.5
    else:             frac = -1.0

    return round(frac * max_score, 2)


# ─────────────────────────────────────────────────────────────────────────────
# MOMENTUM SCORE
# Combines 7 components into a single score in the range −20 to +20.
#
# Component | What it measures                        | Range
# ----------|-----------------------------------------|--------
# C1        | Price vs each EMA (5 EMAs × ±1)         | −5  to +5
# C2        | EMA stack order  (4 pairs × ±0.75)      | −3  to +3
# C3        | Daily pivot zone                         | −4  to +4
# C4        | Weekly pivot zone (half weight of daily) | −2  to +2
# C5        | 6M H/L range position                   | −3  to +3
# C6        | 3M H/L range position                   | −2  to +2
# C7        | 1M H/L range position                   | −1  to +1
#
# Returns a dict with the total score and each component broken out so the
# UI can show a detailed breakdown.
# ─────────────────────────────────────────────────────────────────────────────

def momentum_score(
    df,
    current_price: float,
    daily_pivots: dict,
    weekly_pivots: dict | None,
    hl: dict,
) -> dict:
    """
    Compute the composite momentum score and return a breakdown dict.

    Parameters
    ----------
    df             : DataFrame with EMA columns already added (via add_ema)
    current_price  : latest Close price
    daily_pivots   : output of pivot_points(df)
    weekly_pivots  : output of weekly_pivot_points(...), may be None
    hl             : output of get_high_low_resampled(df)
    """
    last = df.iloc[-1]

    # ── C1: Price vs each EMA ─────────────────────────────────────────────────
    # +1 for each EMA the price is above, −1 for each below.
    # Max +5 when price > all EMAs (strong bull), min −5 when below all.
    c1 = 0.0
    ema_positions = {}
    for span in [10, 20, 50, 100, 200]:
        col = f"EMA{span}"
        ema_val = float(last[col])
        delta = 1.0 if current_price > ema_val else -1.0
        c1 += delta
        ema_positions[col] = (round(ema_val, 2), delta)

    # ── C2: EMA stack order ───────────────────────────────────────────────────
    # A perfect bull stack is EMA10 > EMA20 > EMA50 > EMA100 > EMA200.
    # Each consecutive pair in correct order scores +0.75; inverted −0.75.
    c2 = 0.0
    pairs = [(10, 20), (20, 50), (50, 100), (100, 200)]
    stack_detail = {}
    for fast, slow in pairs:
        fast_val = float(last[f"EMA{fast}"])
        slow_val = float(last[f"EMA{slow}"])
        aligned  = fast_val > slow_val
        c2 += 0.75 if aligned else -0.75
        stack_detail[f"EMA{fast}>EMA{slow}"] = aligned

    # ── C3: Daily pivot zone ──────────────────────────────────────────────────
    c3 = _pivot_zone_score(current_price, daily_pivots, max_score=4.0)

    # ── C4: Weekly pivot zone ─────────────────────────────────────────────────
    # If weekly pivot data is unavailable, this component contributes 0.
    weekly_missing = weekly_pivots is None
    c4 = _pivot_zone_score(current_price, weekly_pivots, max_score=2.0) \
         if weekly_pivots else 0.0

    # ── C5: 6M range position ─────────────────────────────────────────────────
    h6m, l6m = hl["6M"]
    c5 = _range_pct_score(current_price, h6m, l6m, max_score=3.0)

    # ── C6: 3M range position ─────────────────────────────────────────────────
    h3m, l3m = hl["3M"]
    c6 = _range_pct_score(current_price, h3m, l3m, max_score=2.0)

    # ── C7: 1M range position ─────────────────────────────────────────────────
    h1m, l1m = hl["1M"]
    c7 = _range_pct_score(current_price, h1m, l1m, max_score=1.0)

    total = round(c1 + c2 + c3 + c4 + c5 + c6 + c7, 2)

    return {
        "total":           total,
        "c1_ema_pos":      round(c1, 2),
        "c2_ema_stack":    round(c2, 2),
        "c3_daily_pivot":  c3,
        "c4_weekly_pivot": c4,
        "c5_6m_range":     c5,
        "c6_3m_range":     c6,
        "c7_1m_range":     c7,
        "ema_positions":   ema_positions,   # {EMAxx: (value, ±1)}
        "stack_detail":    stack_detail,    # {EMAx>EMAy: bool}
        "weekly_pivots_missing": weekly_missing,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MOMENTUM LABEL
# Converts a numeric score to a human-readable label and a colour string.
#
# Score range   | Label        | Colour
# ─────────────────────────────────────
#  > 12         | Strong Bull  | green
#  4 to 12      | Bull         | light green
# −4 to  4      | Neutral      | grey
# −12 to −4     | Bear         | orange
#  < −12        | Strong Bear  | red
# ─────────────────────────────────────────────────────────────────────────────

def momentum_label(score: float) -> tuple[str, str]:
    """
    Return (label, hex_colour) for the given momentum score.
    The hex colour is used by app.py to render a coloured badge.
    """
    if   score >  12:  return "Strong Bull",  "#28a745"   # green
    elif score >   4:  return "Bull",          "#5cb85c"   # light green
    elif score >= -4:  return "Neutral",       "#888888"   # grey
    elif score >= -12: return "Bear",          "#e67e22"   # orange
    else:              return "Strong Bear",   "#dc3545"   # red


# ------------------------------------------------------------------------------
# MOMENTUM SUMMARY (human-readable explanation for UI)
# ------------------------------------------------------------------------------

def _pivot_zone_text(score: float, max_score: float) -> str:
    """
    Convert a pivot zone score back into a readable zone description.
    """
    if max_score == 0:
        return "pivot zone: unavailable"
    frac = round(score / max_score, 2)
    if   frac >=  1.00: return "above R3 (strongest bull zone)"
    elif frac >=  0.75: return "between R2 and R3"
    elif frac >=  0.50: return "between R1 and R2"
    elif frac >=  0.25: return "between Pivot and R1"
    elif frac <= -1.00: return "below S3 (strongest bear zone)"
    elif frac <= -0.75: return "between S3 and S2"
    elif frac <= -0.50: return "between S2 and S1"
    elif frac <= -0.25: return "between S1 and Pivot"
    return "near Pivot (neutral zone)"


def _range_pos_text(score: float, max_score: float) -> str:
    """
    Convert a range position score back into a readable location in the range.
    """
    if max_score == 0:
        return "range position: unavailable"
    if score >= max_score:       return "near the period high"
    if score <= -max_score:      return "near the period low"
    if score == 0:               return "mid-range"
    if score > 0:                return "upper half of the range"
    return "lower half of the range"


def momentum_summary(score_dict: dict, label: str) -> str:
    """
    Build a multi-line explanation of what each momentum component implies.
    Returns markdown-friendly text.
    """
    total = float(score_dict["total"])

    # C1: EMA position
    ema_positions = score_dict.get("ema_positions", {})
    above = [k for k, (_, d) in ema_positions.items() if d > 0]
    below = [k for k, (_, d) in ema_positions.items() if d < 0]
    c1_text = (
        f"C1 EMA position: {len(above)} above / {len(below)} below "
        "the 5 EMAs (price vs EMA10/20/50/100/200)."
    )

    # C2: EMA stack order
    stack_detail = score_dict.get("stack_detail", {})
    aligned = [k for k, v in stack_detail.items() if v]
    c2_text = (
        f"C2 EMA stack: {len(aligned)} of 4 fast>slow pairs aligned "
        "(EMA10>20>50>100>200)."
    )

    # C3/C4: Pivot zones
    c3 = float(score_dict["c3_daily_pivot"])
    c4 = float(score_dict["c4_weekly_pivot"])
    c3_text = f"C3 Daily pivots: {_pivot_zone_text(c3, 4.0)}."
    if score_dict.get("weekly_pivots_missing", False):
        c4_text = "C4 Weekly pivots: unavailable (insufficient data)."
    else:
        c4_text = f"C4 Weekly pivots: {_pivot_zone_text(c4, 2.0)}."

    # C5/C6/C7: Range positions
    c5 = float(score_dict["c5_6m_range"])
    c6 = float(score_dict["c6_3m_range"])
    c7 = float(score_dict["c7_1m_range"])
    c5_text = f"C5 6M range: {_range_pos_text(c5, 3.0)}."
    c6_text = f"C6 3M range: {_range_pos_text(c6, 2.0)}."
    c7_text = f"C7 1M range: {_range_pos_text(c7, 1.0)}."

   

    return (
        
        f"{c1_text}  \n"
        f"{c2_text}  \n"
        f"{c3_text}  \n"
        f"{c4_text}  \n"
        f"{c5_text}  \n"
        f"{c6_text}  \n"
        f"{c7_text}"
    )
