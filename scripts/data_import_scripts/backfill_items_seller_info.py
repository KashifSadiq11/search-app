#!/usr/bin/env python3
"""
Backfill items.seller_info (JSONB) from BuyPass JSON file.

Rules:
- Only update rows where seller_info IS NULL
- Match DB items by items.id == JSON productId
- seller_info comes from JSON field: product["seller"]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List
# from sqlalchemy import or_


CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.engine.database import get_db
from services.engine.models import Item  # must have seller_info column


def _load_products(data_file: Path) -> List[Dict[str, Any]]:
    if not data_file.exists():
        raise FileNotFoundError(f"JSON file not found: {data_file}")

    products = json.loads(data_file.read_text(encoding="utf-8"))
    if not isinstance(products, list):
        raise ValueError("Expected JSON root to be a list of products")
    return products


def _build_seller_map(products: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Returns: { productId(str) -> seller(dict) }

    Includes only entries where:
      - productId present
      - seller is a dict
      - seller has non-empty id
    If duplicates exist in JSON, first one wins.
    """
    mapping: Dict[str, Dict[str, Any]] = {}

    for p in products:
        pid = (p.get("productId") or "").strip()
        if not pid:
            continue

        seller = p.get("seller")
        if not isinstance(seller, dict):
            continue

        seller_id = str(seller.get("id") or "").strip()
        if not seller_id:
            continue

        if pid in mapping:
            continue

        mapping[pid] = seller

    return mapping

def _chunks(seq: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]

def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill items.seller_info from BuyPass JSON")
    parser.add_argument(
        "--data-file",
        default=str(PROJECT_ROOT / "data" / "BuyPASS.algolio_products.json"),
        help="Path to BuyPass products JSON file",
    )
    parser.add_argument("--batch-size", type=int, default=500, help="DB update batch size")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes, only report")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    data_file = Path(args.data_file).resolve()
    batch_size = int(args.batch_size)
    if batch_size <= 0:
        raise ValueError("--batch-size must be > 0")

    products = _load_products(data_file)
    seller_by_pid = _build_seller_map(products)

    db = next(get_db())

    # 1) Collect NULL seller_info IDs
    null_ids_rows = (
        db.query(Item.id)
        .filter(Item.seller_info.is_(None))
        .filter(Item.seller_info == {})
        .filter(Item.seller_info == 'null')
        .filter(Item.seller_info == None)
        .all()
    )
    null_ids = [str(r[0]) for r in null_ids_rows]
    total_null = len(null_ids)
    if total_null == 0:
        print("🎉 No items with seller_info NULL. Nothing to backfill.")
        return 0

    # 2) Match against JSON
    updatable_ids: List[str] = []
    missing_in_json: List[str] = []

    for item_id in null_ids:
        if item_id in seller_by_pid:
            updatable_ids.append(item_id)
        else:
            missing_in_json.append(item_id)

    print("\n==============================")
    print("   SELLER_INFO BACKFILL REPORT ")
    print("==============================")
    print(f"Items with seller_info NULL:         {total_null}")
    print(f"Matchable via JSON (will update):    {len(updatable_ids)}")
    print(f"Missing in JSON (will remain NULL):  {len(missing_in_json)}")
    print("==============================\n")

    if args.dry_run:
        print("🧪 DRY RUN enabled. No DB writes will be performed.")
        return 0

    if not args.yes:
        confirm = input(
            f"⚠️  Proceed to update seller_info for {len(updatable_ids)} items? (yes/no): "
        ).strip().lower()
        if confirm != "yes":
            print("❌ Cancelled. No changes were made.")
            return 0

    # 3) Apply updates in batches
    updated = 0
    skipped_no_seller_map = 0

    try:
        for batch in _chunks(updatable_ids, batch_size):
            rows = (
                db.query(Item)
                .filter(Item.id.in_(batch))
                .filter(Item.seller_info.is_(None))
                .filter(Item.seller_info == {})
                .filter(Item.seller_info == 'null')
                .filter(Item.seller_info == None).all()
                .all()
            )
            # print(f'Rows: {len(rows)}')
            for item in rows:
                seller = seller_by_pid.get(str(item.id))
                if not seller:
                    skipped_no_seller_map += 1
                    continue

                item.seller_info = seller
                updated += 1

            db.commit()

    except Exception as e:
        db.rollback()
        print(f"❌ Backfill failed. Rolled back. Error: {e}")
        return 1

    print("\n==============================")
    print("      BACKFILL SUMMARY         ")
    print("==============================")
    print(f"Updated rows:              {updated}")
    print(f"Skipped (no seller map):   {skipped_no_seller_map}")
    print("==============================")
    print("✅ Backfill complete.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())