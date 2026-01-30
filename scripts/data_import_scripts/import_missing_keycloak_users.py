#!/usr/bin/env python3
"""
Import ONLY missing BuyPass Keycloak users into the RecEngine `users` table.

BEHAVIOR:
- Reads BuyPASS.keycloak_users.json (Keycloak export).
- Loads ALL existing users from API endpoints (NOT direct DB).
- Compares JSON userId -> API users.id (primary key).
- Finds Keycloak users whose userId is NOT present via API.
- Further filters to those whose email is NOT already in API.
- Detects and skips duplicate userId / email entries inside the JSON itself.
- Shows a summary (counts, breakdown, duplicated emails).
- Asks for confirmation before inserting ANYTHING.
- Inserts missing users via FastAPI POST /users/.

SAFETY:
- Read-only comparison via API first.
- JSON-level duplicate detection (userId/email).
- API-level duplicate detection (id/email).
- No inserts unless you explicitly confirm.
- Skips any user with missing/invalid userId or email.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any, Set
from collections import Counter
from itertools import islice
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import requests

# -----------------------------
# Configuration
# -----------------------------

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[2] 

API_URL = "http://localhost:8016"   # API running on localhost:8016
DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.keycloak_users.json"

BATCH_SIZE = 150
MAX_THREADS = 12   # Adjust based on system/API capability

# from services.engine.database import get_db          
# from services.engine.models import User              


def convert_keycloak_user(raw_user: Dict[str, Any]) -> Dict[str, str]:
    """
    Convert a Keycloak user JSON object to RecEngine user payload.
    Mapping:
        userId   -> id
        email    -> email
        username -> email (normalized lower)
    """
    user_id = (raw_user.get("userId") or "").strip()
    email = (raw_user.get("email") or "").strip()

    if not user_id:
        raise ValueError("Missing userId (cannot create primary key).")

    if not email:
        raise ValueError(f"Missing email for userId={user_id}")

    email_normalized = email.lower()

    return {
        "id": user_id,
        "username": email_normalized,
        "email": email_normalized,
    }


#  Get existing users from API

def load_existing_users_from_api() -> Tuple[Set[str], Set[str]]:
    """
    Load all existing users (id + email) from the API.
    
    Assumes your API has endpoints:
    - GET /users/ (or similar) to list users
    - Or we can fetch all users via pagination
    
    Returns:
    - existing_ids: set of users.id
    - existing_emails: set of lower(email)
    """
    existing_ids: Set[str] = set()
    existing_emails: Set[str] = set()
    
    try:
        # Option 1: If your API has a "get all users" endpoint
        response = requests.get(f"{API_URL}/users/", timeout=30)
        
        if response.status_code == 200:
            users_data = response.json()
            
            # Handle different response formats:
            if isinstance(users_data, dict) and "items" in users_data:
                # Paginated response: {"items": [...], "total": X}
                users = users_data["items"]
            elif isinstance(users_data, list):
                # Direct list response
                users = users_data
            else:
                print(f" Unexpected API response format: {type(users_data)}")
                print(f"Response preview: {str(users_data)[:200]}")
                users = []
            
            for user in users:
                # Extract id and email from API response
                user_id = str(user.get("id") or "").strip()
                if user_id:
                    existing_ids.add(user_id)
                
                email = (user.get("email") or "").strip().lower()
                if email:
                    existing_emails.add(email)
            
            print(f" Loaded {len(existing_ids)} existing users from API")
            
        else:
            print(f" API returned status {response.status_code}: {response.text[:200]}")
            print("Will assume no existing users (empty database)")
            
    except requests.RequestException as e:
        print(f" Could not connect to API at {API_URL}: {e}")
        print("Will assume no existing users (empty database)")
    
    return existing_ids, existing_emails

def load_existing_users_from_api_paginated() -> Tuple[Set[str], Set[str]]:
    """
    Alternative: If your API uses pagination with page/size parameters.
    Modify this based on your actual API pagination scheme.
    """
    existing_ids: Set[str] = set()
    existing_emails: Set[str] = set()
    
    page = 1
    page_size = 100
    
    try:
        while True:
            # Adjust URL based on your API's pagination
            response = requests.get(
                f"{API_URL}/users/",
                params={"page": page, "size": page_size},
                timeout=30
            )
            
            if response.status_code != 200:
                print(f" API returned status {response.status_code} on page {page}")
                break
            
            data = response.json()
            
            # Check if this is a paginated response
            if isinstance(data, dict) and "items" in data:
                users = data["items"]
                total_pages = data.get("total_pages", 0)
                
                for user in users:
                    user_id = str(user.get("id") or "").strip()
                    if user_id:
                        existing_ids.add(user_id)
                    
                    email = (user.get("email") or "").strip().lower()
                    if email:
                        existing_emails.add(email)
                
                print(f"📥 Page {page}/{total_pages}: Loaded {len(users)} users")
                
                # Check if we have more pages
                if page >= total_pages or not users:
                    break
                page += 1
                
            else:
                # Non-paginated response
                users = data if isinstance(data, list) else []
                for user in users:
                    user_id = str(user.get("id") or "").strip()
                    if user_id:
                        existing_ids.add(user_id)
                    
                    email = (user.get("email") or "").strip().lower()
                    if email:
                        existing_emails.add(email)
                
                print(f"📊 Loaded {len(users)} users from non-paginated API")
                break
                
    except requests.RequestException as e:
        print(f" API error: {e}")
    
    print(f" Total loaded from API: {len(existing_ids)} users")
    return existing_ids, existing_emails


# Core logic using rec_engine main API

def load_keycloak_users() -> List[Dict[str, Any]]:
    """Load all Keycloak users from JSON file."""
    if not DATA_FILE.exists():
        msg = f"Keycloak user file not found: {DATA_FILE}"
        print(msg)
        raise FileNotFoundError(msg)

    print(f" Loading Keycloak users from {DATA_FILE}")

    raw = DATA_FILE.read_text(encoding="utf-8")
    users = json.loads(raw)

    if not isinstance(users, list):
        msg = f"Expected JSON array at root, got {type(users).__name__}"
        print(msg)
        raise ValueError(msg)

    return users

def compute_missing_users(
    keycloak_users: List[Dict[str, Any]],
    existing_ids: Set[str],
    existing_emails: Set[str],
) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    """
    Determine which Keycloak users are missing via API.
    """
    safe_to_create: List[Dict[str, str]] = []
    conflicts: List[Dict[str, Any]] = []

    total_json = len(keycloak_users)

    # Track duplicates INSIDE the JSON itself
    seen_json_ids: Set[str] = set()
    seen_json_emails: Set[str] = set()

    # Track counts for breakdown
    id_in_api_count = 0

    for raw_user in keycloak_users:
        try:
            payload = convert_keycloak_user(raw_user)
        except ValueError as e:
            conflicts.append(
                {
                    "raw": raw_user,
                    "error": str(e),
                    "reason": "invalid_user",
                }
            )
            continue

        user_id = payload["id"]
        email = payload["email"]

        # JSON-level duplicate checks
        if user_id in seen_json_ids:
            conflicts.append({"payload": payload, "reason": "duplicate_userId_in_json"})
            continue
        seen_json_ids.add(user_id)

        if email in seen_json_emails:
            conflicts.append({"payload": payload, "reason": "duplicate_email_in_json"})
            continue
        seen_json_emails.add(email)

        # API-level duplicate checks
        if user_id in existing_ids:
            id_in_api_count += 1
            continue

        if email in existing_emails:
            conflicts.append({"payload": payload, "reason": "email_already_exists_in_api"})
            continue

        safe_to_create.append(payload)

    # Breakdown counts by reason
    invalid_count = sum(1 for c in conflicts if c.get("reason") == "invalid_user")
    dup_id_json_count = sum(1 for c in conflicts if c.get("reason") == "duplicate_userId_in_json")
    dup_email_json_count = sum(1 for c in conflicts if c.get("reason") == "duplicate_email_in_json")
    email_in_api_conf_count = sum(1 for c in conflicts if c.get("reason") == "email_already_exists_in_api")

    print("\n" + "=" * 60)
    print("Keycloak → RecEngine Users Missing-ID Analysis")
    print("=" * 60)
    print(f" Total Keycloak users in JSON              : {total_json}")
    print(f" Total existing users in API (by id)       : {len(existing_ids)}")
    print(f" Safe to create (id+email not in API/JSON) : {len(safe_to_create)}")
    print(f" Conflicts / invalid (will NOT create)    : {len(conflicts)}")
    print("=" * 60)

    print("\nSummary:")
    print(f"  - JSON has              : {total_json} users")
    print(f"  - Should NOT be inserted: {len(conflicts)} users (invalid / duplicates / conflicts)")
    print(f"  - SAFE to insert        : {len(safe_to_create)} users\n")

    print(" Breakdown (how JSON → SAFE):")
    print(f"  Total JSON users                     : {total_json}")
    print(f"    - Invalid users (missing id/email) : {invalid_count}")
    print(f"    - Duplicate userId inside JSON     : {dup_id_json_count}")
    print(f"    - Duplicate email inside JSON      : {dup_email_json_count}")
    print(f"    - Users already in API by id       : {id_in_api_count}")
    print(f"    - Users skipped (email in API)     : {email_in_api_conf_count}")
    print(f"  = SAFE to insert                     : {len(safe_to_create)}\n")

    # Duplicate-email breakdown (inside JSON)
    conflict_emails_json = [
        c["payload"]["email"]
        for c in conflicts
        if c.get("reason") == "duplicate_email_in_json"
        and c.get("payload") is not None
        and c["payload"].get("email")
    ]

    if conflict_emails_json:
        counts_json_emails = Counter(conflict_emails_json)

        print(" Emails duplicated INSIDE JSON (before API check):")
        total_entries_for_dup_emails = 0

        for email, dup_count in sorted(counts_json_emails.items(), key=lambda x: x[0]):
            total_entries = dup_count + 1
            total_entries_for_dup_emails += total_entries
            print(f"  - {email}  → {total_entries} JSON entries (JSON duplicate)")

        unique_dup_emails = len(counts_json_emails)
        duplicates_to_skip = total_entries_for_dup_emails - unique_dup_emails

        print("\n Duplicate-email breakdown:")
        print(f"  Total JSON entries with these emails : {total_entries_for_dup_emails}")
        print(f"  Unique duplicated email addresses    : {unique_dup_emails}")
        print(f"  Entries treated as duplicates/skipped: {duplicates_to_skip}\n")

    return safe_to_create, conflicts

def chunked(iterable, size):
    it = iter(iterable)
    while True:
        batch = list(islice(it, size))
        if not batch:
            return
        yield batch

def main() -> None:
    """Main function - uses API for everything"""
    
    print(f"Using API at: {API_URL}")
    print(f"Loading data from: {DATA_FILE}")
    
    # 1. Load Keycloak users
    keycloak_users = load_keycloak_users()
    
    # 2. Get existing users from API (NOT database)
    print("\n📡 Fetching existing users from API...")
    existing_ids, existing_emails = load_existing_users_from_api()
    
    # If the simple endpoint doesn't work, try paginated version:
    if len(existing_ids) == 0:
        print("Trying paginated API endpoint...")
        existing_ids, existing_emails = load_existing_users_from_api_paginated()
    
    # 3. Compute missing users
    safe_to_create, conflicts = compute_missing_users(
        keycloak_users, existing_ids, existing_emails
    )

    if not safe_to_create:
        print("\nNo safe missing users to create. Nothing to do.")
        return

    # 4. Show preview of users to be created
    print(f"\n👥 First 5 users to be created:")
    for i, user in enumerate(safe_to_create[:5], 1):
        print(f"  {i}. id={user['id']}, email={user['email']}")
    
    if len(safe_to_create) > 5:
        print(f"  ... and {len(safe_to_create) - 5} more")
    
    # 5. Ask for confirmation
    confirm = input(
        f"\nCreate {len(safe_to_create)} users via API? "
        f"Type 'yes' to proceed, anything else to abort: "
    ).strip().lower()

    if confirm != "yes":
        print(" Aborted. No users have been inserted.")
        return

    # 6. Insert users via API
    print("Starting parallel import via API...")
    
    lock = threading.Lock()
    inserted_ids: Set[str] = set()
    successful = 0
    failed = 0
    failed_details: List[Dict[str, Any]] = []

    def process_single_batch(batch, batch_index):
        nonlocal successful, failed, failed_details

        local_created = 0
        local_failed = []
        local_inserted = set()

        for payload in batch:
            user_id = payload["id"]
            
            try:
                # Make API call to create user
                resp = requests.post(
                    f"{API_URL}/users/", 
                    json=payload, 
                    timeout=30,
                    headers={"Content-Type": "application/json"}
                )
            except requests.RequestException as e:
                error_detail = {"payload": payload, "error": str(e)}
                local_failed.append(error_detail)
                continue

            if resp.status_code in (200, 201):
                local_created += 1
                local_inserted.add(user_id)
            else:
                error_detail = {
                    "payload": payload, 
                    "status": resp.status_code,
                    "error": resp.text[:500]
                }
                local_failed.append(error_detail)

        # Merge thread results safely
        with lock:
            inserted_ids.update(local_inserted)
            successful += local_created
            failed += len(local_failed)
            failed_details.extend(local_failed)

            print(f"✓ Batch {batch_index} finished → created {local_created}, failed {len(local_failed)}")

    # Create batches and process in parallel
    batches = list(chunked(safe_to_create, BATCH_SIZE))
    total_batches = len(batches)

    print(f"\n Processing {len(safe_to_create)} users in {total_batches} batches...")

    completed_batches = 0
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = [
            executor.submit(process_single_batch, batch, i)
            for i, batch in enumerate(batches, start=1)
        ]

        for future in as_completed(futures):
            completed_batches += 1

            # Print progress every 4 batches or at the end
            if completed_batches % 4 == 0 or completed_batches == total_batches:
                with lock:
                    print("=" * 60)
                    print(f"  Progress: {completed_batches}/{total_batches} batches")
                    print(f"  Created so far: {successful}")
                    print(f"  Failed so far: {failed}")
                    print("=" * 60)

    # 7. Final summary
    print("\n" + "=" * 60)
    print("           IMPORT COMPLETE             ")
    print("=" * 60)
    print(f"Successfully created: {successful} users")
    print(f"Failed: {failed} users")
    
    if failed_details:
        print(f"\n📋 Failed users (first 10):")
        for i, detail in enumerate(failed_details[:10], 1):
            user_id = detail["payload"]["id"]
            status = detail.get("status", "Connection Error")
            error = detail.get("error", "Unknown error")
            print(f"  {i}. id={user_id} | Status: {status} | Error: {error[:100]}...")
        
        if len(failed_details) > 10:
            print(f"  ... and {len(failed_details) - 10} more failures")
    
    print("=" * 60)
    print("Import finished!")

if __name__ == "__main__":
    main()