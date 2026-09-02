"""
Password hashing.

Stored form:  pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>

PBKDF2-HMAC-SHA256 from the standard library, so there is no extra
dependency to install on the host. Rows written before hashing was
introduced hold the password in plain text; `verify_password` still
accepts those so nobody is locked out, and `needs_rehash` reports them so
the caller can upgrade the row on the next successful login.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

ALGO = "pbkdf2_sha256"
ITERATIONS = 240_000
SALT_BYTES = 16


def hash_password(raw: str) -> str:
    """Return the stored form for a new password."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", (raw or "").encode("utf-8"),
                                 salt, ITERATIONS)
    return f"{ALGO}${ITERATIONS}${salt.hex()}${digest.hex()}"


def is_hashed(stored: str | None) -> bool:
    return bool(stored) and str(stored).startswith(f"{ALGO}$")


def verify_password(raw: str, stored: str | None) -> bool:
    """True when `raw` matches the stored password (hashed or legacy plain)."""
    if not stored:
        return False
    stored = str(stored)
    if not is_hashed(stored):
        # Legacy plaintext row.
        return hmac.compare_digest(raw or "", stored)
    try:
        _algo, iters, salt_hex, digest_hex = stored.split("$", 3)
        digest = hashlib.pbkdf2_hmac(
            "sha256", (raw or "").encode("utf-8"),
            bytes.fromhex(salt_hex), int(iters),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def needs_rehash(stored: str | None) -> bool:
    """True when the stored form should be replaced on next successful login
    (plaintext, or hashed with a weaker iteration count than we use now)."""
    if not is_hashed(stored):
        return True
    try:
        iters = int(str(stored).split("$", 3)[1])
    except (ValueError, IndexError):
        return True
    return iters < ITERATIONS
