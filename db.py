"""
Persistence layer (SQLAlchemy Core).

Two back ends, chosen by configuration, with identical behaviour:

  * no configuration  -> SQLite file `app_data.db` next to this module.
    This is the local default and needs no setup.
  * a connection URL  -> that database (Postgres in practice). Set it in
    `.streamlit/secrets.toml` as

        [database]
        url = "postgresql://USER:PASSWORD@HOST/DB?sslmode=require"

    or in the DATABASE_URL environment variable. Use this on Streamlit
    Cloud, whose filesystem is wiped on every restart, so that samples and
    parameter configurations survive.

All data and configurations are scoped per-user (user_id foreign key on
every domain row).

Tables:
  users       - id, username, password (PBKDF2 hash), role
  samples     - per-user patient rows (input + Actual + Abs)
  parameters  - per-user test-parameter configurations
  sessions    - login tokens, so a browser refresh keeps you signed in
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import streamlit as st
from sqlalchemy import (
    CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer,
    MetaData, Table, Text, UniqueConstraint, create_engine, func, text,
)

from passwords import hash_password, needs_rehash, verify_password

DB_PATH = Path(__file__).parent / "app_data.db"


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------
metadata = MetaData()

users_t = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", Text, nullable=False, unique=True),
    Column("password", Text, nullable=False),
    Column("role", Text, nullable=False),
    CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
)

samples_t = Table(
    "samples", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer,
           ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("parameter", Text),
    Column("device_id", Text),
    Column("sample_id", Text),
    Column("reagent_lot", Text),
    Column("date", Text),
    Column("age", Float),
    Column("gender", Text),
    Column("actual", Float),
    Column("abs_value", Float),
    Column("created_at", DateTime, server_default=func.now()),
    Index("idx_samples_user_param", "user_id", "parameter"),
)

sessions_t = Table(
    "sessions", metadata,
    Column("token", Text, primary_key=True),
    Column("user_id", Integer,
           ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("expires_at", Text, nullable=False),
)

parameters_t = Table(
    "parameters", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer,
           ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("name", Text, nullable=False),
    Column("normal_male", Text),      # JSON: {"low":x,"high":y}
    Column("normal_female", Text),    # JSON: {"low":x,"high":y}
    Column("detection", Text),        # JSON: {"low":x,"high":y}
    Column("clia", Text),             # JSON: see clia.py for schema
    UniqueConstraint("user_id", "name", name="uq_parameters_user_name"),
)


# ---------------------------------------------------------------------------
# engine / connection
# ---------------------------------------------------------------------------
def database_url() -> str | None:
    """Configured external database URL, or None to use the local SQLite file."""
    url = None
    try:
        cfg = st.secrets.get("database", None)
        if cfg:
            url = cfg.get("url")
        if not url:
            url = st.secrets.get("DATABASE_URL", None)
    except Exception:      # no secrets file, or running outside Streamlit
        url = None
    return (url or os.environ.get("DATABASE_URL") or "").strip() or None


def normalise_url(url: str) -> str:
    """Make a provider-supplied URL usable by SQLAlchemy.

    Providers hand out `postgres://`, which SQLAlchemy does not accept, and
    a hosted database should always be reached over TLS.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if not scheme.startswith(("postgres", "postgresql")):
        # Nothing to fix, and rebuilding a netloc-less URL such as
        # sqlite:///path would mangle it.
        return url
    if scheme == "postgres":
        scheme = "postgresql"
    if scheme == "postgresql":
        scheme = "postgresql+psycopg2"
    query = parts.query
    host = (parts.hostname or "").lower()
    if (scheme.startswith("postgresql")
            and "sslmode=" not in query
            and host not in ("localhost", "127.0.0.1", "::1")):
        query = f"{query}&sslmode=require" if query else "sslmode=require"
    return urlunsplit((scheme, parts.netloc, parts.path, query, parts.fragment))


def make_engine(url: str | None = None):
    """Build an engine for `url`, or for the local SQLite file when None."""
    if url:
        return create_engine(
            normalise_url(url),
            pool_pre_ping=True,     # a serverless database may have slept
            pool_recycle=300,
            future=True,
        )
    return create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},   # Streamlit reruns in threads
        future=True,
    )


@st.cache_resource(show_spinner=False)
def _engine():
    return make_engine(database_url())


def get_engine():
    try:
        return _engine()
    except Exception:       # outside a Streamlit runtime (scripts, tests)
        return make_engine(database_url())


def backend() -> str:
    """"postgresql" / "sqlite" - which back end is actually in use."""
    return get_engine().dialect.name


@contextmanager
def get_conn(engine=None):
    """A connection inside a transaction; commits on clean exit."""
    eng = engine or get_engine()
    with eng.begin() as conn:
        if conn.dialect.name == "sqlite":
            conn.exec_driver_sql("PRAGMA foreign_keys = ON")
        yield conn


@contextmanager
def read_conn(engine=None):
    """A connection for read-only work, in autocommit.

    A remote database charges a network round trip for BEGIN and for COMMIT
    as well as for the query itself, which trebles the cost of a single
    SELECT. Reads need no transaction, so they skip both.
    """
    eng = engine or get_engine()
    with eng.connect().execution_options(
            isolation_level="AUTOCOMMIT") as conn:
        yield conn


def _rows(result) -> list[dict]:
    return [dict(r) for r in result.mappings().all()]


@st.cache_resource(show_spinner=False)
def ensure_ready() -> bool:
    """Schema creation, the secrets user sync and the expired-session sweep,
    run ONCE per server process rather than on every rerun.

    Streamlit re-executes the whole script on every interaction; doing this
    work each time cost six round trips to a remote database before anything
    was drawn. Nothing here changes between reruns, so it is cached. An
    exception is not cached, so a failed connection is retried next rerun.
    """
    init_db()
    cleanup_expired_sessions()
    return True


def init_db(engine=None) -> None:
    """Create tables when missing and sync users from st.secrets."""
    eng = engine or get_engine()
    metadata.create_all(eng, checkfirst=True)

    # Sync users from st.secrets on every startup. This keeps logins working
    # on hosts whose filesystem is ephemeral, and is how the first admin is
    # created. Secrets format:
    #   [users.admin]
    #   password = "secure-pass"
    #   role = "admin"
    try:
        secrets_users = dict(st.secrets.get("users", {}))
    except Exception:
        secrets_users = {}

    with get_conn(eng) as c:
        if secrets_users:
            for uname, ucfg in secrets_users.items():
                uname = str(uname)
                upwd = str(ucfg.get("password", ""))
                urole = str(ucfg.get("role", "user"))
                if not upwd:
                    continue
                row = c.execute(
                    text("SELECT id, password FROM users WHERE username = :u"),
                    {"u": uname},
                ).mappings().first()
                if row:
                    # Only write when something actually changed, so a startup
                    # does not churn the row with a fresh salt every time -
                    # but do rewrite a row still held in the legacy plaintext
                    # form, so nothing readable is left behind.
                    if (not verify_password(upwd, row["password"])
                            or needs_rehash(row["password"])):
                        c.execute(
                            text("UPDATE users SET password = :p, role = :r "
                                 "WHERE id = :i"),
                            {"p": hash_password(upwd), "r": urole,
                             "i": row["id"]},
                        )
                    else:
                        c.execute(
                            text("UPDATE users SET role = :r WHERE id = :i"),
                            {"r": urole, "i": row["id"]},
                        )
                else:
                    c.execute(
                        text("INSERT INTO users(username, password, role) "
                             "VALUES (:u, :p, :r)"),
                        {"u": uname, "p": hash_password(upwd), "r": urole},
                    )
        else:
            # No secrets and no users yet: seed the default admin.
            n = c.execute(text("SELECT COUNT(*) AS n FROM users")
                          ).mappings().first()["n"]
            if not n:
                c.execute(
                    text("INSERT INTO users(username, password, role) "
                         "VALUES (:u, :p, :r)"),
                    {"u": "admin", "p": hash_password("admin"), "r": "admin"},
                )


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
def list_users() -> list[dict]:
    with read_conn() as c:
        return _rows(c.execute(
            text("SELECT id, username, password, role FROM users ORDER BY id")))


def get_user_by_credentials(username: str, password: str) -> dict | None:
    """Verify a login. Upgrades legacy plaintext rows to a hash on success."""
    with get_conn() as c:
        row = c.execute(
            text("SELECT id, username, password, role FROM users "
                 "WHERE username = :u"),
            {"u": username},
        ).mappings().first()
        if not row or not verify_password(password, row["password"]):
            return None
        user = dict(row)
        if needs_rehash(user["password"]):
            new_hash = hash_password(password)
            c.execute(text("UPDATE users SET password = :p WHERE id = :i"),
                      {"p": new_hash, "i": user["id"]})
            user["password"] = new_hash
    return user


def create_user(username: str, password: str, role: str) -> None:
    with get_conn() as c:
        c.execute(
            text("INSERT INTO users(username, password, role) "
                 "VALUES (:u, :p, :r)"),
            {"u": username, "p": hash_password(password), "r": role},
        )


def update_user(user_id: int, username: str,
                password: str | None, role: str) -> None:
    """Update a user. `password=None` (or empty) leaves it unchanged, since a
    hash cannot be read back to pre-fill an edit form."""
    with get_conn() as c:
        if password:
            c.execute(
                text("UPDATE users SET username = :u, password = :p, "
                     "role = :r WHERE id = :i"),
                {"u": username, "p": hash_password(password),
                 "r": role, "i": user_id},
            )
        else:
            c.execute(
                text("UPDATE users SET username = :u, role = :r WHERE id = :i"),
                {"u": username, "r": role, "i": user_id},
            )


def delete_user(user_id: int) -> None:
    with get_conn() as c:
        c.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
    _invalidate_parameters()      # their configurations cascade away


# ---------------------------------------------------------------------------
# samples
# ---------------------------------------------------------------------------
SAMPLE_COLS = [
    "device_id", "sample_id", "reagent_lot", "date",
    "age", "gender", "actual", "abs_value",
]


def load_samples(user_id: int, parameter: str | None = None) -> list[dict]:
    with read_conn() as c:
        if parameter:
            return _rows(c.execute(
                text("SELECT * FROM samples WHERE user_id = :u "
                     "AND parameter = :p ORDER BY id"),
                {"u": user_id, "p": parameter}))
        return _rows(c.execute(
            text("SELECT * FROM samples WHERE user_id = :u ORDER BY id"),
            {"u": user_id}))


def _sample_payload(user_id: int, parameter: str | None, r: dict) -> dict:
    return {
        "user_id": user_id,
        "parameter": parameter,
        "device_id": r.get("device_id"),
        "sample_id": r.get("sample_id"),
        "reagent_lot": r.get("reagent_lot"),
        "date": r.get("date"),
        "age": _to_float(r.get("age")),
        "gender": r.get("gender"),
        "actual": _to_float(r.get("actual")),
        "abs_value": _to_float(r.get("abs_value")),
    }


def replace_samples(user_id: int, parameter: str, rows: Iterable[dict]) -> None:
    """Replace all samples for (user_id, parameter) with the given rows."""
    payload = [_sample_payload(user_id, parameter, r) for r in rows]
    with get_conn() as c:
        c.execute(
            text("DELETE FROM samples WHERE user_id = :u AND parameter = :p"),
            {"u": user_id, "p": parameter},
        )
        if payload:
            c.execute(samples_t.insert(), payload)


def load_all_samples(user_id: int) -> list[dict]:
    """All samples across every parameter for this user."""
    with read_conn() as c:
        return _rows(c.execute(
            text("SELECT * FROM samples WHERE user_id = :u ORDER BY id"),
            {"u": user_id}))


def replace_all_samples(user_id: int, rows: Iterable[dict]) -> None:
    """Replace ALL samples for user_id with the given rows.

    Each row dict may include 'parameter' (may be empty)."""
    payload = [
        _sample_payload(user_id, (r.get("parameter") or "").strip() or None, r)
        for r in rows
    ]
    with get_conn() as c:
        c.execute(text("DELETE FROM samples WHERE user_id = :u"), {"u": user_id})
        if payload:
            c.execute(samples_t.insert(), payload)


# ---------------------------------------------------------------------------
# parameters / configurations
# ---------------------------------------------------------------------------
def list_parameters(user_id: int) -> list[dict]:
    with read_conn() as c:
        rows = _rows(c.execute(
            text("SELECT * FROM parameters WHERE user_id = :u ORDER BY name"),
            {"u": user_id}))
    return [_decode_param(r) for r in rows]


@st.cache_data(show_spinner=False)
def parameters_for(user_id: int) -> list[dict]:
    """list_parameters() with the result cached until a configuration is
    written. Every rerun needs this list, and it changes only when the
    Configurations tab saves or deletes something."""
    return list_parameters(user_id)


def _invalidate_parameters() -> None:
    try:
        parameters_for.clear()
    except Exception:       # outside a Streamlit runtime
        pass


def get_parameter(user_id: int, name: str) -> dict | None:
    with read_conn() as c:
        row = c.execute(
            text("SELECT * FROM parameters WHERE user_id = :u AND name = :n"),
            {"u": user_id, "n": name},
        ).mappings().first()
    return _decode_param(dict(row)) if row else None


def upsert_parameter(user_id: int, cfg: dict) -> None:
    # ON CONFLICT ... DO UPDATE with `excluded` is understood by both SQLite
    # (3.24+) and Postgres (9.5+), so one statement serves both back ends.
    with get_conn() as c:
        c.execute(
            text("""INSERT INTO parameters
                      (user_id, name, normal_male, normal_female,
                       detection, clia)
                    VALUES (:u, :n, :nm, :nf, :det, :clia)
                    ON CONFLICT (user_id, name) DO UPDATE SET
                      normal_male   = excluded.normal_male,
                      normal_female = excluded.normal_female,
                      detection     = excluded.detection,
                      clia          = excluded.clia"""),
            {
                "u": user_id,
                "n": cfg["name"],
                "nm": json.dumps(cfg.get("normal_male") or {}),
                "nf": json.dumps(cfg.get("normal_female") or {}),
                "det": json.dumps(cfg.get("detection") or {}),
                "clia": json.dumps(cfg.get("clia") or {}),
            },
        )
    _invalidate_parameters()


def delete_parameter(user_id: int, name: str) -> None:
    with get_conn() as c:
        c.execute(
            text("DELETE FROM parameters WHERE user_id = :u AND name = :n"),
            {"u": user_id, "n": name},
        )
    _invalidate_parameters()


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
            text("INSERT INTO sessions(token, user_id, expires_at) "
                 "VALUES (:t, :u, :e)"),
            {"t": token, "u": user_id, "e": expires},
        )
    return token


def get_session(token: str | None) -> dict | None:
    """Return session row joined with user, or None if missing/expired.
    Auto-deletes the row if expired."""
    if not token:
        return None
    with get_conn() as c:
        row = c.execute(
            text("SELECT s.token, s.user_id, s.expires_at, "
                 "       u.username, u.password, u.role "
                 "FROM sessions s JOIN users u ON u.id = s.user_id "
                 "WHERE s.token = :t"),
            {"t": token},
        ).mappings().first()
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
        c.execute(text("DELETE FROM sessions WHERE token = :t"), {"t": token})


def cleanup_expired_sessions() -> None:
    with get_conn() as c:
        c.execute(text("DELETE FROM sessions WHERE expires_at < :now"),
                  {"now": _utcnow().isoformat()})
