"""
SQLite persistence layer.

All data and configurations are scoped per-user (user_id foreign key on every
domain row). Admin can read all users' rows from the User Management tab.

Tables:
  users       - id, username, password (plaintext per spec so admin can view), role
  samples     - per-user patient rows (input + Actual + Abs)
  parameters  - per-user test-parameter configurations (CLIA, normal, detection)
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).parent / "app_data.db"


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and seed a default admin if the users table is empty."""
    with get_conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT NOT NULL UNIQUE,
                password  TEXT NOT NULL,
                role      TEXT NOT NULL CHECK (role IN ('admin', 'user'))
            );

            CREATE TABLE IF NOT EXISTS samples (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                parameter  TEXT,
                device_id  TEXT,
                sample_id  TEXT,
                reagent_lot TEXT,
                date       TEXT,
                age        REAL,
                gender     TEXT,
                actual     REAL,
                abs_value  REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_samples_user_param
                ON samples(user_id, parameter);

            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS parameters (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name          TEXT NOT NULL,
                normal_male   TEXT,        -- JSON: {"low":x,"high":y}
                normal_female TEXT,        -- JSON: {"low":x,"high":y}
                detection     TEXT,        -- JSON: {"low":x,"high":y}
                clia          TEXT,        -- JSON: see clia.py for schema
                UNIQUE(user_id, name)
            );
            """
        )
        # seed default admin if no users exist
        cur = c.execute("SELECT COUNT(*) AS n FROM users")
        if cur.fetchone()["n"] == 0:
            c.execute(
                "INSERT INTO users(username, password, role) VALUES (?,?,?)",
                ("admin", "admin", "admin"),
            )


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
def list_users() -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, username, password, role FROM users ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_user_by_credentials(username: str, password: str) -> dict | None:
    with get_conn() as c:
        row = c.execute(
            "SELECT id, username, password, role FROM users "
            "WHERE username = ? AND password = ?",
            (username, password),
        ).fetchone()
    return dict(row) if row else None


def create_user(username: str, password: str, role: str) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO users(username, password, role) VALUES (?,?,?)",
            (username, password, role),
        )


def update_user(user_id: int, username: str, password: str, role: str) -> None:
    with get_conn() as c:
        c.execute(
            "UPDATE users SET username=?, password=?, role=? WHERE id=?",
            (username, password, role, user_id),
        )


def delete_user(user_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM users WHERE id=?", (user_id,))


# ---------------------------------------------------------------------------
# samples
# ---------------------------------------------------------------------------
SAMPLE_COLS = [
    "device_id", "sample_id", "reagent_lot", "date",
    "age", "gender", "actual", "abs_value",
]


def load_samples(user_id: int, parameter: str | None = None) -> list[dict]:
    with get_conn() as c:
        if parameter:
            rows = c.execute(
                "SELECT * FROM samples WHERE user_id=? AND parameter=? ORDER BY id",
                (user_id, parameter),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM samples WHERE user_id=? ORDER BY id",
                (user_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def replace_samples(user_id: int, parameter: str, rows: Iterable[dict]) -> None:
    """Replace all samples for (user_id, parameter) with the given rows."""
    with get_conn() as c:
        c.execute(
            "DELETE FROM samples WHERE user_id=? AND parameter=?",
            (user_id, parameter),
        )
        for r in rows:
            c.execute(
                """INSERT INTO samples
                    (user_id, parameter, device_id, sample_id, reagent_lot,
                     date, age, gender, actual, abs_value)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, parameter,
                    r.get("device_id"), r.get("sample_id"),
                    r.get("reagent_lot"), r.get("date"),
                    _to_float(r.get("age")), r.get("gender"),
                    _to_float(r.get("actual")), _to_float(r.get("abs_value")),
                ),
            )


# ---------------------------------------------------------------------------
# parameters / configurations
# ---------------------------------------------------------------------------
def list_parameters(user_id: int) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM parameters WHERE user_id=? ORDER BY name", (user_id,)
        ).fetchall()
    return [_decode_param(dict(r)) for r in rows]


def get_parameter(user_id: int, name: str) -> dict | None:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM parameters WHERE user_id=? AND name=?", (user_id, name)
        ).fetchone()
    return _decode_param(dict(row)) if row else None


def upsert_parameter(user_id: int, cfg: dict) -> None:
    payload = (
        user_id,
        cfg["name"],
        json.dumps(cfg.get("normal_male") or {}),
        json.dumps(cfg.get("normal_female") or {}),
        json.dumps(cfg.get("detection") or {}),
        json.dumps(cfg.get("clia") or {}),
    )
    with get_conn() as c:
        c.execute(
            """INSERT INTO parameters
                (user_id, name, normal_male, normal_female, detection, clia)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(user_id, name) DO UPDATE SET
                 normal_male  = excluded.normal_male,
                 normal_female= excluded.normal_female,
                 detection    = excluded.detection,
                 clia         = excluded.clia""",
            payload,
        )


def delete_parameter(user_id: int, name: str) -> None:
    with get_conn() as c:
        c.execute(
            "DELETE FROM parameters WHERE user_id=? AND name=?", (user_id, name)
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _decode_param(row: dict) -> dict:
    for k in ("normal_male", "normal_female", "detection", "clia"):
        try:
            row[k] = json.loads(row.get(k) or "{}")
        except json.JSONDecodeError:
            row[k] = {}
    return row


# ---------------------------------------------------------------------------
# sessions (persistent login across browser reloads)
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_session(user_id: int, hours: int = 3) -> str:
    token = secrets.token_urlsafe(24)
    expires = (_utcnow() + timedelta(hours=hours)).isoformat()
    with get_conn() as c:
        c.execute(
            "INSERT INTO sessions(token, user_id, expires_at) VALUES (?,?,?)",
            (token, user_id, expires),
        )
    return token


def get_session(token: str | None) -> dict | None:
    """Return session row joined with user, or None if missing/expired.
    Auto-deletes the row if expired."""
    if not token:
        return None
    with get_conn() as c:
        row = c.execute(
            "SELECT s.token, s.user_id, s.expires_at, "
            "       u.username, u.password, u.role "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ?",
            (token,),
        ).fetchone()
    if not row:
        return None
    try:
        exp = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        delete_session(token)
        return None
    if exp < _utcnow():
        delete_session(token)
        return None
    return dict(row)


def delete_session(token: str | None) -> None:
    if not token:
        return
    with get_conn() as c:
        c.execute("DELETE FROM sessions WHERE token = ?", (token,))


def cleanup_expired_sessions() -> None:
    with get_conn() as c:
        c.execute(
            "DELETE FROM sessions WHERE expires_at < ?",
            (_utcnow().isoformat(),),
        )


# ---------------------------------------------------------------------------
# multi-parameter sample helpers (used when the grid mixes parameters)
# ---------------------------------------------------------------------------
def load_all_samples(user_id: int) -> list[dict]:
    """All samples across every parameter for this user."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM samples WHERE user_id=? ORDER BY id",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def replace_all_samples(user_id: int, rows: Iterable[dict]) -> None:
    """Replace ALL samples for user_id with the given rows.

    Each row dict must include 'parameter' (may be empty string)."""
    with get_conn() as c:
        c.execute("DELETE FROM samples WHERE user_id=?", (user_id,))
        for r in rows:
            c.execute(
                """INSERT INTO samples
                    (user_id, parameter, device_id, sample_id, reagent_lot,
                     date, age, gender, actual, abs_value)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    (r.get("parameter") or "").strip() or None,
                    r.get("device_id"), r.get("sample_id"),
                    r.get("reagent_lot"), r.get("date"),
                    _to_float(r.get("age")), r.get("gender"),
                    _to_float(r.get("actual")), _to_float(r.get("abs_value")),
                ),
            )
