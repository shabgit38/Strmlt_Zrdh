import json
import math
import hashlib
from pathlib import Path
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import numpy as np
import streamlit as st
from kiteconnect import KiteConnect
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error



SUPABASE_BATCH_SIZE = 500
SUPABASE_READ_PAGE_SIZE = 1000
REQUIRED_INSTRUMENT_COLUMNS = {"instrument_token", "tradingsymbol", "name"}
MIN_COMPLETE_DUMP_ROWS = 10000
COMPLETE_DUMP_REQUIRED_EXCHANGES = {"NSE", "BSE", "NFO", "BFO"}
COMPLETE_DUMP_REQUIRED_TYPES = {"EQ", "CE", "PE"}


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
    total_rows = len(df)
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

    normalized_df = clean_dataframe_for_supabase(df)
    print(normalized_df.head(5))
    ready_count = len(normalized_df)
    print(f"Prepared {ready_count} record(s) for Supabase upload.")
    if ready_count == 0:
        return


    issues = _scan_dataframe_for_bad_values(normalized_df)
    print(f"Scanned dataframe for JSON serialization issues, found {len(issues)} issue(s).")
    if issues:
        message = "Found non-JSON-safe values in the CSV: " + "; ".join(issues[:20])
        print(message)
        st.error(message)


    st.info(
        f"File validated: {total_rows:,} total rows, {total_skipped:,} skipped, "
        f"{ready_count:,} ready for Supabase upload."
    )
    return normalized_df


def fetch_instruments_dump(kite: KiteConnect) -> pd.DataFrame:
    return pd.DataFrame(kite.instruments())


def _json_safe_value(value: Any) -> Any:
    """Convert pandas/numpy values into JSON-safe primitives for Supabase."""
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, np.generic):
        return _json_safe_value(value.item())

    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()

    if isinstance(value, float) and not math.isfinite(value):
        return None

    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, str):
        return value.strip()

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
            try:
                missing = pd.isna(value)
                if isinstance(missing, bool) and missing:
                    issues.append(f"row={row_index}, column={column_name}, value={value!r}")
                    continue
            except (TypeError, ValueError):
                pass
            if isinstance(value, float) and not math.isfinite(value):
                issues.append(f"row={row_index}, column={column_name}, value={value!r}")
            elif isinstance(value, complex):
                issues.append(f"row={row_index}, column={column_name}, value={value!r}")
            elif isinstance(value, (list, dict, set, tuple)):
                issues.append(f"row={row_index}, column={column_name}, value_type={type(value).__name__}")
    return issues


def _chunk_records(records: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [records[i : i + chunk_size] for i in range(0, len(records), chunk_size)]


def _supabase_record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    record = {key: _json_safe_value(value) for key, value in row.items()}
    value = record.get("instrument_token")
    if value is not None:
        record["instrument_token"] = int(float(value))
    return record


def _supabase_indexed_records(df: pd.DataFrame) -> list[tuple[Any, dict[str, Any]]]:
    records = df.to_dict(orient="records")
    return [
        (row_index, _supabase_record_from_row(record))
        for row_index, record in zip(df.index.tolist(), records)
    ]


def _build_supabase_payloads(indexed_records: list[tuple[Any, dict[str, Any]]]) -> list[tuple[int, bytes]]:
    payloads: list[tuple[int, bytes]] = []
    for chunk in _chunk_records(indexed_records, SUPABASE_BATCH_SIZE):
        chunk_rows = [record for _, record in chunk]
        try:
            payload = json.dumps(chunk_rows, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError) as exc:
            issues = _find_json_serialization_issues(chunk)
            details = "; ".join(issues[:20]) if issues else "no specific cell identified"
            raise RuntimeError(
                f"Supabase payload contains non-JSON-safe values before upload in chunk starting at row "
                f"{chunk[0][0]}: {details}"
            ) from exc
        if payload and payload != b"[]":
            payloads.append((len(chunk_rows), payload))
    return payloads


def _instrument_source_hash(record: dict[str, Any]) -> str:
    """Return a stable checksum of all instrument fields, including token and symbol."""
    comparable_record = {
        key: _json_safe_value(value)
        for key, value in record.items()
        if key != "source_hash"
    }
    payload = json.dumps(
        comparable_record,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _add_source_hashes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["source_hash"] = [
        _instrument_source_hash(record)
        for record in df.to_dict(orient="records")
    ]
    return df


def _fetch_existing_source_hashes(supabase_url: str, supabase_key: str, table_name: str) -> dict[int, str | None]:
    """Fetch only the fields needed to determine which records require an upsert."""
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        "?select=instrument_token,source_hash&order=instrument_token.asc"
    )
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Range-Unit": "items",
    }
    existing: dict[int, str | None] = {}
    start = 0

    while True:
        page_headers = {**headers, "Range": f"{start}-{start + SUPABASE_READ_PAGE_SIZE - 1}"}
        request = Request(endpoint, headers=page_headers, method="GET")
        try:
            with urlopen(request, timeout=60) as response:
                page = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Failed to fetch existing instrument hashes — HTTP {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"Failed to fetch existing instrument hashes: {exc.reason}") from exc

        for row in page:
            token = row.get("instrument_token")
            if token is not None:
                existing[int(token)] = row.get("source_hash")

        if len(page) < SUPABASE_READ_PAGE_SIZE:
            return existing
        start += SUPABASE_READ_PAGE_SIZE

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
    Upsert only new or changed instruments, keyed by instrument_token.
    """
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    _validate_complete_instrument_dump(df)
    hashed_df = _add_source_hashes(df)
    existing_hashes = _fetch_existing_source_hashes(supabase_url, supabase_key, table_name)
    existing_hash_series = hashed_df["instrument_token"].map(existing_hashes)
    changed_df = hashed_df[existing_hash_series != hashed_df["source_hash"]]
    unchanged_count = len(hashed_df) - len(changed_df)

    indexed_records = _supabase_indexed_records(changed_df)
    payloads = _build_supabase_payloads(indexed_records)
    prepared_count = sum(row_count for row_count, _ in payloads)
    if prepared_count == 0:
        st.success(f"Supabase sync complete: 0 new or changed rows; {unchanged_count:,} unchanged.")
        return
    st.info(
        f"Upload preflight passed: {prepared_count:,} new or changed rows JSON-safe; "
        f"{unchanged_count:,} unchanged rows skipped."
    )

    endpoint = f"{supabase_url}/rest/v1/{quote(table_name, safe='')}?on_conflict=instrument_token"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    uploaded_count = 0
    for row_count, payload in payloads:
        request = Request(endpoint, data=payload, headers=headers, method="POST")

        try:
            with urlopen(request, timeout=60) as response:
                response.read()
            uploaded_count += row_count
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            failed_count = prepared_count - uploaded_count
            raise RuntimeError(
                f"Supabase upload failed after {uploaded_count:,} uploaded rows; "
                f"{failed_count:,} rows were not uploaded. HTTP {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            failed_count = prepared_count - uploaded_count
            raise RuntimeError(
                f"Supabase upload failed after {uploaded_count:,} uploaded rows; "
                f"{failed_count:,} rows were not uploaded. {exc.reason}"
            ) from exc

    st.success(
        f"Supabase sync complete: {prepared_count:,} new or changed rows uploaded, "
        f"{unchanged_count:,} unchanged rows skipped, 0 failed."
    )


def _validate_complete_instrument_dump(df: pd.DataFrame) -> None:
    """
    Guard clear-and-replace syncs from partial instrument uploads.
    """
    if len(df) < MIN_COMPLETE_DUMP_ROWS:
        raise ValueError(
            f"Upload has only {len(df):,} rows. Clear-and-replace requires a complete Kite dump "
            f"with at least {MIN_COMPLETE_DUMP_ROWS:,} rows."
        )

    missing_columns = {"exchange", "instrument_type"} - set(df.columns)
    if missing_columns:
        raise ValueError(f"Complete dump validation failed. Missing columns: {', '.join(sorted(missing_columns))}")

    exchanges = {str(value).upper().strip() for value in df["exchange"].dropna().tolist()}
    instrument_types = {str(value).upper().strip() for value in df["instrument_type"].dropna().tolist()}
    missing_exchanges = COMPLETE_DUMP_REQUIRED_EXCHANGES - exchanges
    missing_types = COMPLETE_DUMP_REQUIRED_TYPES - instrument_types

    if missing_exchanges or missing_types:
        details = []
        if missing_exchanges:
            details.append(f"missing exchanges: {', '.join(sorted(missing_exchanges))}")
        if missing_types:
            details.append(f"missing instrument types: {', '.join(sorted(missing_types))}")
        raise ValueError("Upload does not look like a complete Kite dump (" + "; ".join(details) + ").")


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
        except Exception as supabase_exc:
            st.error(f"Supabase upload failed: {supabase_exc}")
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
