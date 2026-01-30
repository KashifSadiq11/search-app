#!/usr/bin/env python3
"""
ONE-TIME MIGRATION:

Replace items.id (UUID) with BuyPass productId taken from [Metadata: {...}]
stored in the description column.

BEHAVIOR:
- Only items whose description contains a valid product_id/productId in the
  [Metadata: {...}] JSON will have their `id` changed.
- Items WITHOUT product_id in metadata are SKIPPED and KEEP their existing UUID.

SAFETY:
- Runs only if CURRENT_ENVIRONMENT is 'development' or 'local'.
- Aborts if there are any Interaction rows (to avoid FK breakage).
- Aborts if any product_id appears more than once.
"""

import sys
import json
from pathlib import Path
from typing import Optional, Dict, cast

# ---------------------------------------------------------------
# Add PROJECT ROOT to PYTHONPATH so package imports work
# ---------------------------------------------------------------
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]  # root of your project, which contains `services/`

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Now use package-style imports (same style as truncate_user_table.py)
from services.engine.database import get_db
from services.engine.config import settings
from services.engine.models import Item, Interaction
from logs.logging_config import get_rotating_logger


logger = get_rotating_logger(
    name="convert_item_ids_to_product_id",
    folder_name="db_migrations",
)


def extract_product_id_from_description(description: Optional[str]) -> Optional[str]:
    """
    Extract product_id from the [Metadata: {...}] JSON at the end of the description.

    Expected tail pattern, for example:
        ... Wireless charging standard: Qi
        [Metadata: {"buypass_id":"GEB4J","product_id":"685cedc85a950f332a8d794e", ...}]
    """
    if not description:
        return None

    marker = "[Metadata:"
    idx = description.find(marker)
    if idx == -1:
        return None

    # From first '{' after marker to last '}' in the string
    json_start = description.find("{", idx)
    json_end = description.rfind("}")
    if json_start == -1 or json_end == -1 or json_end < json_start:
        return None

    json_str = description[json_start : json_end + 1]

    try:
        metadata = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse metadata JSON from description")
        return None

    # Support both product_id and productId field names
    return metadata.get("product_id") or metadata.get("productId")


def main() -> None:
    # --------------------------------------------------------
    # 0. Safety: block non-dev environments
    # --------------------------------------------------------
    current_env = settings.CURRENT_ENVIRONMENT.lower()
    if current_env not in {"development", "local"}:
        raise RuntimeError(
            f"Refusing to run in environment={current_env!r}. "
            "Run this only in 'development' or 'local'."
        )

    db = next(get_db())

    try:
        logger.info("Starting conversion of items.id -> productId from metadata")

        # --------------------------------------------------------
        # 1. Safety: ensure there are no interactions
        # --------------------------------------------------------
        interaction_count = db.query(Interaction).count()
        if interaction_count > 0:
            msg = (
                f"Found {interaction_count} rows in 'interactions' table. "
                "To avoid breaking foreign keys, aborting. "
                "Truncate or migrate interactions first."
            )
            logger.error(msg)
            print(msg)
            return

        # --------------------------------------------------------
        # 2. Load all items and prepare mapping old_id -> new_id(productId)
        # --------------------------------------------------------
        items = db.query(Item).all()
        logger.info("Loaded %s items from database", len(items))

        if not items:
            print("No items found. Nothing to do.")
            logger.info("No items found. Exiting.")
            return

        old_to_new: Dict[str, str] = {}
        missing_product_id: list[str] = []
        duplicate_product_id: list[str] = []

        seen_new_ids: set[str] = set()

        for item in items:
            # Pylance-safe access to description and id
            description_value = cast(Optional[str], item.description)
            item_id = cast(str, item.id)

            pid = extract_product_id_from_description(description_value)
            if not pid:
                # This item has no usable product_id in metadata
                missing_product_id.append(item_id)
                continue

            # Optional heuristic: your productIds are usually around 24 chars
            if len(pid) < 10:
                logger.warning(
                    "Suspiciously short product_id='%s' for item id=%s",
                    pid,
                    item_id,
                )

            if pid in seen_new_ids:
                duplicate_product_id.append(pid)
            else:
                seen_new_ids.add(pid)

            old_to_new[item_id] = pid

        # --------------------------------------------------------
        # 3. Validate: duplicates MUST NOT exist; missing are skipped
        # --------------------------------------------------------
        if missing_product_id:
            msg = (
                f"{len(missing_product_id)} items have NO product_id in metadata. "
                f"Example item id: {missing_product_id[0]}"
            )
            logger.warning(msg)
            print(msg)
            print(
                "Continuing: these items will KEEP their existing UUID ids "
                "and will NOT be updated."
            )

        if duplicate_product_id:
            msg = (
                f"Found {len(duplicate_product_id)} duplicate product_id values. "
                f"Example product_id: {duplicate_product_id[0]}"
            )
            logger.error(msg)
            print(msg)
            print("Aborting WITHOUT making any changes due to duplicate product_ids.")
            return

        if not old_to_new:
            msg = (
                "No items with valid product_id metadata were found. "
                "Nothing to update."
            )
            logger.info(msg)
            print(msg)
            return

        logger.info(
            "Validation passed. %s items will have id changed to productId.",
            len(old_to_new),
        )
        print(
            f"Validation passed. {len(old_to_new)} items will have id changed to productId."
        )

        # --------------------------------------------------------
        # 4. Apply changes in a single transaction
        # --------------------------------------------------------
        changed = 0
        for item in items:
            item_id = cast(str, item.id)
            new_id = old_to_new.get(item_id)
            if not new_id:
                # This item either had no product_id in metadata or was otherwise skipped
                continue

            logger.debug("Updating item id=%s -> %s", item_id, new_id)
            # Use setattr to avoid Pylance confusion about Column vs value
            setattr(item, "id", new_id)
            changed += 1

        db.commit()
        logger.info("Successfully updated %s item IDs to productId.", changed)
        print(f"Successfully updated {changed} item IDs to productId.")
        print(
            f"Skipped {len(missing_product_id)} items with no product_id; "
            "they keep their original UUID ids."
        )

    except Exception:
        db.rollback()
        logger.exception("Conversion failed – transaction rolled back.")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
