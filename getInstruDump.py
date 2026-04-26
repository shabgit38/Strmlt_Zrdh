import json
import math
from pathlib import Path
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
import numpy as np
import streamlit as st

from kite_auth import clear_auth_state, get_secret_value, is_token_error


SUPABASE_TABLE_DEFAULT = "kite_instruments"
SUPABASE_BATCH_SIZE = 500
REQUIRED_INSTRUMENT_COLUMNS = {"instrument_token", "tradingsymbol"}
EQUITY_INSTRUMENT_TYPE = "EQ"


REQUIRED_INSTRUMENT_COLUMNS = {"instrument_token", "tradingsymbol"}

def find_instruments_file_from_upload():
    """
    Allows user to upload CSV/Excel and validates required schema.
    Returns: pandas DataFrame
    """

    uploaded_file = st.file_uploader(
        "Upload Instruments File (CSV or Excel)",
        type=["csv", "xlsx"]
    )

    if uploaded_file is None:
        st.info("Please upload a file to proceed.")
        st.stop()

    # Detect file type and read
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Failed to read file: {e}")
        st.stop()

    # Validate required columns
    columns = set(df.columns)
    if not REQUIRED_INSTRUMENT_COLUMNS.issubset(columns):
        st.error(
            f"Missing required columns: {REQUIRED_INSTRUMENT_COLUMNS - columns}"
        )
        st.stop()
    
    if "instrument_type" not in df.columns:
        raise ValueError("Instrument dump is missing required column: instrument_type")

    equity_df = df[df["instrument_type"].astype(str).str.strip().eq(EQUITY_INSTRUMENT_TYPE)].copy()
    equity_df = equity_df.replace([pd.NA, float("inf"), float("-inf")], None)

    equity_df["name"] = equity_df["name"].where(
    pd.notnull(equity_df["name"]),None
    )
    
    st.success(f"File loaded successfully with {len(equity_df):,} rows.")
    return equity_df


#@st.cache_data(ttl=24 * 60 * 60)
def fetch_instruments_dump(api_key: str, access_token: str) -> pd.DataFrame:
    """Fetch the daily instrument dump from the local CSV and cache it for one day."""
    # Keep the Kite Connect wiring available for future re-enable.
    # client = KiteConnect(api_key=api_key)
    # client.set_access_token(access_token)
    # return pd.DataFrame(client.instruments())

    #return pd.read_csv(csv_path)
    


def _json_safe_value(value: Any) -> Any:
    """Convert pandas/numpy values into JSON-safe primitives for Supabase."""
    if pd.isna(value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _scan_dataframe_for_bad_values(df: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    for row_index, row in df.iterrows():
        for column_name, value in row.items():
            if pd.isna(value):
                continue
            if isinstance(value, float) and not math.isfinite(value):
                issues.append(f"row={row_index}, column={column_name}, value={value!r}")
            elif isinstance(value, complex):
                issues.append(f"row={row_index}, column={column_name}, value={value!r}")
            elif hasattr(value, "item"):
                unwrapped = value.item()
                if isinstance(unwrapped, float) and not math.isfinite(unwrapped):
                    issues.append(f"row={row_index}, column={column_name}, value={unwrapped!r}")
    return issues


def _filter_equity_rows(df: pd.DataFrame) -> pd.DataFrame:
    if "instrument_type" not in df.columns:
        raise ValueError("Instrument dump is missing required column: instrument_type")

    equity_df = df[df["instrument_type"].astype(str).str.strip().eq(EQUITY_INSTRUMENT_TYPE)].copy()
    return equity_df


def _records_from_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    normalized = df.copy().astype(object)
    for column in normalized.columns:
        normalized[column] = normalized[column].map(_json_safe_value)
    return normalized.to_dict(orient="records")


def _find_json_serialization_issues(records: list[tuple[Any, dict[str, Any]]]) -> list[str]:
    issues: list[str] = []
    for row_index, record in records:
        for column_name, value in record.items():
            if isinstance(value, float) and not math.isfinite(value):
                issues.append(f"row={row_index}, column={column_name}, value={value!r}")
            elif isinstance(value, complex):
                issues.append(f"row={row_index}, column={column_name}, value={value!r}")
            elif isinstance(value, (list, dict, set, tuple)):
                issues.append(f"row={row_index}, column={column_name}, value_type={type(value).__name__}")
    return issues


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

    #equity_df = _filter_equity_rows(df)
    if df.empty:
        st.warning("No EQ rows found in the CSV. Nothing will be uploaded to Supabase.")
        return

    normalized_df = df.copy().astype(object)
    for column in normalized_df.columns:
        normalized_df[column] = normalized_df[column].map(_json_safe_value)

    issues = _scan_dataframe_for_bad_values(normalized_df)
    if issues:
        message = "Found non-JSON-safe values in the CSV: " + "; ".join(issues[:20])
        print(message)
        st.error(message)

    records = normalized_df.to_dict(orient="records")
    if not records:
        return

    endpoint = f"{supabase_url}/rest/v1/{table_name}?on_conflict=instrument_token"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    indexed_records = list(zip(normalized_df.index.tolist(), records))

    for chunk in _chunk_records(indexed_records, SUPABASE_BATCH_SIZE):
        chunk_rows = [record for _, record in chunk]
        try:
            payload = json.dumps(chunk_rows, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError) as exc:
            issues = _find_json_serialization_issues(chunk)
            details = "; ".join(issues[:20]) if issues else "no specific cell identified"
            print(
                f"Supabase payload contains non-JSON-safe values in chunk starting at row {chunk[0][0]}: {details}"
            )
            raise RuntimeError(
                f"Supabase payload contains non-JSON-safe values in chunk starting at row {chunk[0][0]}: {details}"
            ) from exc
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


# Kite authentication/bootstrap is kept commented out because the upload flow
# now reads from the local CSV and syncs that data to Supabase.
# _, API_KEY, _ = bootstrap_kite_app("Instrument Dump")

st.caption("Daily instrument dump from the local CSV. It is useful for lookup and database import.")

try:
    #UNCOMMENT the below line to fetch directly from Kite Connect API instead of local CSV upload.
    #instruments_df = fetch_instruments_dump("", "")
    #equity_instruments_df = _filter_equity_rows(instruments_df)
    #st.success(f"Loaded {len(instruments_df):,} instruments from the local CSV.")
    #st.info(f"Found {len(equity_instruments_df):,} EQ rows to upload to Supabase.")
    #st.download_button(
    #    "Download full CSV",
    #    data=instruments_df.to_csv(index=False),
    #    file_name=f"kite_instruments_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
    #    mime="text/csv",
    #)  



    # For - loading from the local CSV upload. 
    # Future enhancement could include a toggle to choose between direct API fetch vs local CSV upload.
    instruments_df = find_instruments_file_from_upload()
    print(instruments_df.columns)
    print(instruments_df.head(5))

    try:
        upsert_instruments_to_supabase(instruments_df)
        st.success("Instrument dump synced to Supabase.")
    except Exception as supabase_exc:
        st.warning(f"Loaded instruments, but Supabase sync was skipped or failed: {supabase_exc}")

    
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
