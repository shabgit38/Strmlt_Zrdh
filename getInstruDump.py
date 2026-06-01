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
from kiteconnect import KiteConnect
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error



SUPABASE_BATCH_SIZE = 500
REQUIRED_INSTRUMENT_COLUMNS = {"instrument_token", "tradingsymbol", "name"}
EQUITY_INSTRUMENT_TYPE = "EQ"


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

    # Drop completely empty rows before any validation
    before = len(df)
    print(f"Initial row count: {before}")
    df = df.dropna(how="all")
    empty_dropped = before - len(df) # Count how many rows were dropped due to being completely empty
    print(f"Dropped {empty_dropped} completely empty row(s). Remaining rows: {len(df)}")
    

    # Validate required columns
    columns = set(df.columns)
    if not REQUIRED_INSTRUMENT_COLUMNS.issubset(columns):
        st.error(
            f"Missing required columns: {REQUIRED_INSTRUMENT_COLUMNS - columns}"
        )
        st.stop()

    # Drop rows missing the primary key — these can never be upserted
    before = len(df)
    print(f"Row count before dropping rows with missing instrument_token: {before}")
    df = df[df["instrument_token"].notna()]
    key_dropped = before - len(df)
    print(f"Dropped {key_dropped} row(s) with missing instrument_token. Remaining rows: {len(df)}")

    
    print( f"total rows with missing name: {df.isna().sum()}")
    print(df[df["name"].isna()].head())
    before = len(df)
    print(f"Row count before dropping rows with missing name: {before}")    
    df = df[df["name"].notna()]
    noname_dropped = before - len(df)
    print(f"Dropped {noname_dropped} row(s) with missing name. Remaining rows: {len(df)}")


    total_skipped = empty_dropped + key_dropped + noname_dropped
    print(f"Total skipped rows: {total_skipped}")
    if total_skipped:
        st.info(f"Skipped {total_skipped:,} row(s) with missing data ({empty_dropped:,} blank, {key_dropped:,} missing instrument_token, {noname_dropped:,} missing name).")

    # Filter to EQ instruments on NSE/BSE only
    before = len(df)
    print(f"Row count before filtering EQ instruments on NSE/BSE/INDICES: {before}")
    df = df[
        (df["instrument_type"].str.upper() == EQUITY_INSTRUMENT_TYPE) &
        (df["exchange"].str.upper().isin({"NSE", "BSE","INDICES"}))
    ]
    filtered_out = before - len(df)
    print(f"Filtered out {filtered_out} non-EQ or non-NSE/BSE/INDICES row(s). Keeping {len(df)} EQ rows.")
    if filtered_out:
        st.info(f"Filtered out {filtered_out:,} non-EQ or non-NSE/BSE row(s). Keeping {len(df):,} EQ rows.")
    
     
    ######
        normalized_df = clean_dataframe_for_supabase(df)
        print(normalized_df.head(5))
        records = normalized_df.to_dict(orient="records")
        print(f"Converted dataframe to {len(records)} record(s) for Supabase upload.")
        if not records:
            return

        # Guarantee instrument_token is a Python int in every record regardless of
        # how pandas serialised the column dtype (float64 → "500002.0" breaks bigint).
        for record in records:
            v = record.get("instrument_token")
            if v is not None:
                try:
                    record["instrument_token"] = int(float(v))
                except (TypeError, ValueError):
                    pass


        issues = _scan_dataframe_for_bad_values(normalized_df)
        print(f"Scanned dataframe for JSON serialization issues, found {len(issues)} issue(s).")
        if issues:
            message = "Found non-JSON-safe values in the CSV: " + "; ".join(issues[:20])
            print(message)
            st.error(message)
        #####


    st.success(f"File loaded successfully with {len(normalized_df):,} rows.")
    return normalized_df


def fetch_instruments_dump(kite: KiteConnect) -> pd.DataFrame:
    return pd.DataFrame(kite.instruments())


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

def clean_dataframe_for_supabase(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 1. Global cleaning (vectorized)
    df = df.replace([pd.NA, float("inf"), float("-inf")], None)
    df = df.where(pd.notnull(df), None)

    # 2. Clean strings
    for col in df.select_dtypes(include="object"):
        df[col] = df[col].apply(
            lambda x: x.strip() if isinstance(x, str) else x
        )

    # 3. Final pass: convert any remaining NaN / non-finite / non-JSON-safe
    #    values to None (covers date columns like 'expiry' that hold float NaN)
    for col in df.columns:
        df[col] = df[col].apply(_json_safe_value)
        

    # 4. Fix integer columns LAST so df.where/replace above can't revert them
    #    back to float64 (which would serialize as "500002.0" and fail bigint insert)
    INT_COLUMNS = ["instrument_token"]
    for col in INT_COLUMNS:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: int(float(x)) if x is not None else None
            )

    return df

def upsert_instruments_to_supabase(df: pd.DataFrame) -> None:
    """
    Write the full instrument dump to Supabase using an upsert keyed by instrument_token.
    """
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )
   
    # Clear existing records before inserting fresh data
    delete_endpoint = f"{supabase_url}/rest/v1/{table_name}?instrument_token=gte.0"
    delete_headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Prefer": "return=minimal",
    }
    try:
        delete_request = Request(delete_endpoint, headers=delete_headers, method="DELETE")
        with urlopen(delete_request, timeout=60) as resp:
            resp.read()
        st.info("Existing records cleared from Supabase table.")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Failed to clear Supabase table before insert — HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to clear Supabase table before insert: {exc.reason}") from exc

    endpoint = f"{supabase_url}/rest/v1/{table_name}?on_conflict=instrument_token"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    records = df.to_dict(orient="records")
    indexed_records = list(zip(df.index.tolist(), records))

    INT_RECORD_COLUMNS = ("instrument_token",)

    for chunk in _chunk_records(indexed_records, SUPABASE_BATCH_SIZE):
        chunk_rows = [record for _, record in chunk]
        for row in chunk_rows:
            for col in INT_RECORD_COLUMNS:
                v = row.get(col)
                if v is not None:
                    try:
                        row[col] = int(float(v))
                    except (TypeError, ValueError):
                        pass
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


st.title("Instrument Dump")
#st.caption("Fetch the daily instrument dump from Kite Connect, or upload a local CSV to sync to Supabase.")

if "request_token" in st.query_params and "access_token" not in st.session_state:
    bootstrap_kite_app("Instrument Dump")

tab_kite, tab_upload = st.tabs(["Download from Kite", "Upload CSV to Supabase"])

with tab_kite:
    st.write("Fetch the full instrument list directly from the Kite Connect API and download it as a CSV.")
    if st.button("Fetch from Kite Connect"):
        try:
            kite, _, _ = bootstrap_kite_app("Instrument Dump")
            with st.spinner("Fetching instruments from Kite..."):
                instruments_df = fetch_instruments_dump(kite)
            st.success(f"Fetched {len(instruments_df):,} instruments from Kite Connect.")
            st.download_button(
                "Download CSV",
                data=instruments_df.to_csv(index=False),
                file_name=f"kite_instruments_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )
        except Exception as exc:
            if is_token_error(exc):
                clear_auth_state()
                st.error("Your session expired. Please login again.")
                st.rerun()
            st.error(f"Failed to fetch instruments from Kite: {exc}")

with tab_upload:
    #st.write("Upload a CSV or Excel file to validate and sync instrument data to Supabase.")
    try:
        instruments_df = find_instruments_file_from_upload()
        try:
            upsert_instruments_to_supabase(instruments_df)
            st.success("Instrument dump synced to Supabase.")
        except Exception as supabase_exc:
            st.warning(f"Loaded instruments, but Supabase sync failed: {supabase_exc}")
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again.")
            st.rerun()
        st.error("Error loading instrument list. Please try again.")

if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        clear_auth_state()
        st.rerun()
