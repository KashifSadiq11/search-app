#!/usr/bin/env python3
"""
Import BuyPass Keycloak users into the RecEngine users table via API.

JSON fields -> DB fields mapping:

- userId   -> id        (primary key)
- fullName -> username
- email    -> email
"""

import json
import sys
import os
from pathlib import Path
from typing import Dict, List, Tuple

import requests

# Optional: load .env from project root if python-dotenv is installed
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# -----------------------------
# Project root & sys.path
# -----------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Ensure project root is on sys.path so "logs" (and other packages) can be imported
project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

# Load environment variables from .env at project root (if dotenv available)
if load_dotenv is not None:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

from logs.logging_config import get_rotating_logger  # central logging

# -----------------------------
# Config
# -----------------------------

# For now keep this simple like your other script.
# If you want, later we can switch to API_URL = os.getenv("RECENGINE_API_URL")
API_URL = "http://localhost:8000"   # <-- change if using /api/v1/users/

# Resolve project root (rec-engine/)
# (Already done above; re-used here just for clarity)
# PROJECT_ROOT = Path(__file__).resolve().parents[1]

# *** Your file + path ***
DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.keycloak_users.json"

# Logger for this script (logs/import_keycloak_users/*.log)
logger = get_rotating_logger(
    name="import_keycloak_users",
    folder_name="import_keycloak_users",
)

# -----------------------------
# Helper: check if user exists
# -----------------------------

def user_exists(user_id: str) -> bool:
    """
    Check if a user already exists in the backend by primary key (id).

    Assumes you have an endpoint similar to:
        GET /users/{user_id}

    Returns:
        True  -> user exists (HTTP 200)
        False -> user not found (HTTP 404)

    Any other status code is treated as "unknown", and we return False
    so the caller can still attempt creation (and see the error).
    """
    try:
        resp = requests.get(f"{API_URL}/users/{user_id}")
    except requests.RequestException as exc:
        msg = f"Could not check existence for user_id={user_id}: {exc}"
        print(f"⚠️  {msg}")
        logger.warning(msg)
        return False

    if resp.status_code == 200:
        logger.debug("User %s exists (HTTP 200).", user_id)
        return True
    if resp.status_code == 404:
        logger.debug("User %s does not exist (HTTP 404).", user_id)
        return False

    msg = (
        f"Unexpected status while checking user {user_id}: "
        f"{resp.status_code} {resp.text[:200]}"
    )
    print(f"⚠️  {msg}")
    logger.warning(msg)
    return False


# -----------------------------
# Conversion logic
# -----------------------------

def convert_keycloak_user(user: Dict) -> Dict:
    """
    Convert a Keycloak user to DB payload.

    Mapping:
        userId   -> id
        fullName -> username
        email    -> email
    """
    # Required fields
    user_id = (user.get("userId") or "").strip()
    full_name = (user.get("fullName") or "").strip()
    email = (user.get("email") or "").strip()

    if not user_id:
        raise ValueError("Missing userId (cannot create primary key).")

    if not email:
        raise ValueError(f"Missing email for userId={user_id}")

    # If fullName missing, fallback to email prefix
    if not full_name:
        full_name = email.split("@")[0]

    payload = {
        "id": user_id,          # DB PK
        "username": full_name,  # DB username
        "email": email,         # DB email
    }

    return payload


# -----------------------------
# Import logic
# -----------------------------

def import_users(users_json: str) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    users: List[Dict] = json.loads(users_json)

    msg = f"Found {len(users)} users to import"
    print(msg)
    logger.info(msg)

    successful: List[Dict] = []
    failed: List[Dict] = []
    skipped_existing: List[Dict] = []

    for i, raw_user in enumerate(users):
        n = i + 1

        # --- Extract user_id early, so we can existence-check before converting ---
        user_id = (raw_user.get("userId") or "").strip()
        display_name = (
            raw_user.get("fullName")
            or raw_user.get("email")
            or user_id
            or f"index_{n}"
        )

        if not user_id:
            err = "Missing userId"
            print(f"✗ [{n}/{len(users)}] {err}, skipping.")
            logger.error(
                "Skipping user [%s/%s]: %s | Error: %s",
                n,
                len(users),
                display_name,
                err,
            )
            failed.append({"raw": raw_user, "error": err})
            continue

        # Check if user already exists in DB (via API)
        if user_exists(user_id):
            print(
                f"⏭ [{n}/{len(users)}] User already exists: {display_name} (id={user_id}), skipping."
            )
            logger.info(
                "Skipped existing user [%s/%s]: %s (id=%s)",
                n,
                len(users),
                display_name,
                user_id,
            )
            skipped_existing.append({"userId": user_id})
            continue

        try:
            payload = convert_keycloak_user(raw_user)

            # POST to backend
            response = requests.post(f"{API_URL}/users/", json=payload)

            if response.status_code in (200, 201):
                body = response.json()
                successful.append(body)
                print(
                    f"✓ [{n}/{len(users)}] Imported {payload['username']} ({payload['email']})"
                )
                logger.info(
                    "Imported user [%s/%s]: %s (%s)",
                    n,
                    len(users),
                    payload["username"],
                    payload["email"],
                )

            # If backend returns conflict (e.g., duplicate primary key),
            # treat as "already exists" instead of "hard failure".
            elif response.status_code == 409:
                print(
                    f"⏭ [{n}/{len(users)}] User {payload['username']} already exists "
                    f"(409 Conflict), skipping."
                )
                logger.info(
                    "User already exists (409 Conflict) [%s/%s]: %s (id=%s)",
                    n,
                    len(users),
                    payload["username"],
                    payload["id"],
                )
                skipped_existing.append({"userId": payload["id"]})

            else:
                err = {
                    "payload": payload,
                    "status": response.status_code,
                    "error": response.text,
                }
                failed.append(err)

                print(f"✗ [{n}/{len(users)}] Failed {payload['username']}")
                print(f"    Status: {response.status_code}")
                print(f"    Error : {response.text[:200]}")

                logger.error(
                    "Failed importing user [%s/%s]: %s | Status: %s | Error: %s",
                    n,
                    len(users),
                    payload["username"],
                    response.status_code,
                    response.text[:300],
                )

        except Exception as e:
            err_str = str(e)
            failed.append({"raw": raw_user, "error": err_str})
            print(f"✗ [{n}/{len(users)}] Error importing '{display_name}': {e}")
            logger.exception(
                "Exception while importing user [%s/%s]: %s | Error: %s",
                n,
                len(users),
                display_name,
                err_str,
            )

    # Summary
    print("\n" + "=" * 60)
    print("User Import Summary")
    print(f"  Successful creates : {len(successful)}")
    print(f"  Already existed    : {len(skipped_existing)}")
    print(f"  Failed             : {len(failed)}")

    logger.info(
        "User import summary | Successful: %s | Already existed: %s | Failed: %s",
        len(successful),
        len(skipped_existing),
        len(failed),
    )

    # Optional: persist results for audit/debug
    if successful:
        out_path = PROJECT_ROOT / "logs" / "import_keycloak_users" / "imported_users.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(successful, f, indent=2)
        logger.info("Saved %s successful users to %s", len(successful), out_path)

    if failed:
        out_path = PROJECT_ROOT / "logs" / "import_keycloak_users" / "failed_users.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(failed, f, indent=2)
        logger.warning("Saved %s failed users to %s", len(failed), out_path)

    return successful, skipped_existing, failed


# -----------------------------
# Main entrypoint
# -----------------------------

if __name__ == "__main__":
    if not DATA_FILE.exists():
        msg = f"Keycloak user file not found: {DATA_FILE}"
        print(msg)
        logger.error(msg)
        raise FileNotFoundError(msg)

    print(f"Loading Keycloak users from {DATA_FILE}")
    logger.info("Loading Keycloak users from %s", DATA_FILE)

    with DATA_FILE.open("r", encoding="utf-8") as f:
        json_raw = f.read()

    successful, skipped, failed = import_users(json_raw)

    logger.info(
        "User import finished. Successful=%s, Skipped=%s, Failed=%s",
        len(successful),
        len(skipped),
        len(failed),
    )
