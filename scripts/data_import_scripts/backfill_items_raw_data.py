#!/usr/bin/env python3
"""
Backfill ONLY items.raw_data from BuyPASS JSON.
- Match: items.id == product["productId"]
- Update only when raw_data is empty: {} (or NULL just in case)
- Do NOT modify any other columns.
- Uses DB session directly (no API calls), batched updates.
"""

import sys
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy import update, or_
from sqlalchemy.orm import Session

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.engine.database import get_db
from services.engine.models import Item

DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.algolio_products.json"

BATCH_SIZE = 500  # safe default

def _load_products(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected JSON file to contain a list of products")

    return data


def _chunked(items: List[Tuple[str, Dict[str, Any]]], size: int) -> Iterable[List[Tuple[str, Dict[str, Any]]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main() -> None:
    products = _load_products(DATA_FILE)

    # Build (productId -> raw_json) pairs, dedup by productId
    pairs: Dict[str, Dict[str, Any]] = {}
    skipped_no_pid = 0

    for p in products:
        if not isinstance(p, dict):
            continue
        pid = (p.get("productId") or "").strip()
        if not pid:
            skipped_no_pid += 1
            continue
        # last one wins if duplicates
        pairs[pid] = p

    items_to_update: List[Tuple[str, Dict[str, Any]]] = list(pairs.items())

    print(f"📄 File: {DATA_FILE}")
    print(f"📦 Products in JSON: {len(products)}")
    print(f"🧹 Skipped without productId: {skipped_no_pid}")
    print(f"🆔 Unique productIds: {len(items_to_update)}")
    print(f"🔁 Will attempt to backfill raw_data only (empty {{}} rows only).")
    print()

    confirm = input("Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("❌ Cancelled.")
        return

    db_gen = get_db()
    db: Session = next(db_gen)

    updated = 0
    not_found = 0
    already_filled = 0
    failed = 0

    try:
        # We do batched updates for performance & stability.
        for batch in _chunked(items_to_update, BATCH_SIZE):
            # For better reporting, we check which IDs exist and which are empty
            batch_ids = [pid for pid, _ in batch]

            # Find which items exist
            existing_rows = (
                db.query(Item.id, Item.raw_data)
                .filter(Item.id.in_(batch_ids))
                .all()
            )
            existing_map = {str(r[0]): r[1] for r in existing_rows}

            # Track not-found
            for pid in batch_ids:
                if pid not in existing_map:
                    not_found += 1

            # Build update statements per row (only when empty)
            # We consider empty when raw_data IS NULL OR raw_data == {}
            for pid, raw_json in batch:
                try:
                    if pid not in existing_map:
                        continue

                    current = existing_map.get(pid)

                    is_empty = (current is None) or (current == {})
                    if not is_empty:
                        already_filled += 1
                        continue

                    stmt = (
                        update(Item)
                        .where(
                            Item.id == pid,
                            or_(Item.raw_data == {}, Item.raw_data.is_(None)),
                        )
                        .values(raw_data=raw_json)
                    )
                    res = db.execute(stmt)
                    # res.rowcount should be 1 if updated, 0 if condition didn't match
                    if res.rowcount == 1:
                        updated += 1
                    else:
                        already_filled += 1

                except Exception as e:
                    failed += 1
                    # don't print huge exceptions per row; keep readable
                    print(f"✗ Failed pid={pid}: {e}")

            db.commit()
            print(f"✅ Batch committed. updated={updated}, already_filled={already_filled}, not_found={not_found}, failed={failed}")

    except Exception as e:
        db.rollback()
        print(f"❌ Fatal error. Rolled back current batch: {e}")
        raise
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass

    print("\n==============================")
    print("        BACKFILL SUMMARY      ")
    print("==============================")
    print(f"Updated raw_data:     {updated}")
    print(f"Already had raw_data: {already_filled}")
    print(f"Not found in DB:      {not_found}")
    print(f"Failed:              {failed}")
    print("==============================")
    print("🎉 Done.")


if __name__ == "__main__":
    main()
