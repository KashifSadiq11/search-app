#!/usr/bin/env python3
"""
Audit RecEngine `users` table for duplicate `id`, `username`, or `email`.

SAFETY:
- READ-ONLY: This script never INSERTs, UPDATEs, or DELETEs.
- It only runs SELECT queries and prints a summary.
- Uses the same POSTGRES_* environment variables as the rest of the project.

CHECKS:
1) Duplicate primary key `id`
2) Duplicate `email` (raw)
3) Duplicate `email` (case-insensitive: lower(email))
4) Duplicate `username` (raw)
5) Duplicate `username` (case-insensitive: lower(username))
"""

import os
import sys
from pathlib import Path
from typing import List, Dict

import psycopg2
from psycopg2.extras import RealDictCursor

# Optional: load .env if python-dotenv is installed
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# -----------------------------
# Project root & sys.path
# -----------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

# Load environment variables from .env at project root (if available)
if load_dotenv is not None:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

from logs.logging_config import get_rotating_logger  # type: ignore

logger = get_rotating_logger(
    name="audit_users_duplicates",
    folder_name="audit_users_duplicates",
)

# -----------------------------
# DB config
# -----------------------------

DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")

if not all([DB_NAME, DB_USER, DB_PASSWORD]):
    raise RuntimeError(
        "Database configuration missing. Please set POSTGRES_DB, "
        "POSTGRES_USER, POSTGRES_PASSWORD in your .env."
    )


def get_connection():
    """Create a new PostgreSQL connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


# -----------------------------
# Helper: find duplicates
# -----------------------------

def find_duplicates(
    cur,
    column_name: str,
    normalized: bool = False,
) -> List[Dict]:
    """
    Return all values of `column_name` that appear more than once.

    If normalized=True, we group by lower(column_name).
    """
    if normalized:
        select_expr = f"lower({column_name})"
        label = f"{column_name}_normalized"
    else:
        select_expr = column_name
        label = column_name

    query = f"""
        SELECT
            {select_expr} AS {label},
            COUNT(*) AS count
        FROM users
        WHERE {column_name} IS NOT NULL
        GROUP BY {select_expr}
        HAVING COUNT(*) > 1
        ORDER BY count DESC, {label} ASC;
    """

    cur.execute(query)
    return cur.fetchall()


def count_total_users(cur) -> int:
    cur.execute("SELECT COUNT(*) AS total FROM users;")
    row = cur.fetchone()
    return int(row["total"]) if row and "total" in row else 0


# -----------------------------
# Main audit logic
# -----------------------------

def run_audit() -> None:
    logger.info("Starting users duplicate audit")

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            total_users = count_total_users(cur)
            print("=" * 60)
            print("Users Duplicate Audit (READ-ONLY)")
            print("=" * 60)
            print(f"Total rows in users table: {total_users}")
            print("-" * 60)

            # 1) Duplicate IDs (should not happen if PK is enforced correctly)
            cur.execute("""
                SELECT id, COUNT(*) AS count
                FROM users
                GROUP BY id
                HAVING COUNT(*) > 1
                ORDER BY count DESC, id ASC;
            """)
            dup_ids = cur.fetchall()

            print("1) Duplicate `id` values (primary key):")
            if not dup_ids:
                print("   ✅ No duplicate IDs found.")
            else:
                print(f"   ❌ Found {len(dup_ids)} duplicate id value(s).")
                for row in dup_ids[:20]:
                    print(f"      id={row['id']} | count={row['count']}")
                if len(dup_ids) > 20:
                    print(f"      ... and {len(dup_ids) - 20} more")
            print("-" * 60)

            # 2) Duplicate raw emails
            dup_email_raw = find_duplicates(cur, "email", normalized=False)
            print("2) Duplicate `email` values (raw, case-sensitive):")
            if not dup_email_raw:
                print("   ✅ No duplicate raw emails found.")
            else:
                print(f"   ❌ Found {len(dup_email_raw)} duplicate raw email value(s).")
                for row in dup_email_raw[:20]:
                    print(f"      email={row['email']} | count={row['count']}")
                if len(dup_email_raw) > 20:
                    print(f"      ... and {len(dup_email_raw) - 20} more")
            print("-" * 60)

            # 3) Duplicate normalized emails (lowercase)
            dup_email_norm = find_duplicates(cur, "email", normalized=True)
            print("3) Duplicate `email` values (case-insensitive, lower(email)):")
            if not dup_email_norm:
                print("   ✅ No duplicate normalized emails found.")
            else:
                print(f"   ❌ Found {len(dup_email_norm)} duplicate normalized email value(s).")
                for row in dup_email_norm[:20]:
                    # label for normalized emails is "email_normalized"
                    email_val = row.get("email_normalized")
                    print(f"      email={email_val} | count={row['count']}")
                if len(dup_email_norm) > 20:
                    print(f"      ... and {len(dup_email_norm) - 20} more")
            print("-" * 60)

            # 4) Duplicate raw usernames
            dup_username_raw = find_duplicates(cur, "username", normalized=False)
            print("4) Duplicate `username` values (raw, case-sensitive):")
            if not dup_username_raw:
                print("   ✅ No duplicate raw usernames found.")
            else:
                print(f"   ❌ Found {len(dup_username_raw)} duplicate raw username value(s).")
                for row in dup_username_raw[:20]:
                    print(f"      username={row['username']} | count={row['count']}")
                if len(dup_username_raw) > 20:
                    print(f"      ... and {len(dup_username_raw) - 20} more")
            print("-" * 60)

            # 5) Duplicate normalized usernames (lowercase)
            dup_username_norm = find_duplicates(cur, "username", normalized=True)
            print("5) Duplicate `username` values (case-insensitive, lower(username)):")
            if not dup_username_norm:
                print("   ✅ No duplicate normalized usernames found.")
            else:
                print(f"   ❌ Found {len(dup_username_norm)} duplicate normalized username value(s).")
                for row in dup_username_norm[:20]:
                    username_val = row.get("username_normalized")
                    print(f"      username={username_val} | count={row['count']}")
                if len(dup_username_norm) > 20:
                    print(f"      ... and {len(dup_username_norm) - 20} more")
            print("-" * 60)

            print("Audit completed (no changes were made to the database).")
            logger.info(
                "Users duplicate audit finished | total_users=%s | dup_ids=%s | "
                "dup_email_raw=%s | dup_email_norm=%s | dup_username_raw=%s | dup_username_norm=%s",
                total_users,
                len(dup_ids),
                len(dup_email_raw),
                len(dup_email_norm),
                len(dup_username_raw),
                len(dup_username_norm),
            )

    finally:
        conn.close()
        logger.info("Closed DB connection for users duplicate audit")


if __name__ == "__main__":
    run_audit()
