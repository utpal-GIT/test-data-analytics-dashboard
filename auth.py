"""
Login + 3-hour session handling.

Sessions live in `st.session_state` AND in a SQLite `sessions` table keyed by
a random token that is also written to the URL via `st.query_params['s']`.
That way a browser refresh (which clears `st.session_state`) can re-hydrate
the login from the query-param token until the 3-hour timer expires.
"""

from __future__ import annotations

import time
from datetime import datetime

import streamlit as st

import db
from passwords import hash_password, verify_password

SESSION_HOURS = 3
QUERY_KEY = "s"


def _now() -> float:
    return time.time()


def _set_query_token(token: str) -> None:
    try:
        st.query_params[QUERY_KEY] = token
    except Exception:
        pass


def _clear_query_token() -> None:
    try:
        if QUERY_KEY in st.query_params:
            del st.query_params[QUERY_KEY]
    except Exception:
        pass


def _read_query_token() -> str | None:
    try:
        return st.query_params.get(QUERY_KEY)
    except Exception:
        return None


def is_logged_in() -> bool:
    # Fast path: session_state already has a live login.
    user = st.session_state.get("user")
    expires = st.session_state.get("session_expires_at")
    if user and expires and _now() <= expires:
        return True

    # Refresh path: rehydrate from the URL token if present and unexpired.
    token = _read_query_token()
    if token:
        sess = db.get_session(token)
        if sess:
            st.session_state["user"] = {
                "id": sess["user_id"],
                "username": sess["username"],
                "password": sess["password"],
                "role": sess["role"],
            }
            try:
                exp_dt = datetime.fromisoformat(sess["expires_at"])
                # SQLite stores naive UTC; treat as epoch using utc -> local
                st.session_state["session_expires_at"] = exp_dt.timestamp()
            except ValueError:
                st.session_state["session_expires_at"] = (
                    _now() + SESSION_HOURS * 3600
                )
            st.session_state["session_token"] = token
            return True

    # Nothing valid — make sure state is clean.
    if user or expires:
        logout()
    return False


def current_user() -> dict | None:
    return st.session_state.get("user") if is_logged_in() else None


def is_admin() -> bool:
    u = current_user()
    return bool(u and u.get("role") == "admin")


def login(username: str, password: str) -> tuple[bool, str]:
    user = db.get_user_by_credentials(username, password)
    if not user:
        return False, "Invalid username or password."
    db.cleanup_expired_sessions()
    token = db.create_session(user["id"], hours=SESSION_HOURS)
    st.session_state["user"] = user
    st.session_state["session_expires_at"] = _now() + SESSION_HOURS * 3600
    st.session_state["session_token"] = token
    _set_query_token(token)
    return True, ""


def logout() -> None:
    token = st.session_state.get("session_token")
    if token:
        db.delete_session(token)
    for k in ("user", "session_expires_at", "session_token"):
        st.session_state.pop(k, None)
    _clear_query_token()


def session_remaining() -> str:
    exp = st.session_state.get("session_expires_at")
    if not exp:
        return ""
    secs = max(0, int(exp - _now()))
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
def render_login() -> None:
    # Center everything using columns (Streamlit widgets can't be nested
    # inside markdown-injected divs, so we constrain via columns instead).
    left, mid, right = st.columns([5, 3, 5])
    with mid:
        st.markdown(
            '<div class="login-card-top">'
            '<div class="brand"><span class="logo">🧪</span>'
            'Test Analytics Dashboard</div>'
            '<div class="sub">Sign in to fit calibration models, evaluate '
            'CLIA performance, and review patient results.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        with st.form("login_form", clear_on_submit=False):
            u = st.text_input("Username", placeholder="e.g. admin")
            p = st.text_input("Password", type="password",
                              placeholder="********")
            ok = st.form_submit_button("Sign in", type="primary",
                                       use_container_width=True)

        pass

    if ok:
        success, msg = login(u.strip(), p)
        if success:
            st.success("Signed in.")
            st.rerun()
        else:
            st.error(msg)


def change_own_password_ui() -> None:
    user = current_user()
    if not user:
        return
    with st.expander("Change my password"):
        with st.form("change_pw"):
            current = st.text_input("Current password", type="password")
            new = st.text_input("New password", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            submit = st.form_submit_button("Update")
        if submit:
            if not verify_password(current, user["password"]):
                st.error("Current password is incorrect.")
            elif not new or new != confirm:
                st.error("New password is empty or does not match confirmation.")
            else:
                db.update_user(user["id"], user["username"], new, user["role"])
                # Keep the cached copy in step with what is now stored.
                user["password"] = hash_password(new)
                st.session_state["user"] = user
                st.success("Password updated.")
