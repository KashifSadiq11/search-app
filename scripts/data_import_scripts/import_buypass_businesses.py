#!/usr/bin/env python3
"""
Import BuyPASS businesses into the business table.

Mapping:
- business.id       = int(record["sellerId"])
- business.raw_data = full JSON record (stored as JSONB)

Safety:
- Skips invalid/missing sellerId
- Dedupes sellerId inside JSON
- Uses Postgres ON CONFLICT DO NOTHING (no duplicates)
- Batched inserts with transactional safety

Extras:
- Prints the JSON indexes / sellerIds that are invalid or duplicated
"""

import sys
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.engine.database import get_db
from services.engine.models import Business


# =========================
# CONFIG
# =========================
DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.business.json"
BATCH_SIZE = 1000

# how many samples to print (keeps output readable)
MAX_PRINT_INVALID = 50
MAX_PRINT_DUP_IDS = 50
MAX_PRINT_DUP_INDEXES_PER_ID = 5


# =========================
# HELPERS
# =========================
def load_json_array(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("BuyPASS.business.json must be a JSON array")

    return data


def parse_seller_id(raw: Any) -> int | None:
    """
    sellerId is usually a string: "1259428205"
    Convert safely to BIGINT int.
    """
    if raw is None:
        return None

    if isinstance(raw, int):
        return raw

    if isinstance(raw, str):
        value = raw.strip()
        if not value.isdigit():
            return None
        return int(value)

    return None


def chunk(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    if size <= 0:
        raise ValueError("BATCH_SIZE must be > 0")
    return [items[i : i + size] for i in range(0, len(items), size)]


def insert_batch(db: Session, rows: List[Dict[str, Any]]) -> int:
    """
    INSERT INTO business (...) VALUES (...)
    ON CONFLICT (id) DO NOTHING
    """
    stmt = (
        pg_insert(Business.__table__)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[Business.id])
    )
    result = db.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


# =========================
# MAIN
# =========================
def main() -> None:
    print(f"📄 Loading JSON from: {DATA_FILE}")
    records = load_json_array(DATA_FILE)
    print(f"📦 Total JSON records: {len(records)}")

    seen_in_json: Set[int] = set()
    to_insert: List[Dict[str, Any]] = []

    invalid_count = 0
    duplicate_in_json = 0

    # Track issues
    # invalid_entries: list of (json_index, raw_sellerId_value)
    invalid_entries: List[Tuple[int, Any]] = []
    # dup_index_map: sellerId -> list of JSON indexes where it appeared (for samples)
    dup_index_map: Dict[int, List[int]] = {}

    # -------- build insert list --------
    for idx, rec in enumerate(records):
        raw_seller_id = rec.get("sellerId")
        seller_id = parse_seller_id(raw_seller_id)

        if seller_id is None:
            invalid_count += 1
            if len(invalid_entries) < MAX_PRINT_INVALID:
                invalid_entries.append((idx, raw_seller_id))
            continue

        if seller_id in seen_in_json:
            duplicate_in_json += 1
            # store a few sample indexes for each duplicated id
            if seller_id not in dup_index_map:
                dup_index_map[seller_id] = []
            if len(dup_index_map[seller_id]) < MAX_PRINT_DUP_INDEXES_PER_ID:
                dup_index_map[seller_id].append(idx)
            continue

        seen_in_json.add(seller_id)

        to_insert.append(
            {
                "id": seller_id,
                "raw_data": rec,
            }
        )

    # -------- report --------
    print("\n==============================")
    print("    BUSINESS IMPORT REPORT    ")
    print("==============================")
    print(f"Valid sellerId count:          {len(seen_in_json)}")
    print(f"Invalid/missing sellerId:      {invalid_count}")
    print(f"Duplicates inside JSON:        {duplicate_in_json}")
    print(f"Rows prepared for insert:      {len(to_insert)}")
    print("==============================")

    # Print invalid samples
    if invalid_count > 0:
        print("\nInvalid/missing sellerId samples (JSON index -> raw sellerId):")
        for json_index, raw_val in invalid_entries[:MAX_PRINT_INVALID]:
            print(f"  - index={json_index} -> sellerId={raw_val!r}")
        if invalid_count > len(invalid_entries):
            print(f"  ... (showing first {len(invalid_entries)} of {invalid_count})")

    # Print duplicate sellerId samples
    if duplicate_in_json > 0:
        print("\nDuplicate sellerId samples (sellerId -> sample JSON indexes of duplicates):")
        # dup_index_map includes only ids that had duplicates (and sample indexes)
        dup_ids_sorted = sorted(dup_index_map.keys())
        for sid in dup_ids_sorted[:MAX_PRINT_DUP_IDS]:
            indexes = dup_index_map[sid]
            print(f"  - {sid} -> duplicate_indexes={indexes}")
        if len(dup_ids_sorted) > MAX_PRINT_DUP_IDS:
            print(f"  ... (showing first {MAX_PRINT_DUP_IDS} duplicate sellerIds of {len(dup_ids_sorted)})")

    print("==============================\n")

    if not to_insert:
        print("🎉 Nothing to insert.")
        return

    confirm = input(
        f"⚠️  Insert {len(to_insert)} businesses into DB? (yes/no): "
    ).strip().lower()

    if confirm != "yes":
        print("❌ Import cancelled.")
        return

    db = next(get_db())

    try:
        batches = chunk(to_insert, BATCH_SIZE)
        print(f"\n🚀 Inserting in {len(batches)} batch(es)...\n")

        total_inserted = 0
        failed_batches = 0

        for batch_idx, batch in enumerate(batches, start=1):
            try:
                inserted = insert_batch(db, batch)
                db.commit()
                total_inserted += inserted
                print(
                    f"✓ Batch {batch_idx}/{len(batches)} committed "
                    f"(attempted={len(batch)}, inserted~={inserted})"
                )
            except Exception as exc:
                db.rollback()
                failed_batches += 1
                print(f"✗ Batch {batch_idx}/{len(batches)} FAILED: {exc}")

        print("\n==============================")
        print("      IMPORT SUMMARY          ")
        print("==============================")
        print(f"Attempted inserts: {len(to_insert)}")
        print(f"Inserted (best effort): {total_inserted}")
        print(f"Failed batches: {failed_batches}")
        print("==============================")
        print("🎉 Import finished!")

    finally:
        db.close()


if __name__ == "__main__":
    main()
