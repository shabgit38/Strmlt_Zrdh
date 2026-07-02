import json
from datetime import date
from html import escape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_auth import get_secret_value


STOCK_NOTES_TABLE_NAME = "stock_notes"
STOCK_NOTE_COLUMNS = [
    "id",
    "symbol",
    "why",
    "moat",
    "risk",
    "last_reviewed_date",
    "research_age_days",
]


def _supabase_headers(supabase_key: str, *, write: bool = False) -> dict[str, str]:
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }
    if write:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=minimal"
    return headers


def _stock_notes_config() -> tuple[str, str, str]:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_STOCK_NOTES_TABLE_NAME").strip() or STOCK_NOTES_TABLE_NAME

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    return supabase_url, supabase_key, table_name


def _empty_notes_df() -> pd.DataFrame:
    return pd.DataFrame(columns=STOCK_NOTE_COLUMNS)


def _normalize_note_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _notes_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_review_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _research_age_days(review_date: date | None) -> int | None:
    if review_date is None:
        return None
    return max((date.today() - review_date).days, 0)


def _stock_note_record(record: dict[str, Any]) -> dict[str, Any]:
    notes = _notes_dict(record.get("notes"))
    review_date = _parse_review_date(
        record.get("last_reviewed_date") or record.get("last_review_date")
    )
    return {
        "id": record.get("id"),
        "symbol": _normalize_note_symbol(record.get("symbol")),
        "why": record.get("why") or notes.get("why"),
        "moat": record.get("moat") or notes.get("moat"),
        "risk": record.get("risk") or notes.get("risk"),
        "last_reviewed_date": review_date.isoformat() if review_date else None,
        "research_age_days": _research_age_days(review_date),
    }


@st.cache_data(ttl=10 * 60)
def load_stock_notes_from_supabase(symbols: list[str]) -> pd.DataFrame:
    normalized_symbols = sorted(
        {_normalize_note_symbol(symbol) for symbol in symbols if _normalize_note_symbol(symbol)}
    )
    if not normalized_symbols:
        return _empty_notes_df()

    supabase_url, supabase_key, table_name = _stock_notes_config()
    symbol_filter = ",".join(f"symbol.eq.{quote(symbol, safe='')}" for symbol in normalized_symbols)
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?select=*&or=({symbol_filter})"
    )
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")

    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase stock notes lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase stock notes lookup failed: {exc.reason}") from exc

    notes_df = pd.DataFrame(_stock_note_record(record) for record in records)
    if notes_df.empty:
        return _empty_notes_df()
    return notes_df.drop_duplicates("symbol", keep="last")


def insert_stock_note_in_supabase(
    symbol: str,
    why: str,
    moat: str,
    risk: str,
    *,
    reviewed_date: date | None = None,
) -> None:
    normalized_symbol = _normalize_note_symbol(symbol)
    if not normalized_symbol:
        raise ValueError("Symbol is required.")

    supabase_url, supabase_key, table_name = _stock_notes_config()
    endpoint = f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
    record = {
        "symbol": normalized_symbol,
        "why": why.strip() or None,
        "moat": moat.strip() or None,
        "risk": risk.strip() or None,
        "last_reviewed_date": (reviewed_date or date.today()).isoformat(),
    }
    payload = json.dumps(record).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers=_supabase_headers(supabase_key, write=True),
        method="POST",
    )

    try:
        with urlopen(request, timeout=60):
            pass
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase stock note insert failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase stock note insert failed: {exc.reason}") from exc


def update_stock_note_in_supabase(
    row_id: Any,
    symbol: str,
    why: str,
    moat: str,
    risk: str,
    *,
    reviewed_date: date | None = None,
) -> None:
    if row_id is None or pd.isna(row_id):
        raise ValueError("Stock note id is required for update.")

    supabase_url, supabase_key, table_name = _stock_notes_config()
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?id=eq.{quote(str(row_id), safe='')}"
    )
    record = {
        "symbol": _normalize_note_symbol(symbol),
        "why": why.strip() or None,
        "moat": moat.strip() or None,
        "risk": risk.strip() or None,
        "last_reviewed_date": (reviewed_date or date.today()).isoformat(),
    }
    payload = json.dumps(record).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers=_supabase_headers(supabase_key, write=True),
        method="PATCH",
    )

    try:
        with urlopen(request, timeout=60):
            pass
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase stock note update failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase stock note update failed: {exc.reason}") from exc


def _format_card_value(value: Any, fallback: str = "-") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    return text or fallback


def _format_research_age(value: Any) -> str:
    age = pd.to_numeric(value, errors="coerce")
    if pd.isna(age):
        return "Not reviewed"
    return f"{int(age)}d ago"


def _research_age_class(value: Any) -> str:
    age = pd.to_numeric(value, errors="coerce")
    if pd.isna(age):
        return "overdue"
    if age <= 30:
        return "fresh"
    if age <= 90:
        return "watch"
    return "overdue"


def _stock_card_html(row: pd.Series) -> str:
    symbol = escape(_format_card_value(row.get("ticker") or row.get("symbol")))
    ltp = pd.to_numeric(row.get("ltp"), errors="coerce")
    price = "-" if pd.isna(ltp) else f"Rs {float(ltp):.2f}"
    label = escape(_format_card_value(row.get("entry_signal"), "No signal"))
    score = pd.to_numeric(row.get("pullback_score"), errors="coerce")
    score_text = "-" if pd.isna(score) else f"{float(score):.1f}"
    age_class = _research_age_class(row.get("research_age_days"))
    reviewed = escape(_format_research_age(row.get("research_age_days")))
    review_date = escape(_format_card_value(row.get("last_reviewed_date"), "No date"))
    why = escape(_format_card_value(row.get("why"), "No notes added"))
    moat = escape(_format_card_value(row.get("moat"), "No notes added"))
    risk = escape(_format_card_value(row.get("risk"), "No notes added"))

    return f"""
    <div class="stock-memory-card">
        <div class="stock-memory-card__top">
            <div>
                <div class="stock-memory-card__symbol">{symbol}</div>
                <div class="stock-memory-card__price">{price}</div>
            </div>
            <div class="stock-memory-card__score">{score_text}</div>
        </div>
        <div class="stock-memory-card__label">{label}</div>
        <div class="stock-memory-card__notes">
            <div><span>Why</span>{why}</div>
            <div><span>Moat</span>{moat}</div>
            <div><span>Risk</span>{risk}</div>
        </div>
        <div class="stock-memory-card__review stock-memory-card__review--{age_class}">
            Reviewed: {reviewed} <span>{review_date}</span>
        </div>
    </div>
    """


def _has_stock_note(row: pd.Series) -> bool:
    return any(
        _format_card_value(row.get(column), "").strip()
        for column in ["why", "moat", "risk", "last_reviewed_date"]
    )


def _has_note_id(row: pd.Series) -> bool:
    row_id = row.get("id")
    return row_id is not None and pd.notna(row_id)


def _clear_editor_fields(editor_key: str) -> None:
    for suffix in ["why", "moat", "risk"]:
        st.session_state.pop(f"{editor_key}_{suffix}", None)


def _inject_stock_card_styles() -> None:
    st.markdown(
        """
        <style>
            .stock-memory-card {
                border: 1px solid rgba(148, 163, 184, 0.28);
                border-radius: 8px;
                padding: 0.75rem;
                margin-bottom: 0.75rem;
                background: rgba(15, 23, 42, 0.28);
            }
            .stock-memory-card__top {
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                gap: 0.75rem;
            }
            .stock-memory-card__symbol {
                font-size: 1rem;
                font-weight: 700;
                line-height: 1.1;
            }
            .stock-memory-card__price {
                color: #cbd5e1;
                font-size: 0.86rem;
                margin-top: 0.2rem;
            }
            .stock-memory-card__score {
                font-size: 0.9rem;
                font-weight: 700;
                color: #38bdf8;
            }
            .stock-memory-card__label {
                color: #f8fafc;
                font-size: 0.86rem;
                font-weight: 650;
                margin: 0.45rem 0 0.55rem;
            }
            .stock-memory-card__notes {
                display: grid;
                gap: 0.35rem;
                font-size: 0.82rem;
                line-height: 1.25;
            }
            .stock-memory-card__notes span {
                color: #94a3b8;
                display: inline-block;
                font-weight: 700;
                min-width: 2.75rem;
            }
            .stock-memory-card__review {
                display: flex;
                justify-content: space-between;
                gap: 0.5rem;
                margin-top: 0.65rem;
                font-size: 0.78rem;
                font-weight: 700;
            }
            .stock-memory-card__review span {
                color: #94a3b8;
                font-weight: 500;
            }
            .stock-memory-card__review--fresh { color: #22c55e; }
            .stock-memory-card__review--watch { color: #f59e0b; }
            .stock-memory-card__review--overdue { color: #ef4444; }
            .stock-memory-card-edit-title {
                color: #cbd5e1;
                font-size: 0.82rem;
                font-weight: 700;
                margin: 0.25rem 0 0.4rem;
            }
            .stock-memory-card-meta {
                color: #94a3b8;
                font-size: 0.8rem;
                line-height: 1.2;
            }
            .stock-memory-card-note-line {
                font-size: 0.82rem;
                line-height: 1.25;
                margin: 0.18rem 0;
            }
            .stock-memory-card-note-line span {
                color: #94a3b8;
                display: inline-block;
                font-weight: 700;
                min-width: 2.75rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _merge_stock_notes(momentum_df: pd.DataFrame) -> pd.DataFrame:
    if momentum_df.empty or "ticker" not in momentum_df.columns:
        return momentum_df.copy()

    symbols = momentum_df["ticker"].dropna().astype(str).str.upper().str.strip().tolist()
    try:
        notes_df = load_stock_notes_from_supabase(symbols)
    except Exception as exc:
        st.warning(f"Could not load stock notes from Supabase: {exc}")
        notes_df = _empty_notes_df()

    cards_df = momentum_df.copy()
    cards_df["symbol"] = cards_df["ticker"].astype(str).str.upper().str.strip()
    if not notes_df.empty:
        cards_df = cards_df.merge(notes_df, on="symbol", how="left")
        for column in ["id", "why", "moat", "risk", "last_reviewed_date", "research_age_days"]:
            left_column = f"{column}_x"
            right_column = f"{column}_y"
            if left_column in cards_df.columns and right_column in cards_df.columns:
                cards_df[column] = cards_df[right_column].combine_first(cards_df[left_column])
                cards_df = cards_df.drop(columns=[left_column, right_column])
    return cards_df


def render_stock_memory_card(momentum_row: pd.Series) -> None:
    if momentum_row.empty:
        st.info("Select a stock row to view notes.")
        return

    cards_df = _merge_stock_notes(pd.DataFrame([momentum_row]))
    if cards_df.empty:
        st.info("Select a stock row to view notes.")
        return

    _inject_stock_card_styles()
    card_row = cards_df.iloc[0]
    symbol = _normalize_note_symbol(card_row.get("ticker") or card_row.get("symbol"))
    editor_key = f"stock_note_editor_{symbol}"
    is_editing = st.session_state.get(editor_key) or not _has_stock_note(card_row)

    with st.container(border=True):
        header_cols = st.columns([1.8, 1.0, 0.2], vertical_alignment="top")
        with header_cols[0]:
            st.markdown(f"**{symbol or '-'}**")
        with header_cols[1]:
            st.markdown(
                f"<div class='stock-memory-card-meta'>{escape(_format_card_value(card_row.get('entry_signal'), 'No signal'))}</div>",
                unsafe_allow_html=True,
            )
        with header_cols[2]:
            if not is_editing and st.button(
                "",
                key=f"{editor_key}_open",
                icon=":material/edit:",
                help="Edit stock note",
                width="content",
            ):
                st.session_state[editor_key] = True
                st.rerun()

        if is_editing:
            st.markdown(
                "<div class='stock-memory-card-edit-title'>"
                + ("Edit note" if _has_note_id(card_row) else "Add note")
                + "</div>",
                unsafe_allow_html=True,
            )
            with st.form(f"{editor_key}_form"):
                why = st.text_area(
                    "Why",
                    value=_format_card_value(card_row.get("why"), ""),
                    height=58,
                    key=f"{editor_key}_why",
                )
                moat = st.text_area(
                    "Moat",
                    value=_format_card_value(card_row.get("moat"), ""),
                    height=58,
                    key=f"{editor_key}_moat",
                )
                risk = st.text_area(
                    "Risk",
                    value=_format_card_value(card_row.get("risk"), ""),
                    height=58,
                    key=f"{editor_key}_risk",
                )
                action_cols = st.columns([0.18, 0.18, 1], vertical_alignment="center")
                with action_cols[0]:
                    save_clicked = st.form_submit_button("Save", type="primary")
                with action_cols[1]:
                    cancel_clicked = st.form_submit_button("Cancel")

            if cancel_clicked:
                st.session_state.pop(editor_key, None)
                _clear_editor_fields(editor_key)
                st.rerun()
            if save_clicked:
                try:
                    if _has_note_id(card_row):
                        update_stock_note_in_supabase(card_row.get("id"), symbol, why, moat, risk)
                    else:
                        insert_stock_note_in_supabase(symbol, why, moat, risk)
                    load_stock_notes_from_supabase.clear()
                    st.session_state.pop(editor_key, None)
                    _clear_editor_fields(editor_key)
                    st.success("Stock note saved.")
                    st.rerun()
                except Exception as exc:
                    st.warning(f"Could not save stock note: {exc}")
            return

        why = escape(_format_card_value(card_row.get("why"), "No notes added"))
        moat = escape(_format_card_value(card_row.get("moat"), "No notes added"))
        risk = escape(_format_card_value(card_row.get("risk"), "No notes added"))
        reviewed = escape(_format_research_age(card_row.get("research_age_days")))
        review_date = escape(_format_card_value(card_row.get("last_reviewed_date"), "No date"))
        age_class = _research_age_class(card_row.get("research_age_days"))
        st.markdown(
            f"""
            <div class='stock-memory-card-note-line'><span>Why</span>{why}</div>
            <div class='stock-memory-card-note-line'><span>Moat</span>{moat}</div>
            <div class='stock-memory-card-note-line'><span>Risk</span>{risk}</div>
            <div class='stock-memory-card__review stock-memory-card__review--{age_class}'>
                Reviewed: {reviewed} <span>{review_date}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_stock_memory_cards(momentum_df: pd.DataFrame, *, columns: int = 4) -> None:
    if momentum_df.empty or "ticker" not in momentum_df.columns:
        return

    cards_df = _merge_stock_notes(momentum_df)
    _inject_stock_card_styles()

    card_columns = st.columns(columns)
    for index, (_, row) in enumerate(cards_df.iterrows()):
        with card_columns[index % columns]:
            st.markdown(_stock_card_html(row), unsafe_allow_html=True)
