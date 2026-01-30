#!/usr/bin/env python3
# deduplicate_product_ids.py

import os
import sys
from pathlib import Path

# ---------- Safe psycopg2 import with helpful error ----------
try:
    import psycopg2
except ImportError as exc:
    print(
        "\n[ERROR] Could not import 'psycopg2'.\n"
        "Make sure it is installed in THIS environment.\n\n"
        "If you are in your .venv, run:\n"
        "    python -m pip install psycopg2-binary\n\n"
        "Then run this script with:\n"
        "    python deduplicate_product_ids.py\n"
    )
    raise

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
from logs.logging_config import get_rotating_logger

# Logger for product_id duplicate cleanup
product_logger = get_rotating_logger(
    name="product_id_dedup",
    folder_name="product_id_dedup",
)


# ---------- DB CONNECTION ----------

def get_db_connection():
    """
    Connect to PostgreSQL using DATABASE_URL only.
    For safety, autocommit is disabled so we can rollback on error.
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        msg = "DATABASE_URL is not set. Cannot run product_id deduplication."
        print(msg)
        product_logger.error(msg)
        raise RuntimeError(msg)

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = False  # explicit transaction control for safe deletes
        product_logger.info("Connected to PostgreSQL.")
        return conn
    except Exception as exc:
        msg = f"Failed to connect to PostgreSQL: {exc}"
        print(msg)
        product_logger.exception(msg)
        raise


# ---------- DUPLICATE INSPECTION ----------

def log_duplicate_product_ids(conn, prefix_msg: str = "duplicate product_id groups"):
    """
    - Extract product_id from the metadata inside 'description'
    - Group duplicates
    - Log them (audit only, no deletion here)
    Returns number of duplicate groups.
    """
    query = """
        WITH extracted AS (
            SELECT
                id,
                title,
                (regexp_match(description, '"product_id":"([^"]+)"'))[1] AS product_id
            FROM public.items
        )
        SELECT
            product_id,
            array_agg(id) AS ids,
            array_agg(title) AS titles,
            COUNT(*) AS cnt
        FROM extracted
        WHERE product_id IS NOT NULL
        GROUP BY product_id
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC;
    """

    with conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()

    if not rows:
        msg = f"No {prefix_msg} found in the items table."
        print(msg)
        product_logger.info(msg)
        return 0

    header = f"Found {len(rows)} {prefix_msg}:"
    print(header)
    product_logger.warning(header)

    for product_id, ids, titles, cnt in rows:
        msg = (
            f"\n[Duplicate] product_id='{product_id}' appears {cnt} times\n"
            f"  IDs: {ids}\n"
            f"  Titles: {titles}\n"
        )
        print(msg)
        product_logger.warning(msg)

    return len(rows)


# ---------- DEDUPLICATION (DELETE SECOND+ OCCURRENCES) ----------

def delete_duplicate_product_ids(conn):
    """
    Delete all but the first item per product_id:
    - "First" = earliest created_at
      (if created_at ties, smallest id)
    - Only rows with duplicate product_id (extracted from description) are touched.
    - Logs how many rows were deleted and their IDs.
    """

    delete_query = """
        WITH extracted AS (
            SELECT
                id,
                title,
                created_at,
                (regexp_match(description, '"product_id":"([^"]+)"'))[1] AS product_id
            FROM public.items
        ),
        ranked AS (
            SELECT
                id,
                title,
                product_id,
                created_at,
                ROW_NUMBER() OVER (
                    PARTITION BY product_id
                    ORDER BY created_at ASC, id ASC
                ) AS rn
            FROM extracted
            WHERE product_id IS NOT NULL
        ),
        to_delete AS (
            SELECT id
            FROM ranked
            WHERE rn > 1
        )
        DELETE FROM public.items i
        USING to_delete td
        WHERE i.id = td.id
        RETURNING i.id;
    """

    with conn.cursor() as cur:
        cur.execute(delete_query)
        deleted_rows = cur.fetchall()  # each row is (id,)

    deleted_count = len(deleted_rows)
    if deleted_count == 0:
        msg = "No duplicate rows were deleted (nothing to clean up)."
        print(msg)
        product_logger.info(msg)
    else:
        ids_deleted = [row[0] for row in deleted_rows]
        msg = f"Deleted {deleted_count} duplicate item rows (second+ occurrences per product_id)."
        print(msg)
        product_logger.warning(msg)
        product_logger.warning(f"Deleted IDs: {ids_deleted}")

    return deleted_count


# ---------- MAIN ----------

if __name__ == "__main__":
    print("Running product_id deduplication (check + delete duplicates)...")
    product_logger.info("Starting product_id deduplication run.")

    conn = get_db_connection()

    try:
        # 1) Log duplicates BEFORE deletion (pure audit, like your checker script)
        duplicate_groups_before = log_duplicate_product_ids(
            conn, prefix_msg="duplicate product_id groups (before deletion)"
        )

        if duplicate_groups_before == 0:
            # Nothing to delete
            conn.rollback()
            product_logger.info("No duplicates found. Exiting without changes.")
            print("No duplicates found. Nothing to delete.")
        else:
            # 2) Delete second+ occurrences
            deleted_count = delete_duplicate_product_ids(conn)

            # 3) Commit changes if deletion happened
            conn.commit()
            product_logger.info(
                f"Deduplication committed. Groups before: {duplicate_groups_before}, "
                f"rows deleted: {deleted_count}"
            )

            # 4) Re-check duplicates AFTER deletion
            print("\nRe-checking duplicates after deletion...")
            product_logger.info("Re-checking duplicates after deletion.")
            remaining_groups = log_duplicate_product_ids(
                conn, prefix_msg="duplicate product_id groups (after deletion)"
            )
            product_logger.info(f"Remaining duplicate groups after cleanup: {remaining_groups}")

    except Exception as exc:
        conn.rollback()
        product_logger.exception("Error during product_id deduplication, rolled back transaction: %s", exc)
        print(f"Error during deduplication: {exc}. All changes rolled back.")
    finally:
        conn.close()
        product_logger.info("Closed DB connection.")
        print("Deduplication run completed.")
