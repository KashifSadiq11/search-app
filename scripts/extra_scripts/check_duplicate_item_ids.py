#!/usr/bin/env python3
"""
Check for duplicate item IDs in the items table.

Pure audit:
- Groups by items.id
- Finds any id that appears more than once
- Logs and prints the duplicates
"""

import os
import sys
from pathlib import Path

import psycopg2

# Optional .env support
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# ---------- Setup Project Root ----------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env if exists
if load_dotenv is not None:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

# Logging
from logs.logging_config import get_rotating_logger  # type: ignore

item_logger = get_rotating_logger(
    name="duplicate_item_id_check",
    folder_name="duplicate_item_id_check",
)


# ---------- DB CONNECTION ----------

def get_db_connection():
    """Connect to PostgreSQL using DATABASE_URL only."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        msg = "DATABASE_URL is not set. Cannot run duplicate item id check."
        print(msg)
        item_logger.error(msg)
        raise RuntimeError(msg)

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        item_logger.info("Connected to PostgreSQL.")
        return conn
    except Exception as exc:
        msg = f"Failed to connect to PostgreSQL: {exc}"
        print(msg)
        item_logger.exception(msg)
        raise


# ---------- DUPLICATE CHECK FUNCTION ----------

def check_duplicate_item_ids(conn):
    """
    Pure audit:
    - Group by items.id
    - Any id with COUNT(*) > 1 is a duplicate
    """
    query = """
        SELECT
            id,
            array_agg(title) AS titles,
            COUNT(*) AS cnt
        FROM public.items
        GROUP BY id
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC;
    """

    try:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

        if not rows:
            msg = "No duplicate item.id values found in the items table."
            print(msg)
            item_logger.info(msg)
            return

        header = f"Found {len(rows)} duplicate item.id groups:"
        print(header)
        item_logger.warning(header)

        for item_id, titles, cnt in rows:
            msg = (
                f"\n[Duplicate] id='{item_id}' appears {cnt} times\n"
                f"  Titles: {titles}\n"
            )
            print(msg)
            item_logger.warning(msg)

    except Exception as exc:
        item_logger.exception("Error while running duplicate item id check: %s", exc)
        raise


# ---------- MAIN ----------

if __name__ == "__main__":
    print("Running duplicate item.id checker...")
    item_logger.info("Starting duplicate item.id checker.")

    conn = get_db_connection()

    try:
        check_duplicate_item_ids(conn)
    finally:
        conn.close()
        item_logger.info("Closed DB connection.")
        print("Check completed.")
