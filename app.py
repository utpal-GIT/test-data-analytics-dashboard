"""
Streamlit entry point.

Run from this folder:
    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

import auth
import db
import style
from views import test_analytics, configurations, user_management


st.set_page_config(
    page_title="Test Analytics Dashboard",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# initialise schema + default admin on first launch
db.init_db()
db.cleanup_expired_sessions()
style.apply()


def _header() -> None:
    user = auth.current_user()
    style.header_band(
        "Test Analytics Dashboard",
        [
            ("User", f"{user['username']}"),
            ("Role", user["role"]),
            ("Session left", auth.session_remaining()),
        ],
    )
    c1, _spacer, c3 = st.columns([2, 6, 1.2])
    with c1:
        auth.change_own_password_ui()
    with c3:
        if st.button("Sign out", use_container_width=True):
            auth.logout()
            st.rerun()


def main() -> None:
    if not auth.is_logged_in():
        auth.render_login()
        return

    _header()

    tab_labels = ["📊  Test Analytics", "⚙️  Configurations"]
    if auth.is_admin():
        tab_labels.append("👥  User Management")

    tabs = st.tabs(tab_labels)
    with tabs[0]:
        test_analytics.render()
    with tabs[1]:
        configurations.render()
    if auth.is_admin():
        with tabs[2]:
            user_management.render()


if __name__ == "__main__":
    main()
