from __future__ import annotations

import json
import math
import os
import tomllib
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from kiteconnect import KiteConnect


class KiteConfigError(RuntimeError):
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GIT_CLONE_ROOT = PROJECT_ROOT.parent
STRMLT_ZRDH_ROOT = Path(os.getenv("STRMLT_ZRDH_PATH", GIT_CLONE_ROOT / "Strmlt_Zrdh"))
HOLDINGS_TABLE_NAME = "holdings_breakdown"
KITE_SESSION_PATH = PROJECT_ROOT / "backend" / ".kite_session.json"
KITE_SESSION_TZ = ZoneInfo("Asia/Kolkata")


def _load_key_value_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_streamlit_secrets(path: Path) -> None:
    if not path.exists():
        return
    with path.open("rb") as secrets_file:
        secrets = tomllib.load(secrets_file)
    for key, value in secrets.items():
        if isinstance(value, (str, int, float, bool)) and key not in os.environ:
            os.environ[key] = str(value)


def _load_local_config() -> None:
    _load_key_value_env(PROJECT_ROOT / ".env")
    _load_key_value_env(PROJECT_ROOT / "backend" / ".env")
    _load_streamlit_secrets(STRMLT_ZRDH_ROOT / ".streamlit" / "secrets.toml")


def _secret(name: str) -> str:
    _load_local_config()
    return os.getenv(name, "").strip()


def _next_kite_session_expiry(now: datetime | None = None) -> datetime:
    current = now or datetime.now(KITE_SESSION_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KITE_SESSION_TZ)
    today_expiry = datetime.combine(current.date(), time(6, 0), tzinfo=KITE_SESSION_TZ)
    if current < today_expiry:
        return today_expiry
    return today_expiry + timedelta(days=1)


def _read_kite_session_token() -> str:
    if not KITE_SESSION_PATH.exists():
        return ""
    try:
        session = json.loads(KITE_SESSION_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    access_token = str(session.get("access_token") or "").strip()
    expires_at = str(session.get("expires_at") or "").strip()
    if not access_token or not expires_at:
        return ""

    try:
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        return ""
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=KITE_SESSION_TZ)

    return access_token if datetime.now(KITE_SESSION_TZ) < expires else ""


def kite_login_url() -> str:
    api_key = _secret("ZERODHA_API_KEY")
    if not api_key:
        raise KiteConfigError("Missing ZERODHA_API_KEY for Kite login.")
    return KiteConnect(api_key=api_key).login_url()


def complete_kite_login(request_token: str) -> dict[str, str]:
    api_key = _secret("ZERODHA_API_KEY")
    api_secret = _secret("ZERODHA_API_SECRET")
    if not api_key:
        raise KiteConfigError("Missing ZERODHA_API_KEY for Kite login.")
    if not api_secret:
        raise KiteConfigError("Missing ZERODHA_API_SECRET for Kite token exchange.")
    if not request_token:
        raise KiteConfigError("Missing request_token from Kite login callback.")

    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token, api_secret=api_secret)
    access_token = str(session.get("access_token") or "").strip()
    if not access_token:
        raise KiteConfigError("Kite login succeeded but no access token was returned.")

    now = datetime.now(KITE_SESSION_TZ)
    expires_at = _next_kite_session_expiry(now)
    KITE_SESSION_PATH.write_text(
        json.dumps(
            {
                "access_token": access_token,
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "user_id": session.get("user_id"),
                "login_time": session.get("login_time"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"expires_at": expires_at.isoformat()}


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return value


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        converted = float(value)
        return converted if math.isfinite(converted) else default
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    return int(_float(value, default))


def _pct(numerator: float, denominator: float) -> float:
    return numerator / denominator * 100 if denominator else 0.0


def _symbol(value: Any) -> str:
    return str(value or "").upper().strip()


def _ltp_match_symbol(value: Any) -> str:
    normalized = _symbol(value)
    for suffix in ("-RR", "-IV"):
        if normalized.endswith(suffix):
            return normalized.removesuffix(suffix)
    return normalized


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).split("T", 1)[0]).date()
    except ValueError:
        return None


def _holding_age(value: Any) -> str:
    trade_date = _parse_date(value)
    if trade_date is None:
        return "-"

    today = date.today()
    if trade_date > today:
        return "0 Years, 0 Months, 0 Days"

    years = today.year - trade_date.year
    months = today.month - trade_date.month
    days = today.day - trade_date.day

    if days < 0:
        months -= 1
        previous_month = today.month - 1 or 12
        previous_year = today.year if today.month > 1 else today.year - 1
        if previous_month == 2:
            leap = previous_year % 4 == 0 and (previous_year % 100 != 0 or previous_year % 400 == 0)
            days += 29 if leap else 28
        elif previous_month in {4, 6, 9, 11}:
            days += 30
        else:
            days += 31
    if months < 0:
        years -= 1
        months += 12
    return f"{years} Years, {months} Months, {days} Days"


def _supabase_headers(key: str) -> dict[str, str]:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _load_holdings_breakdown() -> list[dict[str, Any]]:
    supabase_url = _secret("SUPABASE_URL").rstrip("/")
    supabase_key = _secret("SUPABASE_SERVICE_ROLE_KEY")
    table_name = _secret("SUPABASE_HOLDINGS_TABLE_NAME") or HOLDINGS_TABLE_NAME
    if not supabase_url or not supabase_key:
        return []

    endpoint = f"{supabase_url}/rest/v1/{quote(table_name, safe='')}?select=*&order=id.asc"
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Supabase holdings breakdown lookup failed: {exc}") from exc
    return records if isinstance(records, list) else []


def _sector_by_symbol(records: list[dict[str, Any]]) -> dict[str, str]:
    sectors: dict[str, str] = {}
    for record in records:
        row_type = _symbol(record.get("row_type"))
        if row_type and row_type != "SUMMARY":
            continue
        symbol = _symbol(record.get("symbol"))
        sector = str(record.get("sector") or "").strip()
        if symbol and sector:
            sectors[symbol] = sector

    fallback = {
        _ltp_match_symbol(symbol): sector
        for symbol, sector in sectors.items()
        if _ltp_match_symbol(symbol) not in sectors
    }
    return {**fallback, **sectors}


def _batch_rows_by_symbol(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    batches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if _symbol(record.get("row_type")) != "BATCH":
            continue
        if _symbol(record.get("holding_status")) == "EXITED":
            continue
        symbol = _symbol(record.get("symbol"))
        if symbol:
            batches[symbol].append(record)
    for rows in batches.values():
        rows.sort(key=lambda row: _float(row.get("id")))
    return batches


def _build_batches(
    symbol: str,
    ltp: float,
    records_by_symbol: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = records_by_symbol.get(symbol) or records_by_symbol.get(_ltp_match_symbol(symbol)) or []
    batches: list[dict[str, Any]] = []
    for row in rows:
        price = _float(row.get("batch_price"))
        qty = _int(row.get("batch_qty"))
        profit_pct = _pct(ltp - price, price)
        batches.append(
            {
                "price": price,
                "qty": qty,
                "age": str(row.get("present_age") or _holding_age(row.get("trade_date"))),
                "profitPct": profit_pct,
            }
        )
    return batches


def _kite_client() -> KiteConnect:
    api_key = _secret("ZERODHA_API_KEY")
    access_token = _read_kite_session_token() or _secret("ZERODHA_ACCESS_TOKEN")
    if not api_key:
        raise KiteConfigError("Missing ZERODHA_API_KEY for Kite live holdings.")
    if not access_token:
        raise KiteConfigError(
            "Kite authentication required. Open /api/auth/kite/login to sign in and create today's session."
        )

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def build_live_portfolio_snapshot() -> dict[str, Any]:
    kite = _kite_client()
    holdings = kite.holdings()
    breakdown_records = _load_holdings_breakdown()
    sector_by_symbol = _sector_by_symbol(breakdown_records)
    batches_by_symbol = _batch_rows_by_symbol(breakdown_records)

    total_invested = 0.0
    total_current = 0.0
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mtf_holdings: list[dict[str, Any]] = []

    for holding in holdings:
        symbol = _symbol(holding.get("tradingsymbol"))
        mtf = holding.get("mtf") if isinstance(holding.get("mtf"), dict) else {}
        mtf_quantity = _int(mtf.get("quantity"))
        if mtf_quantity > 0:
            mtf_holdings.append(
                {
                    "symbol": symbol,
                    "mtfQty": mtf_quantity,
                    "mtfAvgPrice": _float(mtf.get("average_price")),
                    "mtfValue": _float(mtf.get("value")),
                    "ltp": _float(holding.get("last_price")),
                    "pnl": _float(holding.get("pnl")),
                    "dayChangePct": _float(holding.get("day_change_percentage")),
                }
            )
            continue

        quantity = _int(holding.get("quantity"))
        average_price = _float(holding.get("average_price"))
        ltp = _float(holding.get("last_price"))
        invested = average_price * quantity
        current = ltp * quantity
        pnl = _float(holding.get("pnl"), current - invested)
        pnl_pct = _pct(pnl, invested)
        sector = (
            sector_by_symbol.get(symbol)
            or sector_by_symbol.get(_ltp_match_symbol(symbol))
            or "Unmapped"
        )

        total_invested += invested
        total_current += current
        grouped[sector].append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "averagePrice": average_price,
                "invested": invested,
                "weightPct": 0.0,
                "current": current,
                "ltp": ltp,
                "pnl": pnl,
                "pnlPct": pnl_pct,
                "dayChangePct": _float(holding.get("day_change_percentage")),
                "batches": _build_batches(symbol, ltp, batches_by_symbol),
            }
        )

    sectors = []
    for sector, sector_holdings in grouped.items():
        sector_invested = sum(_float(holding["invested"]) for holding in sector_holdings)
        sector_current = sum(_float(holding["current"]) for holding in sector_holdings)
        sector_pnl = sum(_float(holding["pnl"]) for holding in sector_holdings)
        for holding in sector_holdings:
            holding["weightPct"] = _pct(_float(holding["invested"]), sector_invested)

        sectors.append(
            {
                "sector": sector,
                "holdingsCount": len(sector_holdings),
                "invested": sector_invested,
                "weightPct": _pct(sector_invested, total_invested),
                "current": sector_current,
                "pnl": sector_pnl,
                "pnlPct": _pct(sector_pnl, sector_invested),
                "holdings": sorted(sector_holdings, key=lambda holding: holding["symbol"]),
            }
        )

    total_pnl = total_current - total_invested
    return _json_safe(
        {
            "asOf": datetime.now().astimezone().isoformat(),
            "source": "Kite live API",
            "totals": {
                "invested": total_invested,
                "current": total_current,
                "pnl": total_pnl,
                "pnlPct": _pct(total_pnl, total_invested),
            },
            "sectors": sorted(sectors, key=lambda sector: sector["current"], reverse=True),
            "mtfHoldings": sorted(mtf_holdings, key=lambda holding: holding["symbol"]),
        }
    )
