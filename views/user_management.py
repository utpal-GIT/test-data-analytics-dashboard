"""
User Management tab — admin only.

Modern UX:
    - Top toolbar: search + Add new user
    - Card grid: each user in a bordered container with role pill, how the
      password is stored, and per-card Edit / Delete buttons
    - Edit form (or new-user form) appears below when an action is triggered

Passwords are stored as PBKDF2 hashes, so they cannot be displayed. The edit
form sets a new password instead; leaving it blank keeps the current one.
"""

from __future__ import annotations

import math

import streamlit as st

import auth
import db
import style
from passwords import is_hashed


# session-state key holds the current edit target:
#   None        -> no form shown
#   ""          -> creating a new user
#   "<user_id>" -> editing the user with that id (stringified)
EDIT_KEY = "usr_edit_target"


# ---------------------------------------------------------------------------
def render() -> None:
    if not auth.is_admin():
        st.error("Admin only.")
        return

    me = auth.current_user()
    users = db.list_users()

    style.section(
        "User management",
        f"{len(users)} account{'s' if len(users) != 1 else ''} · "
        "you are signed in as " + me["username"],
    )

    # ---- toolbar: search + add ----
    tcol1, tcol2 = st.columns([3, 1])
    query = tcol1.text_input(
        "Search users", placeholder="Filter by username or role…",
        label_visibility="collapsed", key="usr_search",
    )
    if tcol2.button("➕  Add new user", use_container_width=True,
                    type="primary", key="usr_add_new"):
        st.session_state[EDIT_KEY] = ""
        st.rerun()

    filt = (query or "").strip().lower()
    visible = [
        u for u in users
        if not filt or filt in u["username"].lower() or filt in u["role"].lower()
    ]

    if not users:
        st.info("No users yet.")
    elif not visible:
        st.info("No users match your search.")
    else:
        style.section(
            "Accounts",
            f"{len(visible)} of {len(users)} shown" if visible != users
            else None,
        )
        _render_user_cards(me, users, visible)

    # Edit / add panel below if active
    if EDIT_KEY in st.session_state and st.session_state[EDIT_KEY] is not None:
        _render_edit_panel(me, users)


# ---------------------------------------------------------------------------
def _render_user_cards(me: dict, all_users: list[dict],
                       visible: list[dict]) -> None:
    n_cols = 3
    rows = math.ceil(len(visible) / n_cols)
    for r in range(rows):
        cols = st.columns(n_cols)
        for c in range(n_cols):
            i = r * n_cols + c
            if i >= len(visible):
                continue
            _render_user_card(me, all_users, visible[i])


def _render_user_card(me: dict, all_users: list[dict], u: dict) -> None:
    is_self = (u["id"] == me["id"])
    role = (u["role"] or "user").lower()
    role_class = "primary" if role == "admin" else "muted"
    you_pill = '<span class="usr-pill primary">You</span>' if is_self else ""

    # Passwords are stored as PBKDF2 hashes, so there is nothing to reveal —
    # the card reports how the row is stored and the edit form sets a new one.
    stored_label = "hashed" if is_hashed(u["password"]) else "plaintext (legacy)"

    with st.container(border=True):
        st.markdown(
            '<div class="usr-card-head">'
            f'<div class="usr-card-name">{u["username"]}</div>'
            '<div class="usr-card-pills">'
            f'<span class="usr-pill {role_class}">{role}</span>'
            f'{you_pill}'
            '</div></div>'
            '<div class="usr-card-body">'
            '<div class="usr-row">'
            '<span class="lbl">Username</span>'
            f'<span class="val">{u["username"]}</span>'
            '</div>'
            '<div class="usr-row">'
            '<span class="lbl">Password</span>'
            f'<span class="val mono">{stored_label}</span>'
            '</div>'
            '<div class="usr-row">'
            '<span class="lbl">Role</span>'
            f'<span class="val">{role}</span>'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        b2, b3 = st.columns(2)
        if b2.button("Edit", key=f"usr_edit_{u['id']}",
                     use_container_width=True):
            st.session_state[EDIT_KEY] = str(u["id"])
            st.rerun()

        del_armed_key = f"usr_del_armed_{u['id']}"
        armed = st.session_state.get(del_armed_key, False)
        del_label = "Confirm delete" if armed else "Delete"
        if b3.button(del_label, key=f"usr_del_{u['id']}",
                     use_container_width=True, disabled=is_self):
            if is_self:
                st.error("You cannot delete your own account while signed in.")
            elif (u["role"] == "admin"
                  and sum(1 for x in all_users if x["role"] == "admin") <= 1):
                st.error("Cannot delete the only remaining admin.")
                st.session_state.pop(del_armed_key, None)
            else:
                if armed:
                    db.delete_user(u["id"])
                    st.session_state.pop(del_armed_key, None)
                    st.success(f"User '{u['username']}' deleted.")
                    st.rerun()
                else:
                    st.session_state[del_armed_key] = True
                    st.warning(
                        f"Click 'Confirm delete' again to permanently remove "
                        f"user '{u['username']}'."
                    )


# ---------------------------------------------------------------------------
def _render_edit_panel(me: dict, users: list[dict]) -> None:
    target = st.session_state[EDIT_KEY]
    is_new = (target == "")
    seed: dict = {}
    if not is_new:
        try:
            uid = int(target)
        except (TypeError, ValueError):
            st.session_state[EDIT_KEY] = None
            st.rerun()
            return
        seed = next((u for u in users if u["id"] == uid), {})
        if not seed:
            st.session_state[EDIT_KEY] = None
            st.rerun()

    title = "Add new user" if is_new else f"Edit · {seed.get('username')}"
    style.section(title, "All fields are required")

    sfx = "new" if is_new else str(seed.get("id"))

    with st.container(border=True):
        c1, c2, c3 = st.columns([2, 2, 1])
        new_u = c1.text_input("Username", value=seed.get("username", ""),
                              key=f"usr_form_u__{sfx}",
                              placeholder="e.g. alice")
        new_p = c2.text_input(
            "Password", value="", type="password",
            key=f"usr_form_p__{sfx}",
            placeholder=("set a password" if is_new
                         else "leave blank to keep current"),
            help=("Stored as a PBKDF2 hash. Existing passwords cannot be read "
                  "back, so leave this blank unless you are setting a new one."),
        )
        new_r = c3.selectbox(
            "Role", ["user", "admin"],
            index=0 if (seed.get("role") or "user") == "user" else 1,
            key=f"usr_form_r__{sfx}",
        )

        st.markdown("")
        bcols = st.columns(3)
        cancel = bcols[0].button("Cancel", use_container_width=True,
                                 key=f"usr_cancel__{sfx}")
        save_label = "Create user" if is_new else "Save changes"
        save = bcols[1].button(save_label, type="primary",
                               use_container_width=True,
                               key=f"usr_save__{sfx}")
        delete = (bcols[2].button("Delete", use_container_width=True,
                                  key=f"usr_delete__{sfx}")
                  if not is_new else False)

    if cancel:
        st.session_state[EDIT_KEY] = None
        st.rerun()

    if save:
        new_u_s = new_u.strip()
        if not new_u_s:
            st.error("Username is required.")
            return
        if is_new and not new_p:
            st.error("A password is required for a new user.")
            return
        if is_new:
            if any(u["username"] == new_u_s for u in users):
                st.error(f"A user named '{new_u_s}' already exists.")
                return
            db.create_user(new_u_s, new_p, new_r)
            st.success(f"Created user '{new_u_s}'.")
        else:
            if (new_u_s != seed["username"]
                and any(u["username"] == new_u_s for u in users)):
                st.error(f"A user named '{new_u_s}' already exists.")
                return
            # Empty password field = keep the stored one.
            db.update_user(seed["id"], new_u_s, new_p or None, new_r)
            st.success(f"Updated user '{new_u_s}'."
                       + (" Password changed." if new_p else ""))
        st.session_state[EDIT_KEY] = None
        st.rerun()

    if delete:
        if seed["id"] == me["id"]:
            st.error("You cannot delete your own account while signed in.")
            return
        if (seed["role"] == "admin"
            and sum(1 for u in users if u["role"] == "admin") <= 1):
            st.error("Cannot delete the only remaining admin.")
            return
        db.delete_user(seed["id"])
        st.session_state[EDIT_KEY] = None
        st.success(f"Deleted user '{seed['username']}'.")
        st.rerun()
