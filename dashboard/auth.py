"""Lightweight authentication for the Token Cop dashboard.

For v1 this uses a simple password check against an environment variable.
For production team deployment, replace this with Cognito integration
using the existing user pool (us-east-1_hYAk8mbYH) and OAuth2 flow.
"""

import hmac
import os

import streamlit as st


def check_password() -> bool:
    """Simple password check for dashboard access.

    Checks against the TOKEN_COP_DASHBOARD_PASSWORD environment variable.
    Defaults to 'tokencop' if not set (development only).
    """
    if st.session_state.get("authenticated"):
        return True

    st.markdown("### Token Cop Dashboard")
    st.caption("Enter the dashboard password to continue.")

    password = st.text_input("Dashboard Password", type="password")
    if password:
        correct = os.environ.get("TOKEN_COP_DASHBOARD_PASSWORD", "tokencop")
        if hmac.compare_digest(password, correct):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password")
    return False
