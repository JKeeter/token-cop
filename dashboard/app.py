"""Token Cop Dashboard -- Team-wide LLM token usage tracker.

Run with: streamlit run dashboard/app.py
"""

from datetime import datetime, timedelta, timezone

import streamlit as st

from dashboard.auth import check_password

st.set_page_config(page_title="Token Cop Dashboard", page_icon="\U0001f694", layout="wide")

# --- Authentication gate ---
if not check_password():
    st.stop()

# --- Sidebar navigation ---
st.sidebar.title("Token Cop")
st.sidebar.caption("More tokens is FINE -- they need to be SMART tokens.")

page = st.sidebar.radio("Navigate", ["Overview", "Per-Model", "Recommendations"])

st.sidebar.divider()

# Date range selector
st.sidebar.subheader("Date Range")
range_option = st.sidebar.selectbox(
    "Period",
    ["Last 7 days", "Last 30 days", "Custom"],
    index=0,
)

now = datetime.now(timezone.utc)
if range_option == "Last 7 days":
    start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")
    days = 7
elif range_option == "Last 30 days":
    start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")
    days = 30
else:
    col1, col2 = st.sidebar.columns(2)
    with col1:
        start_dt = st.date_input("Start", value=now.date() - timedelta(days=7))
    with col2:
        end_dt = st.date_input("End", value=now.date())
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")
    days = (end_dt - start_dt).days or 7

# Refresh button
if st.sidebar.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.caption(f"Showing: {start_date} to {end_date}")


# --- Cached data loading ---
@st.cache_data(ttl=300, show_spinner="Fetching usage data...")
def load_provider_data(start: str, end: str) -> dict:
    from dashboard.data import fetch_all_provider_data
    return fetch_all_provider_data(start, end)


@st.cache_data(ttl=300, show_spinner="Running audit...")
def load_audit_data(num_days: int) -> dict | None:
    from dashboard.data import fetch_audit_data
    return fetch_audit_data(num_days)


# Load data
data = load_provider_data(start_date, end_date)
audit = load_audit_data(days)

# --- Page routing ---
if page == "Overview":
    from dashboard.views.overview import render_overview
    render_overview(data, audit)

elif page == "Per-Model":
    from dashboard.views.per_user import render_per_user
    render_per_user(data)

elif page == "Recommendations":
    from dashboard.views.recommendations import render_recommendations
    render_recommendations(data, audit)
