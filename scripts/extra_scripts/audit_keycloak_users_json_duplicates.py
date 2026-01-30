#!/usr/bin/env python3
"""
Audit Keycloak users JSON for duplicate userId / email / username.

SOURCE:
- Reads from data/BuyPASS.keycloak_users.json

SAFETY:
- READ-ONLY: does NOT modify the JSON file.
- Does NOT connect to the database.
- Does NOT call any HTTP APIs.

CHECKS:
1) Duplicate userId
2) Duplicate email (raw, case-sensitive)
3) Duplicate email (case-insensitive: lower(email))
4) Duplicate username (raw, case-sensitive)
5) Duplicate username (case-insensitive: lower(username))
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from collections import Counter

# -----------------------------
# Project root & logging
# -----------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from logs.logging_config import get_rotating_logger  # type: ignore

logger = get_rotating_logger(
    name="audit_keycloak_users_json_duplicates",
    folder_name="audit_keycloak_users_json_duplicates",
)

DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.keycloak_users.json"


# -----------------------------
# Helpers
# -----------------------------

def load_users_from_json(path: Path) -> List[Dict]:
    if not path.exists():
        raise FileNotFoundError(f"Keycloak user JSON not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected JSON to be a list of users")

    return data


def find_duplicates(values: List[str]) -> List[Tuple[str, int]]:
    """
    Given a list of string values, return (value, count) for all values
    that appear more than once.
    """
    counter = Counter(values)
    return [(val, cnt) for val, cnt in counter.items() if cnt > 1]


# -----------------------------
# Main audit logic
# -----------------------------

def run_audit() -> None:
    logger.info("Starting Keycloak JSON users duplicate audit")
    users = load_users_from_json(DATA_FILE)

    total_users = len(users)
    print("=" * 60)
    print("Keycloak Users JSON Duplicate Audit (READ-ONLY)")
    print("=" * 60)
    print(f"Source file: {DATA_FILE}")
    print(f"Total user records in JSON: {total_users}")
    print("-" * 60)

    # Collect fields
    user_ids: List[str] = []
    emails_raw: List[str] = []
    emails_norm: List[str] = []
    usernames_raw: List[str] = []
    usernames_norm: List[str] = []

    missing_user_id = 0
    missing_email = 0
    missing_username = 0

    for u in users:
        # userId (Keycloak export uses userId, but we also check id just in case)
        user_id = (u.get("userId") or u.get("id") or "").strip()
        if user_id:
            user_ids.append(user_id)
        else:
            missing_user_id += 1

        # email
        email_raw = (u.get("email") or "").strip()
        if email_raw:
            emails_raw.append(email_raw)
            emails_norm.append(email_raw.lower())
        else:
            missing_email += 1

        # username (from JSON as-is, not our normalized mapping)
        username_raw = (u.get("username") or "").strip()
        if username_raw:
            usernames_raw.append(username_raw)
            usernames_norm.append(username_raw.lower())
        else:
            missing_username += 1

    # 1) Duplicate userId
    dup_user_ids = find_duplicates(user_ids)
    print("1) Duplicate `userId` values:")
    if not dup_user_ids:
        print("   ✅ No duplicate userId values found.")
    else:
        print(f"   ❌ Found {len(dup_user_ids)} duplicate userId value(s).")
        for val, cnt in sorted(dup_user_ids, key=lambda x: (-x[1], x[0]))[:20]:
            print(f"      userId={val} | count={cnt}")
        if len(dup_user_ids) > 20:
            print(f"      ... and {len(dup_user_ids) - 20} more")
    if missing_user_id:
        print(f"   ⚠ Users with missing/blank userId: {missing_user_id}")
    print("-" * 60)

    # 2) Duplicate raw emails
    dup_email_raw = find_duplicates(emails_raw)
    print("2) Duplicate `email` values (raw, case-sensitive):")
    if not dup_email_raw:
        print("   ✅ No duplicate raw emails found.")
    else:
        print(f"   ❌ Found {len(dup_email_raw)} duplicate raw email value(s).")
        for val, cnt in sorted(dup_email_raw, key=lambda x: (-x[1], x[0]))[:20]:
            print(f"      email={val} | count={cnt}")
        if len(dup_email_raw) > 20:
            print(f"      ... and {len(dup_email_raw) - 20} more")
    if missing_email:
        print(f"   ⚠ Users with missing/blank email: {missing_email}")
    print("-" * 60)

    # 3) Duplicate normalized emails (lowercase)
    dup_email_norm = find_duplicates(emails_norm)
    print("3) Duplicate `email` values (case-insensitive, lower(email)):")
    if not dup_email_norm:
        print("   ✅ No duplicate normalized emails found.")
    else:
        print(f"   ❌ Found {len(dup_email_norm)} duplicate normalized email value(s).")
        for val, cnt in sorted(dup_email_norm, key=lambda x: (-x[1], x[0]))[:20]:
            print(f"      email={val} | count={cnt}")
        if len(dup_email_norm) > 20:
            print(f"      ... and {len(dup_email_norm) - 20} more")
    print("-" * 60)

    # 4) Duplicate raw usernames
    dup_username_raw = find_duplicates(usernames_raw)
    print("4) Duplicate `username` values (raw, case-sensitive):")
    if not dup_username_raw:
        print("   ✅ No duplicate raw usernames found.")
    else:
        print(f"   ❌ Found {len(dup_username_raw)} duplicate raw username value(s).")
        for val, cnt in sorted(dup_username_raw, key=lambda x: (-x[1], x[0]))[:20]:
            print(f"      username={val} | count={cnt}")
        if len(dup_username_raw) > 20:
            print(f"      ... and {len(dup_username_raw) - 20} more")
    if missing_username:
        print(f"   ⚠ Users with missing/blank username: {missing_username}")
    print("-" * 60)

    # 5) Duplicate normalized usernames
    dup_username_norm = find_duplicates(usernames_norm)
    print("5) Duplicate `username` values (case-insensitive, lower(username)):")
    if not dup_username_norm:
        print("   ✅ No duplicate normalized usernames found.")
    else:
        print(f"   ❌ Found {len(dup_username_norm)} duplicate normalized username value(s).")
        for val, cnt in sorted(dup_username_norm, key=lambda x: (-x[1], x[0]))[:20]:
            print(f"      username={val} | count={cnt}")
        if len(dup_username_norm) > 20:
            print(f"      ... and {len(dup_username_norm) - 20} more")
    print("-" * 60)

    print("JSON audit completed (no changes were made to the file).")

    logger.info(
        "Keycloak JSON users duplicate audit finished | total_users=%s | "
        "dup_userIds=%s | dup_email_raw=%s | dup_email_norm=%s | "
        "dup_username_raw=%s | dup_username_norm=%s | "
        "missing_userId=%s | missing_email=%s | missing_username=%s",
        total_users,
        len(dup_user_ids),
        len(dup_email_raw),
        len(dup_email_norm),
        len(dup_username_raw),
        len(dup_username_norm),
        missing_user_id,
        missing_email,
        missing_username,
    )


if __name__ == "__main__":
    run_audit()
