# streamlit_app.py
# -------------------------------------------------------------
# ATTOM Expanded Sales/Loan History Viewer — friendly UI + .env + password gate
# -------------------------------------------------------------
# What this does
# - Prompts you for a property address
# - Calls ATTOM "saleshistory/expandedhistory" endpoint
# - Extracts and displays sales & loan/mortgage history
# - Focus table: Purchase Date (year), Loan Type, Lender Name
# - Optional tabs for full parsed data + raw JSON
# - Requires a password before use (set in .env or st.secrets)
#
# How to run
#   1) Install deps:  pip install streamlit requests pandas python-dateutil python-dotenv
#   2) Create .env with:  ATTOM_API_KEY="YOUR_API_KEY" and APP_PASSWORD="mypassword"
#   3) Run:  streamlit run streamlit_app.py

from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st
from dateutil import parser as dateparser
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# ------------------------- UI CONFIG -------------------------
st.set_page_config(
    page_title="Loan History Viewer",
    page_icon="🏠",
    layout="wide",
)

# Global CSS / aesthetic polish
st.markdown(
    """
    <style>
      .main .block-container {padding-top: 1.25rem; padding-bottom: 2rem;}
      .card {border-radius: 16px; padding: 16px; box-shadow: 0 6px 20px rgba(0,0,0,0.08); background: var(--background-color, white); border:1px solid #e5e7eb}
      .pill {display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;background:#eef2ff;color:#3730a3;font-weight:600;font-size:12px;margin-right:8px;border:1px solid #e5e7eb}
      .pill.good {background:#ecfdf5;color:#065f46}
      .pill.warn {background:#fff7ed;color:#9a3412}
      .subtle {color:#6b7280}
      .small {font-size: 12px}
      .section {border-radius: 16px; padding: 18px; border:1px solid #e5e7eb; background: #fafafa}
      .muted {color:#9ca3af}
    </style>
    """,
    unsafe_allow_html=True,
)

# ------------------------- HELPERS -------------------------

ATTOM_BASE = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/saleshistory/expandedhistory"

LOAN_KEYS = {
    "lender", "lender1", "lender2", "lenderName", "lenderName1", "lenderName2",
    "loanAmount", "amountLoan", "loanAmt", "loanType", "loanToValue", "lienType",
    "interestRate", "loanTerm", "loanDueDate", "recordingDate", "documentDate", "docNumber",
    "loanTypeCode", "lenderLastName", "beneficiary", "date", "term",
}
SALE_KEYS = {
    "saleAmount", "salePrice", "price", "saleAmt",
    "deedType", "transferTax", "buyerName", "sellerName", "saleTransDate", "saleRecDate",
}
DATE_KEYS = {"documentDate", "recordingDate", "saleDate", "saleTransDate", "saleRecDate", "contractDate", "date"}


def get_api_key() -> str | None:
    key = os.getenv("ATTOM_API_KEY")
    if not key and hasattr(st, "secrets"):
        key = st.secrets.get("attom", {}).get("api_key")
    return key


def get_app_password() -> str | None:
    pwd = os.getenv("APP_PASSWORD")
    if not pwd and hasattr(st, "secrets"):
        pwd = st.secrets.get("app_password")
    return pwd


def check_password() -> bool:
    """Prompt for a password and return True if correct."""
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    def password_entered():
        if st.session_state["password"] == get_app_password():
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if not st.session_state["password_correct"]:
        st.text_input("Enter app password", type="password", on_change=password_entered, key="password")
        if st.session_state.get("password_correct") is False:
            st.error("Password incorrect")
        return False
    else:
        return True


@st.cache_data(show_spinner=False)
def fetch_attom(address1: str, address2: str, api_key: str) -> Tuple[Dict[str, Any], requests.Response]:
    params = {"address1": address1, "address2": address2}
    url = f"{ATTOM_BASE}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/json", "apikey": api_key}
    resp = requests.get(url, headers=headers, timeout=30)
    try:
        data = resp.json()
    except Exception:
        data = {"_raw_text": resp.text}
    return data, resp


def _looks_like_record(d: Dict[str, Any]) -> bool:
    return len(set(d.keys()) & (LOAN_KEYS | SALE_KEYS)) > 0


def _coerce_date(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    try:
        return dateparser.parse(v).date().isoformat()
    except Exception:
        return v


def _normalize_row(d: Dict[str, Any]) -> Dict[str, Any]:
    colmap = {
        "documentDate": ["documentDate", "docDate", "date"],
        "recordingDate": ["recordingDate", "saleRecDate"],
        "recordType": ["recordType", "doctype", "docType", "type"],
        "salePrice": ["saleAmount", "salePrice", "price", "saleAmt"],
        "deedType": ["deedType"],
        "loanAmount": ["loanAmount", "amountLoan", "loanAmt", "amount"],
        "loanType": ["loanType", "loanTypeCode"],
        "lienType": ["lienType"],
        "interestRate": ["interestRate", "rate"],
        "loanTerm": ["loanTerm", "term"],
        "loanToValue": ["loanToValue", "ltv"],
        "docNumber": ["docNumber", "documentNumber", "trustDeedDocumentNumber"],
        "lenderName": ["lender", "lender1", "lenderName", "lenderName1", "lenderLastName", "beneficiary"],
        "buyerName": ["buyerName"],
        "sellerName": ["sellerName"],
        "saleDate": ["saleDate", "saleTransDate", "contractDate", "date"],
    }
    out = {}
    for std_col, candidates in colmap.items():
        for c in candidates:
            if c in d and d[c] not in (None, ""):
                out[std_col] = d[c]
                break
    for k in list(out.keys()):
        if k in DATE_KEYS or k.lower().endswith("date"):
            out[k] = _coerce_date(out[k])
    return out


def harvest_records(obj: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    if isinstance(obj, dict):
        if _looks_like_record(obj):
            found.append(obj)
        for v in obj.values():
            found.extend(harvest_records(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(harvest_records(item))
    return found


def make_dataframe(records: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = [_normalize_row(r) for r in records]
    rows = [r for r in rows if any(str(v).strip() for v in r.values())]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for col in ("documentDate", "recordingDate", "saleDate"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def make_focus_loans_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    best_date = next((c for c in ("saleDate", "documentDate", "recordingDate") if c in df.columns), None)
    if not best_date:
        return pd.DataFrame(columns=["Purchase Date", "Year", "Loan Type", "Lender Name"])
    view = pd.DataFrame()
    view["Purchase Date"] = pd.to_datetime(df[best_date], errors="coerce")
    if "loanType" in df.columns:
        view["Loan Type"] = df["loanType"].replace({"CONV": "Conventional", "FHA": "FHA", "VA": "VA", "HELOC": "HELOC"})
    if "lenderName" in df.columns:
        view["Lender Name"] = df["lenderName"]
    if "Loan Type" in view.columns and "Lender Name" in view.columns:
        view = view[view[["Loan Type", "Lender Name"]].notna().any(axis=1)]
    elif "Loan Type" in view.columns:
        view = view[view["Loan Type"].notna()]
    elif "Lender Name" in view.columns:
        view = view[view["Lender Name"].notna()]
    if view.empty:
        return view
    view["Year"] = view["Purchase Date"].dt.year
    view = view.sort_values(by=["Purchase Date"], ascending=False)
    ordered_cols = [c for c in ["Year", "Purchase Date", "Loan Type", "Lender Name"] if c in view.columns]
    view = view[ordered_cols]
    if "Purchase Date" in view.columns:
        view["Purchase Date"] = view["Purchase Date"].dt.date.astype("string")
    return view


# ------------------------- MAIN APP -------------------------
if check_password():
    st.title("🏠 ATTOM Sales & Loan History Viewer")

    api_key = get_api_key()
    status_html = (
        "<span class='pill good'>✓ API key loaded</span>" if api_key else
        "<span class='pill warn'>! API key missing</span>"
    )
    st.markdown(status_html, unsafe_allow_html=True)

    with st.container():
        st.markdown("<div class='section'>", unsafe_allow_html=True)
        st.markdown("#### 🔎 Look up an address")
        with st.form("addr_form"):
            ex1, ex2 = "1111 11th St SE", "Chicago, IL 60007"
            col1, col2 = st.columns([2, 1])
            with col1:
                street = st.text_input("Street Address (Address1)", placeholder=f"e.g., {ex1}")
            with col2:
                address2_raw = st.text_input("Address2 (optional)", placeholder=f"e.g., APT 101")
            c1, c2, c3 = st.columns(3)
            with c1:
                city = st.text_input("City", placeholder="Chicago")
            with c2:
                state = st.text_input("State (2-letter)", max_chars=2, placeholder="IL")
            with c3:
                zipcode = st.text_input("ZIP", placeholder="60007")
            submitted = st.form_submit_button("Fetch History", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    if submitted:
        if not api_key:
            st.error("No API key found. Add ATTOM_API_KEY to your .env (or st.secrets) and restart.")
            st.stop()
        if not street:
            st.error("Street Address is required.")
            st.stop()
        address2 = address2_raw.strip()
        if not address2 and (city or state or zipcode):
            parts = []
            if city: parts.append(city.strip())
            if state: parts.append(state.strip().upper())
            line = ", ".join(parts)
            if zipcode: line = f"{line} {zipcode.strip()}" if line else zipcode.strip()
            address2 = line
        if not address2:
            st.warning("Provide Address2 directly or fill City/State/ZIP to construct it.")
            st.stop()
        st.markdown("### Results for ")
        st.caption(f"**{street}**, **{address2}**")
        with st.spinner("Calling ATTOM API…"):
            data, resp = fetch_attom(street, address2, api_key)
        try:
            records = harvest_records(data)
            df = make_dataframe(records)
        except Exception as e:
            st.error(f"Failed to parse response: {e}")
            st.stop()
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        focus_preview = make_focus_loans_table(df)
        total_rows = len(df) if not df.empty else 0
        loan_rows = len(focus_preview) if isinstance(focus_preview, pd.DataFrame) else 0
        st.markdown(f"**Found** <span class='pill'>{loan_rows} loan rows</span> in <span class='pill'>{total_rows} parsed records</span>.", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
        tabs = st.tabs(["Loan Summary", "Full Parsed", "Raw JSON"])
        with tabs[0]:
            st.markdown("##### 📑 Loan Summary (Purchase Date / Loan Type / Lender)")
            focus = focus_preview
            if focus.empty:
                st.info("No loan-type/lender records detected. Use the Full Parsed or Raw JSON tabs to investigate.")
            else:
                left, right = st.columns([2,2])
                with left:
                    years = sorted(list({int(y) for y in focus["Year"].dropna().unique()}), reverse=True) if "Year" in focus.columns else []
                    year_filter = st.multiselect("Filter by Year", options=years, default=years[:10] if years else [])
                with right:
                    lenders = sorted(list({str(x) for x in focus.get("Lender Name", pd.Series(dtype=str)).dropna().unique()}))
                    lender_filter = st.multiselect("Filter by Lender", options=lenders)
                filtered = focus.copy()
                if year_filter and "Year" in filtered.columns:
                    filtered = filtered[filtered["Year"].isin(year_filter)]
                if lender_filter and "Lender Name" in filtered.columns:
                    filtered = filtered[filtered["Lender Name"].isin(lender_filter)]
                st.dataframe(filtered, use_container_width=True)
                c1, c2 = st.columns(2)
                csv = filtered.to_csv(index=False).encode("utf-8")
                json_str = filtered.to_json(orient="records")
                with c1:
                    st.download_button("Download CSV", data=csv, file_name="attom_loan_summary.csv", mime="text/csv", use_container_width=True)
                with c2:
                    st.download_button("Download JSON", data=json_str, file_name="attom_loan_summary.json", mime="application/json", use_container_width=True)
        with tabs[1]:
            st.markdown("##### 🧩 Full Parsed Records (for deeper analysis)")
            if df.empty:
                st.info("Nothing parsed.")
            else:
                preferred = ["documentDate", "recordingDate", "saleDate", "loanAmount", "loanType", "lienType", "interestRate", "loanTerm", "loanToValue", "lenderName", "buyerName", "sellerName", "docNumber", "salePrice"]
                ordered = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
                st.dataframe(df[ordered], use_container_width=True)
        with tabs[2]:
            st.markdown("##### 🛠️ Raw JSON (advanced)")
            st.caption("Troubleshoot field mappings or verify values.")
            st.code(json.dumps(data, indent=2)[:200000], language="json")
    # ------------------------- FOOTER -------------------------
    st.write("")
    st.markdown(
        "<span class='small muted'>Key is never displayed. Loaded from .env or st.secrets. "
        "Loan summary focuses on Purchase Date, Loan Type, and Lender. "
        "Access requires the app password.</span>",
        unsafe_allow_html=True,
    )
