#!/usr/bin/env python3
"""
Import BuyPass Keycloak users into the RecEngine `users` table via direct DB access.

Logic:

- If email already exists in DB (case-insensitive):
    -> UPDATE that row: username = email, email = email
- If email does NOT exist:
    -> INSERT new user with:
         id       = userId  (from JSON)
         email    = email
         username = email

Matching is done ONLY by email.
We do NOT call any API endpoints; we talk directly to PostgreSQL.
"""

import json
import sys
import os
from pathlib import Path
from typing import Dict, List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import errors as pg_errors

# Optional: load .env from project root if python-dotenv is installed
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# -----------------------------
# Project root & sys.path
# -----------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

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

DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.keycloak_users.json"

# Map to your .env keys
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")

if not all([DB_NAME, DB_USER, DB_PASSWORD]):
    raise RuntimeError(
        "Database configuration missing. Please set POSTGRES_DB, "
        "POSTGRES_USER, POSTGRES_PASSWORD in your .env."
    )

logger = get_rotating_logger(
    name="import_keycloak_users",
    folder_name="import_keycloak_users",
)

# -----------------------------
# DB helper
# -----------------------------

def get_connection():
    """Create a new PostgreSQL connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )

# -----------------------------
# Conversion logic
# -----------------------------

def convert_keycloak_user(user: Dict) -> Dict:
    """
    Convert a Keycloak user to DB payload.

    Mapping:
        userId   -> id
        email    -> email
        username -> email  (we ignore fullName on purpose)
    """
    user_id = (user.get("userId") or "").strip()
    email = (user.get("email") or "").strip()

    if not user_id:
        raise ValueError("Missing userId (cannot create primary key).")

    if not email:
        raise ValueError(f"Missing email for userId={user_id}")

    email_normalized = email.strip().lower()

    return {
        "id": user_id,              # DB PK (for NEW rows)
        "username": email_normalized,
        "email": email_normalized,
    }

# -----------------------------
# Import logic
# -----------------------------

def import_users(users_json: str) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    users: List[Dict] = json.loads(users_json)

    msg = f"Found {len(users)} users to import"
    print(msg)
    logger.info(msg)

    created: List[Dict] = []           # new inserts
    updated_existing: List[Dict] = []  # existing rows updated
    failed: List[Dict] = []            # errors

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for i, raw_user in enumerate(users):
                n = i + 1

                display_name = (
                    (raw_user.get("fullName") or "").strip()
                    or (raw_user.get("email") or "").strip()
                    or (raw_user.get("userId") or "").strip()
                    or f"index_{n}"
                )

                # 1) Convert & validate (ensures we have userId + email)
                try:
                    payload = convert_keycloak_user(raw_user)
                except ValueError as e:
                    err_str = str(e)
                    print(f"✗ [{n}/{len(users)}] Invalid user '{display_name}': {err_str}")
                    logger.error(
                        "Skipping invalid user [%s/%s]: %s | Error: %s",
                        n,
                        len(users),
                        display_name,
                        err_str,
                    )
                    failed.append({"raw": raw_user, "error": err_str})
                    continue

                user_id = payload["id"]
                email = payload["email"]       # normalized lower
                username = payload["username"]

                # 2) EMAIL-FIRST → check if a user with this email exists
                try:
                    cur.execute(
                        """
                        SELECT id, username, email
                        FROM users
                        WHERE lower(email) = %s
                        """,
                        (email,),
                    )
                    existing = cur.fetchone()
                except Exception as e:
                    conn.rollback()
                    err_str = f"DB error looking up email={email}: {e}"
                    print(f"✗ [{n}/{len(users)}] {err_str}")
                    logger.error(err_str)
                    failed.append({"raw": raw_user, "error": err_str})
                    continue

                if existing:
                    existing_id = existing["id"]
                    existing_username = existing.get("username")
                    existing_email = (existing.get("email") or "").strip().lower()

                    print(
                        f"↻ [{n}/{len(users)}] Email exists, updating username/email: "
                        f"{email} (existing id={existing_id})"
                    )

                    # Already correct → just record and skip
                    if existing_username == email and existing_email == email:
                        logger.info(
                            "User id=%s already has username=email (%s), skipping update.",
                            existing_id,
                            email,
                        )
                        updated_existing.append(
                            {
                                "id": existing_id,
                                "email": email,
                                "reason": "already_correct",
                            }
                        )
                        continue

                    # Update existing row by email
                    try:
                        cur.execute(
                            """
                            UPDATE users
                            SET username = %s,
                                email = %s
                            WHERE id = %s
                            """,
                            (email, email, existing_id),
                        )
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        err_str = f"Failed to update user id={existing_id} by email={email}: {e}"
                        print(f"✗ [{n}/{len(users)}] {err_str}")
                        logger.error(err_str)
                        failed.append({"raw": raw_user, "error": err_str})
                        continue

                    updated_existing.append(
                        {"id": existing_id, "email": email, "reason": "by_email"}
                    )
                    continue  # done with this user

                # 3) EMAIL NOT FOUND → try INSERT as new user
                try:
                    cur.execute(
                        """
                        INSERT INTO users (id, username, email)
                        VALUES (%s, %s, %s)
                        """,
                        (user_id, username, email),
                    )
                    conn.commit()

                    created.append({"id": user_id, "email": email})
                    print(
                        f"✓ [{n}/{len(users)}] Created NEW user {username} ({email})"
                    )
                    logger.info(
                        "Created new user [%s/%s]: id=%s, email=%s",
                        n,
                        len(users),
                        user_id,
                        email,
                    )

                except pg_errors.UniqueViolation:
                    # PK conflict: id already exists → update that row by id instead
                    conn.rollback()
                    print(
                        f"↻ [{n}/{len(users)}] Insert hit PK conflict for id={user_id}. "
                        f"Updating username/email by id."
                    )
                    logger.warning(
                        "PK conflict on insert (users_pkey) for id=%s, updating instead",
                        user_id,
                    )
                    try:
                        cur.execute(
                            """
                            UPDATE users
                            SET username = %s,
                                email = %s
                            WHERE id = %s
                            """,
                            (email, email, user_id),
                        )
                        conn.commit()
                        updated_existing.append(
                            {"id": user_id, "email": email, "reason": "by_id_conflict"}
                        )
                    except Exception as e2:
                        conn.rollback()
                        err_str = (
                            f"Failed to update existing user by id after PK conflict "
                            f"(id={user_id}, email={email}): {e2}"
                        )
                        print(f"✗ [{n}/{len(users)}] {err_str}")
                        logger.error(err_str)
                        failed.append({"raw": raw_user, "error": err_str})

                except Exception as e:
                    conn.rollback()
                    err_str = f"Failed to insert user id={user_id}, email={email}: {e}"
                    print(f"✗ [{n}/{len(users)}] {err_str}")
                    logger.error(err_str)
                    failed.append({"raw": raw_user, "error": err_str})

    finally:
        conn.close()

    # Summary
    print("\n" + "=" * 60)
    print("User Import Summary")
    print(f"  New users created      : {len(created)}")
    print(f"  Existing users updated : {len(updated_existing)}")
    print(f"  Failed                 : {len(failed)}")

    logger.info(
        "User import summary | Created: %s | Updated existing: %s | Failed: %s",
        len(created),
        len(updated_existing),
        len(failed),
    )

    # Audit files
    logs_root = PROJECT_ROOT / "logs" / "import_keycloak_users"
    logs_root.mkdir(parents=True, exist_ok=True)

    if created:
        out_path = logs_root / "created_users.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(created, f, indent=2)
        logger.info("Saved %s created users to %s", len(created), out_path)

    if updated_existing:
        out_path = logs_root / "updated_users.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(updated_existing, f, indent=2)
        logger.info("Saved %s updated users to %s", len(updated_existing), out_path)

    if failed:
        out_path = logs_root / "failed_users.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(failed, f, indent=2)
        logger.warning("Saved %s failed users to %s", len(failed), out_path)

    return created, updated_existing, failed

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

    created, updated, failed = import_users(json_raw)

    logger.info(
        "User import finished. Created=%s, Updated=%s, Failed=%s",
        len(created),
        len(updated),
        len(failed),
    )
