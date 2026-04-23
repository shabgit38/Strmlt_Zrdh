import json
import math
from pathlib import Path
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect

from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error


SUPABASE_TABLE_DEFAULT = "kite_instruments"
SUPABASE_BATCH_SIZE = 500
REQUIRED_INSTRUMENT_COLUMNS = {"instrument_token", "tradingsymbol"}


def _find_instruments_csv(project_dir: Path) -> Path:
    csv_candidates = sorted(project_dir.glob("*.csv"))
    if not csv_candidates:
        raise FileNotFoundError(f"No CSV files found in project folder: {project_dir}")

    matching_candidates: list[Path] = []
    for csv_path in csv_candidates:
        try:
            columns = set(pd.read_csv(csv_path, nrows=0).columns)
        except Exception:
            continue
        if REQUIRED_INSTRUMENT_COLUMNS.issubset(columns):
            matching_candidates.append(csv_path)

    if not matching_candidates:
        raise FileNotFoundError(
            f"No CSV in {project_dir} contains required columns: {sorted(REQUIRED_INSTRUMENT_COLUMNS)}"
        )
    if len(matching_candidates) > 1:
        raise RuntimeError(
            "Multiple CSV files in the project folder match the instrument schema: "
            + ", ".join(path.name for path in matching_candidates)
        )

    return matching_candidates[0]


@st.cache_data(ttl=24 * 60 * 60)
def fetch_instruments_dump(api_key: str, access_token: str) -> pd.DataFrame:
    """Fetch the daily instrument dump from the local CSV and cache it for one day."""
    # Keep the Kite Connect wiring available for future re-enable.
    # client = KiteConnect(api_key=api_key)
    # client.set_access_token(access_token)
    # return pd.DataFrame(client.instruments())

    csv_path = _find_instruments_csv(Path(__file__).resolve().parent)
    return pd.read_csv(csv_path)

def _json_safe_value(value: Any) -> Any:
    """Convert pandas/numpy values into JSON-safe primitives for Supabase."""
    if pd.isna(value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _records_from_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    normalized = df.copy()
    for column in normalized.columns:
        normalized[column] = normalized[column].map(_json_safe_value)
    return normalized.to_dict(orient="records")


def _chunk_records(records: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [records[i : i + chunk_size] for i in range(0, len(records), chunk_size)]


def upsert_instruments_to_supabase(df: pd.DataFrame) -> None:
    """
    Write the full instrument dump to Supabase using an upsert keyed by instrument_token.
    """
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip() or SUPABASE_TABLE_DEFAULT

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    if "instrument_token" not in df.columns:
        raise ValueError("Instrument dump is missing required column: instrument_token")

    records = _records_from_dataframe(df)
    if not records:
        return

    endpoint = f"{supabase_url}/rest/v1/{table_name}?on_conflict=instrument_token"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    for chunk in _chunk_records(records, SUPABASE_BATCH_SIZE):
        payload = json.dumps(chunk).encode("utf-8")
        if not payload or payload == b"[]":
            continue
        request = Request(endpoint, data=payload, headers=headers, method="POST")

        try:
            with urlopen(request, timeout=60) as response:
                response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Supabase write failed with HTTP {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"Supabase write failed: {exc.reason}") from exc


_, API_KEY, _ = bootstrap_kite_app("Instrument Dump")

st.caption("Daily instrument dump from Kite. It is useful for lookup and database import.")

try:
    instruments_df = fetch_instruments_dump(API_KEY, st.session_state.access_token)
    st.success(f"Loaded {len(instruments_df):,} instruments from Kite.")
    print(instruments_df.columns)
    print(instruments_df.head(5))

    try:
        upsert_instruments_to_supabase(instruments_df)
        st.success("Instrument dump synced to Supabase.")
    except Exception as supabase_exc:
        st.warning(f"Loaded instruments, but Supabase sync was skipped or failed: {supabase_exc}")

    st.download_button(
        "Download full CSV",
        data=instruments_df.to_csv(index=False),
        file_name=f"kite_instruments_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
    )
except Exception as exc:
    if is_token_error(exc):
        clear_auth_state()
        st.error("Your session expired. Please login again to load instruments.")
        st.rerun()
    st.error("Error loading instrument list. Please try again.")


if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        clear_auth_state()
        st.rerun()
