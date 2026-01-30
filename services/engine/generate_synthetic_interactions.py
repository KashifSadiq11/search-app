#!/usr/bin/env python3
"""
Generate synthetic user–item interactions for training the recommendation engine.

BEHAVIOR
--------
Phase 1 (per-user interactions):
- Uses ONLY existing users and items already in the DB.
- For each user, attempts to create between MIN_INTERACTIONS_PER_USER and
  MAX_INTERACTIONS_PER_USER interactions with RANDOM items.
- Interaction types are weighted to simulate realistic behavior:
    view     -> 60%
    click    -> 30%
    purchase -> 10%
- Ratings are auto-assigned:
    view=1.0, click=3.0, purchase=5.0
- No duplicate (user_id, item_id) rows are inserted (existing ones are respected).

Phase 2 (cold-item coverage):
- Ensures every item has AT LEAST one interaction after this script completes.
- Any item with zero interactions gets exactly one assigned from a random user,
  respecting existing (user_id, item_id) pairs.

RESULT
------
✔ Every user ATTEMPTS to get 20–40 interactions without touching existing pairs
✔ Every item has interactions from AT LEAST one user after this run
✔ No duplicate (user_id, item_id)
"""

import random
import uuid
from typing import Dict, Tuple, Set
import sys
from pathlib import Path

from sqlalchemy import func

# -------------------------------------------------------------------
# Ensure project root (rec-engine/) is on sys.path so `logs` imports work
# File is: rec-engine/services/engine/generate_synthetic_interactions.py
# PROJECT_ROOT = rec-engine/
# -------------------------------------------------------------------
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[2]  # services/engine -> services -> rec-engine

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import get_db
from models import User, Item, Interaction
from logs.logging_config import get_rotating_logger  # same style as your other script

# Logger for this script (logs/generate_synthetic_interactions/*.log)
logger = get_rotating_logger(
    name="generate_synthetic_interactions",
    folder_name="generate_synthetic_interactions",
)

# ========== CONFIG ==========
MIN_INTERACTIONS_PER_USER = 20
MAX_INTERACTIONS_PER_USER = 40

# Weighted sampling for interaction types
INTERACTION_TYPE_WEIGHTS: Dict[str, float] = {
    "view": 0.6,
    "click": 0.3,
    "purchase": 0.1,
}

# Type → Rating mapping
INTERACTION_RATING_MAP: Dict[str, float] = {
    "view": 1.0,
    "click": 3.0,
    "purchase": 5.0,
}


def weighted_choice(weights: Dict[str, float]) -> str:
    """Pick one action based on probability weights."""
    actions = list(weights.keys())
    probs = list(weights.values())
    return random.choices(actions, probs, k=1)[0]


def generate_interactions() -> None:
    """Main generator."""
    db = next(get_db())
    total_phase1 = 0
    total_phase2 = 0

    # Track which users/items got *new* interactions in this run
    users_with_new_interactions: Set[str] = set()
    items_with_new_interactions: Set[str] = set()

    try:
        users = db.query(User).all()
        items = db.query(Item).all()

        if not users:
            msg = "❌ No users in DB — aborting synthetic interaction generation."
            print(msg)
            logger.error(msg)
            return

        if not items:
            msg = "❌ No items in DB — aborting synthetic interaction generation."
            print(msg)
            logger.error(msg)
            return

        msg = f"Loaded {len(users)} users, {len(items)} items."
        print(msg)
        logger.info(msg)

        # --- Global stats BEFORE generation ---
        interactions_before = db.query(func.count(Interaction.id)).scalar() or 0

        items_with_interactions_before: Set[str] = {
            str(row[0]) for row in db.query(Interaction.item_id).distinct().all()
        }
        users_with_interactions_before: Set[str] = {
            str(row[0]) for row in db.query(Interaction.user_id).distinct().all()
        }

        logger.info(
            "Before run: interactions=%s, users_with_interactions=%s, items_with_interactions=%s",
            interactions_before,
            len(users_with_interactions_before),
            len(items_with_interactions_before),
        )

        # Existing interactions to prevent duplicates
        existing_pairs: Set[Tuple[str, str]] = set()
        for user_id, item_id in db.query(Interaction.user_id, Interaction.item_id).all():
            existing_pairs.add((str(user_id), str(item_id)))

        # Items that already have at least one interaction (pre-existing or new during this run)
        items_with_interactions: Set[str] = set(items_with_interactions_before)

        # ================= PHASE 1 — PER USER =================
        print("Starting Phase 1 (per-user interactions)...")
        logger.info(
            "Starting Phase 1 (per-user interactions). Min=%s, Max=%s",
            MIN_INTERACTIONS_PER_USER,
            MAX_INTERACTIONS_PER_USER,
        )

        for u in users:
            n = random.randint(MIN_INTERACTIONS_PER_USER, MAX_INTERACTIONS_PER_USER)
            n = min(n, len(items))  # cannot exceed item count

            selected_items = random.sample(items, n)

            for item in selected_items:
                pair: Tuple[str, str] = (str(u.id), str(item.id))
                if pair in existing_pairs:
                    continue

                interaction_type = weighted_choice(INTERACTION_TYPE_WEIGHTS)
                rating = INTERACTION_RATING_MAP[interaction_type]

                db.add(
                    Interaction(
                        id=str(uuid.uuid4()),
                        user_id=u.id,
                        item_id=item.id,
                        interaction_type=interaction_type,
                        rating=rating,
                    )
                )

                existing_pairs.add(pair)
                items_with_interactions.add(str(item.id))
                users_with_new_interactions.add(str(u.id))
                items_with_new_interactions.add(str(item.id))
                total_phase1 += 1

        print(f"Phase 1 generated {total_phase1} interactions.")
        logger.info("Phase 1 generated %s interactions.", total_phase1)

        # ================= PHASE 2 — ITEM COVERAGE =================
        cold_items = [i for i in items if str(i.id) not in items_with_interactions]
        msg = f"Items without interactions after Phase 1: {len(cold_items)}"
        print(msg)
        logger.info(msg)
        print("Starting Phase 2 (cold-item coverage)...")
        logger.info("Starting Phase 2 (cold-item coverage).")

        for item in cold_items:
            # Try up to 10 random users to find a pair that does not yet exist
            for _ in range(10):
                user = random.choice(users)
                pair = (str(user.id), str(item.id))
                if pair not in existing_pairs:
                    interaction_type = weighted_choice(INTERACTION_TYPE_WEIGHTS)
                    rating = INTERACTION_RATING_MAP[interaction_type]

                    db.add(
                        Interaction(
                            id=str(uuid.uuid4()),
                            user_id=user.id,
                            item_id=item.id,
                            interaction_type=interaction_type,
                            rating=rating,
                        )
                    )

                    existing_pairs.add(pair)
                    users_with_new_interactions.add(str(user.id))
                    items_with_new_interactions.add(str(item.id))
                    total_phase2 += 1
                    break

        db.commit()

        total_generated = total_phase1 + total_phase2
        print(f"Phase 2 generated {total_phase2} cold-start interactions.")
        print(f"🎯 TOTAL GENERATED = {total_generated}")

        logger.info(
            "Phase 2 generated %s cold-start interactions. TOTAL GENERATED = %s",
            total_phase2,
            total_generated,
        )

        # --- Global stats AFTER generation (post-commit) ---
        interactions_after = db.query(func.count(Interaction.id)).scalar() or 0
        items_with_interactions_after: Set[str] = {
            str(row[0]) for row in db.query(Interaction.item_id).distinct().all()
        }
        users_with_interactions_after: Set[str] = {
            str(row[0]) for row in db.query(Interaction.user_id).distinct().all()
        }

        # ========== SUMMARY (console + logs) ==========
        print("\n" + "=" * 60)
        print("Synthetic Interaction Generation Summary")
        print("=" * 60)
        print(f"Users in DB                    : {len(users)}")
        print(f"Items in DB                    : {len(items)}")
        print(f"Interactions BEFORE            : {interactions_before}")
        print(f"Interactions AFTER             : {interactions_after}")
        print(f"New interactions CREATED       : {interactions_after - interactions_before}")
        print(f"Users with interactions BEFORE : {len(users_with_interactions_before)}")
        print(f"Users with interactions AFTER  : {len(users_with_interactions_after)}")
        print(f"Users with NEW interactions    : {len(users_with_new_interactions)}")
        print(f"Items with interactions BEFORE : {len(items_with_interactions_before)}")
        print(f"Items with interactions AFTER  : {len(items_with_interactions_after)}")
        print(f"Items with NEW interactions    : {len(items_with_new_interactions)}")
        print("=" * 60)

        logger.info("Summary: users_in_db=%s, items_in_db=%s", len(users), len(items))
        logger.info(
            "Summary: interactions_before=%s, interactions_after=%s, created=%s",
            interactions_before,
            interactions_after,
            interactions_after - interactions_before,
        )
        logger.info(
            "Summary: users_with_interactions_before=%s, after=%s, users_with_new_interactions=%s",
            len(users_with_interactions_before),
            len(users_with_interactions_after),
            len(users_with_new_interactions),
        )
        logger.info(
            "Summary: items_with_interactions_before=%s, after=%s, items_with_new_interactions=%s",
            len(items_with_interactions_before),
            len(items_with_interactions_after),
            len(items_with_new_interactions),
        )

    except Exception:
        print("❌ Synthetic interaction generation failed. Rolling back transaction.")
        logger.exception("Synthetic interaction generation failed, rolling back.")
        db.rollback()
        raise
    finally:
        db.close()
        logger.info("DB session closed for synthetic interaction generation.")


if __name__ == "__main__":
    generate_interactions()
