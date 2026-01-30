#!/usr/bin/env python3
"""
Audit BuyPass Keycloak users JSON (read-only).

This script ONLY reads the JSON file and reports:

- Total objects in JSON
- Total objects with a non-empty userId
- Unique userId count
- How many duplicate userIds exist
- Which userIds are duplicated and how many times

It does NOT write to the database or modify any files.
"""

import json
import sys
from pathlib import Path
from collections import Counter
from typing import Dict, Any, List

# -----------------------------
# Project root & sys.path
# -----------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from logs.logging_config import get_rotating_logger  # type: ignore

DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.keycloak_users.json"

logger = get_rotating_logger(
    name="audit_keycloak_users",
    folder_name="audit_keycloak_users",
)

def load_users() -> List[Dict[str, Any]]:
    if not DATA_FILE.exists():
        msg = f"❌ JSON file not found: {DATA_FILE}"
        print(msg)
        logger.error(msg)
        raise FileNotFoundError(msg)

    print(f"📄 Loading Keycloak users JSON: {DATA_FILE}")
    logger.info("Loading Keycloak users JSON from %s", DATA_FILE)

    raw = DATA_FILE.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in {DATA_FILE}: {e}"
        print(msg)
        logger.error(msg)
        raise

    if not isinstance(data, list):
        msg = f"Expected a JSON array/list at root, got {type(data).__name__}"
        print(msg)
        logger.error(msg)
        raise ValueError(msg)

    return data

def audit_users(users: List[Dict[str, Any]]) -> None:
    total_objects = len(users)

    user_ids: List[str] = []
    missing_or_empty_userid = 0

    for idx, u in enumerate(users, start=1):
        raw_user_id = u.get("userId")
        if raw_user_id is None:
            missing_or_empty_userid += 1
            continue

        user_id_str = str(raw_user_id).strip()
        if not user_id_str:
            missing_or_empty_userid += 1
            continue

        user_ids.append(user_id_str)

    id_counter = Counter(user_ids)
    unique_user_ids = len(id_counter)

    # IDs that appear more than once
    duplicate_entries = {uid: count for uid, count in id_counter.items() if count > 1}
    duplicate_id_count = len(duplicate_entries)
    duplicate_records_total = sum(count - 1 for count in id_counter.values() if count > 1)

    print("\n" + "=" * 60)
    print("Keycloak Users JSON Audit (userId)")
    print("=" * 60)
    print(f"📦 Total JSON objects                 : {total_objects}")
    print(f"✅ Objects with non-empty userId      : {len(user_ids)}")
    print(f"⚠️  Objects missing/empty userId       : {missing_or_empty_userid}")
    print(f"🔑 Unique userId values               : {unique_user_ids}")
    print(f"♻️  userIds with duplicates           : {duplicate_id_count}")
    print(f"📊 Extra records due to duplicates    : {duplicate_records_total}")
    print("=" * 60)

    logger.info(
        "Audit summary | total_objects=%s, with_userid=%s, "
        "missing_or_empty_userid=%s, unique_user_ids=%s, "
        "duplicate_id_count=%s, duplicate_records_total=%s",
        total_objects,
        len(user_ids),
        missing_or_empty_userid,
        unique_user_ids,
        duplicate_id_count,
        duplicate_records_total,
    )

    if duplicate_entries:
        print("\nDuplicate userIds:")
        for uid, count in sorted(duplicate_entries.items(), key=lambda x: -x[1]):
            print(f"  - {uid}  (occurs {count} times)")
        logger.warning("Found %s duplicate userIds", duplicate_id_count)
    else:
        print("\n✅ No duplicate userIds found.")
        logger.info("No duplicate userIds found.")

def main() -> None:
    users = load_users()
    audit_users(users)

if __name__ == "__main__":
    main()
