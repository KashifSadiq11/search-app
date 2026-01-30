#!/usr/bin/env python3
"""
Count how many BuyPass products (by productId) are NOT yet in the items table.

ASSUMPTION:
- In your rec-engine DB, items.id is set to BuyPass `productId`
  like: "67975a689f1efc1309a1bb9c".

BEHAVIOR:
- Reads BuyPASS.algolio_products.json
- Collects distinct productId values
- Loads all item IDs from the items table
- Computes how many productIds are missing in DB
- Prints and logs the counts

This script is READ-ONLY for the DB (no inserts, updates, deletes).
"""

import sys
import json
from pathlib import Path
from typing import Set, Tuple

# -------------------------
# Resolve project root
# -------------------------
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# -------------------------
# Imports from project
# -------------------------
from logs.logging_config import get_rotating_logger  # type: ignore

from services.engine.database import get_db          # type: ignore
from services.engine.models import Item              # type: ignore

# -------------------------
# Config
# -------------------------
DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.algolio_products.json"

logger = get_rotating_logger(
    name="count_missing_buypass_items",
    folder_name="count_missing_buypass_items",
)


# -------------------------
# Helper functions
# -------------------------
def load_json_product_ids(path: Path) -> Tuple[Set[str], int, int, int]:
    """
    Load distinct productId values from the JSON file.

    Returns:
        (product_ids_set, total_rows, rows_missing_productId, duplicate_rows)
    """
    with path.open("r", encoding="utf-8") as f:
        products = json.load(f)

    total_rows = len(products)
    product_ids: Set[str] = set()
    missing_productid_rows = 0
    duplicate_rows = 0

    seen: Set[str] = set()

    for p in products:
        raw_pid = p.get("productId") or ""
        pid = raw_pid.strip()

        if not pid:
            missing_productid_rows += 1
            continue

        if pid in seen:
            duplicate_rows += 1
        else:
            seen.add(pid)
            product_ids.add(pid)

    logger.info(
        "JSON summary | total_rows=%s | distinct_productIds=%s | "
        "rows_missing_productId=%s | duplicate_rows=%s",
        total_rows,
        len(product_ids),
        missing_productid_rows,
        duplicate_rows,
    )

    return product_ids, total_rows, missing_productid_rows, duplicate_rows


def load_db_item_ids() -> Set[str]:
    """
    Load all item IDs from the items table.

    Assumes Item.id is the BuyPass productId.
    """
    db = next(get_db())
    rows = db.query(Item.id).all()
    ids = {row[0] for row in rows}

    logger.info("DB summary | item_ids_in_db=%s", len(ids))
    return ids


# -------------------------
# Main
# -------------------------
def main() -> None:
    if not DATA_FILE.exists():
        msg = f"Data file not found: {DATA_FILE}"
        print(msg)
        logger.error(msg)
        raise SystemExit(1)

    print(f"Using data file: {DATA_FILE}")
    logger.info("Using data file: %s", DATA_FILE)

    # JSON side
    json_product_ids, total_rows, missing_pid_rows, duplicate_rows = load_json_product_ids(DATA_FILE)

    # DB side
    db_item_ids = load_db_item_ids()

    # Compute missing productIds (present in JSON but not in DB)
    missing_ids = json_product_ids - db_item_ids
    missing_count = len(missing_ids)

    print("\n==============================")
    print(" BuyPass → RecEngine Sync Info")
    print("==============================")
    print(f"Total JSON rows                 : {total_rows}")
    print(f"Rows missing productId in JSON  : {missing_pid_rows}")
    print(f"Distinct productIds in JSON     : {len(json_product_ids)}")
    print(f"Distinct item.id values in DB   : {len(db_item_ids)}")
    print("--------------------------------")
    print(f"productIds NOT present in DB    : {missing_count}")
    print("  (i.e. this many items would need to be inserted)")

    logger.info(
        "Missing products report | total_json_rows=%s | distinct_productIds=%s | "
        "db_item_ids=%s | missing_productIds=%s",
        total_rows,
        len(json_product_ids),
        len(db_item_ids),
        missing_count,
    )

    # Show a few examples to sanity-check
    if missing_ids:
        example_list = list(missing_ids)[:10]
        print("\nExample missing productIds (up to 10):")
        for pid in example_list:
            print(f"  - {pid}")
        logger.info("Example missing productIds: %s", example_list)


if __name__ == "__main__":
    main()
