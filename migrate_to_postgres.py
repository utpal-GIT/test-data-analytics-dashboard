"""
One-off copy of the local SQLite database into an external database.

Usage (PowerShell):

    $env:DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DB?sslmode=require"
    python migrate_to_postgres.py --dry-run
    python migrate_to_postgres.py

The URL is read from --url, then DATABASE_URL, then
.streamlit/secrets.toml. Nothing is written with --dry-run.

Users are matched by username. A user missing on the target is created; a
plaintext password is hashed on the way across. Samples and parameters are
only copied when the target user has none, unless --replace is given, so
running this twice cannot silently duplicate rows.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

import db
from passwords import hash_password, is_hashed


def _source_engine():
    if not db.DB_PATH.exists():
        sys.exit(f"No local database at {db.DB_PATH} - nothing to migrate.")
    return db.make_engine(None)


def _target_engine(url: str | None):
    url = url or db.database_url()
    if not url:
        sys.exit(
            "No target database URL. Pass --url, set DATABASE_URL, or add\n"
            "  [database]\n  url = \"postgresql://...\"\n"
            "to .streamlit/secrets.toml."
        )
    return db.make_engine(url)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="target database URL")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be copied, write nothing")
    ap.add_argument("--replace", action="store_true",
                    help="overwrite samples and parameters that already exist "
                         "on the target for a user")
    args = ap.parse_args()

    src = _source_engine()
    dst = _target_engine(args.url)
    print(f"source : {db.DB_PATH}")
    print(f"target : {dst.url.render_as_string(hide_password=True)}")

    with src.connect() as s:
        users = [dict(r) for r in s.execute(
            text("SELECT id, username, password, role FROM users ORDER BY id")
        ).mappings()]
        samples = [dict(r) for r in s.execute(
            text("SELECT * FROM samples ORDER BY id")).mappings()]
        params = [dict(r) for r in s.execute(
            text("SELECT * FROM parameters ORDER BY id")).mappings()]
    print(f"found  : {len(users)} users, {len(samples)} samples, "
          f"{len(params)} parameters")

    if args.dry_run:
        for u in users:
            n_s = sum(1 for r in samples if r["user_id"] == u["id"])
            n_p = sum(1 for r in params if r["user_id"] == u["id"])
            note = "" if is_hashed(u["password"]) else "  (password will be hashed)"
            print(f"  would copy {u['username']!r} [{u['role']}]: "
                  f"{n_s} samples, {n_p} parameters{note}")
        print("dry run - nothing written.")
        return

    db.init_db(dst)      # create tables on the target if they are missing

    copied_s = copied_p = skipped = 0
    with db.get_conn(dst) as c:
        for u in users:
            row = c.execute(text("SELECT id FROM users WHERE username = :u"),
                            {"u": u["username"]}).mappings().first()
            if row:
                new_id = row["id"]
                print(f"  user {u['username']!r}: already on target (id {new_id})")
            else:
                pwd = (u["password"] if is_hashed(u["password"])
                       else hash_password(u["password"] or ""))
                c.execute(
                    text("INSERT INTO users(username, password, role) "
                         "VALUES (:u, :p, :r)"),
                    {"u": u["username"], "p": pwd, "r": u["role"]},
                )
                new_id = c.execute(
                    text("SELECT id FROM users WHERE username = :u"),
                    {"u": u["username"]}).mappings().first()["id"]
                print(f"  user {u['username']!r}: created (id {new_id})")

            for table, rows_all, cols in (
                ("samples", samples,
                 ["parameter", "device_id", "sample_id", "reagent_lot",
                  "date", "age", "gender", "actual", "abs_value"]),
                ("parameters", params,
                 ["name", "normal_male", "normal_female", "detection", "clia"]),
            ):
                mine = [r for r in rows_all if r["user_id"] == u["id"]]
                if not mine:
                    continue
                have = c.execute(
                    text(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = :u"),
                    {"u": new_id}).mappings().first()["n"]
                if have and not args.replace:
                    print(f"    {table}: target already has {have} rows - "
                          f"skipped ({len(mine)} not copied; use --replace)")
                    skipped += len(mine)
                    continue
                if have:
                    c.execute(text(f"DELETE FROM {table} WHERE user_id = :u"),
                              {"u": new_id})
                payload = [
                    {"user_id": new_id, **{k: r.get(k) for k in cols}}
                    for r in mine
                ]
                target_t = db.samples_t if table == "samples" else db.parameters_t
                c.execute(target_t.insert(), payload)
                print(f"    {table}: copied {len(payload)} rows")
                if table == "samples":
                    copied_s += len(payload)
                else:
                    copied_p += len(payload)

    print(f"done   : {copied_s} samples, {copied_p} parameters copied"
          + (f", {skipped} skipped" if skipped else ""))


if __name__ == "__main__":
    main()
