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
from logs.logging_config import get_rotating_logger

# Logger for product_id duplicate check
product_logger = get_rotating_logger(
    name="product_id_similarity_check",
    folder_name="product_id_similarity_check",
)


# ---------- DB CONNECTION ----------

def get_db_connection():
    """Connect to PostgreSQL using DATABASE_URL only."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        msg = "DATABASE_URL is not set. Cannot run product_id duplicate check."
        print(msg)
        product_logger.error(msg)
        raise RuntimeError(msg)

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        product_logger.info("Connected to PostgreSQL.")
        return conn
    except Exception as exc:
        msg = f"Failed to connect to PostgreSQL: {exc}"
        print(msg)
        product_logger.exception(msg)
        raise


# ---------- DUPLICATE CHECK FUNCTION ----------

def check_duplicate_product_ids(conn):
    """
    Pure audit function:
    - Extract product_id from the metadata inside 'description'
    - Group duplicates
    - Log them
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

    try:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

        if not rows:
            msg = "No duplicate product_id found in the items table."
            print(msg)
            product_logger.info(msg)
            return

        header = f"Found {len(rows)} duplicate product_id groups:"
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

    except Exception as exc:
        product_logger.exception("Error while running product_id duplicate check: %s", exc)
        raise


# ---------- MAIN ----------

if __name__ == "__main__":
    print("Running product_id duplicate checker...")
    product_logger.info("Starting product_id duplicate checker.")

    conn = get_db_connection()

    try:
        check_duplicate_product_ids(conn)
    finally:
        conn.close()
        product_logger.info("Closed DB connection.")
        print("Check completed.")
