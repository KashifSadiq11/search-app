#!/usr/bin/env python3
"""
ONE-TIME CLEANUP:

Delete all items whose description does NOT contain a valid product_id/productId
inside the [Metadata: {...}] JSON tail.

BEHAVIOR:
- Items WITHOUT product_id in metadata will be DELETED (only after user confirms).
- Items WITH product_id/productId in metadata are kept.

SAFETY:
- Runs only if CURRENT_ENVIRONMENT is 'development' or 'local'.
- Prints full summary BEFORE deletion.
- Asks for explicit 'yes' confirmation before deleting.
- Logs how many items existed, how many are deleted, and how many remain.
"""

import sys
import json
from pathlib import Path
from typing import Optional, cast, List

# ---------------------------------------------------------------
# Add PROJECT ROOT to PYTHONPATH so package imports work
# ---------------------------------------------------------------
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.engine.database import get_db
from services.engine.config import settings
from services.engine.models import Item
from logs.logging_config import get_rotating_logger


logger = get_rotating_logger(
    name="delete_items_without_product_id",
    folder_name="db_migrations",
)


def extract_product_id_from_description(description: Optional[str]) -> Optional[str]:
    """Extract product_id or productId from Metadata JSON."""
    if not description:
        return None

    marker = "[Metadata:"
    idx = description.find(marker)
    if idx == -1:
        return None

    json_start = description.find("{", idx)
    json_end = description.rfind("}")
    if json_start == -1 or json_end == -1 or json_end < json_start:
        return None

    json_str = description[json_start: json_end + 1]

    try:
        metadata = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse metadata JSON")
        return None

    return metadata.get("product_id") or metadata.get("productId")


def main() -> None:
    # --------------------------------------------------------
    # 0. Safety: only dev/local
    # --------------------------------------------------------
    current_env = settings.CURRENT_ENVIRONMENT.lower()
    if current_env not in {"development", "local"}:
        raise RuntimeError(
            f"Refusing to run in environment={current_env!r}. "
            "Run this cleanup only in 'development' or 'local'."
        )

    db = next(get_db())

    try:
        print("Scanning items…")
        logger.info("Scanning items for missing product_id metadata")

        items: List[Item] = db.query(Item).all()
        total_items = len(items)

        print(f"Total items in database: {total_items}")
        logger.info("Loaded %s items", total_items)

        if total_items == 0:
            print("No items found. Nothing to delete.")
            return

        to_delete: List[Item] = []

        # Identify items missing product_id
        for item in items:
            description_value = cast(Optional[str], item.description)
            pid = extract_product_id_from_description(description_value)

            if pid is None:
                to_delete.append(item)

        delete_count = len(to_delete)

        if delete_count == 0:
            print("All items have product_id metadata. Nothing to delete.")
            logger.info("Cleanup complete: 0 items deleted")
            return

        # Display summary BEFORE deletion
        remaining = total_items - delete_count
        print("-----------------------------------------------------")
        print(f"Items WITHOUT product_id (WILL BE DELETED): {delete_count}")
        print(f"Items WITH product_id (will remain): {remaining}")
        print("-----------------------------------------------------")
        print("⚠️  This action is irreversible.")
        print("⚠️  All items without product_id metadata will be permanently deleted.")
        print("-----------------------------------------------------")

        # Interactive confirmation
        confirm = input("Type 'yes' to confirm deletion: ").strip().lower()

        if confirm != "yes":
            print("Aborted. No items were deleted.")
            logger.info("User aborted deletion.")
            return

        print("Deleting items…")
        logger.info("User confirmed deletion of %s items", delete_count)

        # Perform deletion
        for item in to_delete:
            db.delete(item)

        db.commit()

        print("-----------------------------------------------------")
        print(f"✅ Deleted {delete_count} items WITHOUT product_id.")
        print(f"✅ Remaining items WITH product_id: {remaining}")
        print("Cleanup complete.")
        print("-----------------------------------------------------")

        logger.info("Deletion complete: %s deleted, %s remain.", delete_count, remaining)

    except Exception:
        db.rollback()
        logger.exception("Cleanup failed – transaction rolled back.")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
