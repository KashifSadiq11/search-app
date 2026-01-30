#!/usr/bin/env python3
"""
Analyze interaction distribution for both users and items.
----------------------------------------------------------
Users:
  ✓ Exactly 20
  ✓ Exactly 40
  ✓ Between 21–39
  ✓ Below 20
  ✓ Above 40

Items:
  ✓ Exactly 1 interaction
  ✓ More than one interaction

Interactions:
  ✓ Count per interaction_type (view / click / purchase / other)
  ✓ Count per rating value
"""

import sys
from pathlib import Path
from sqlalchemy import func

# ==========================================================
#  Inject project root into import path
# ==========================================================
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ==========================================================
#  Now imports will work
# ==========================================================
from services.engine.database import get_db
from services.engine.models import Interaction


def analyze_distribution() -> None:
    db = next(get_db())

    # ================= USER INTERACTION COUNTS =================
    user_results = (
        db.query(
            Interaction.user_id,
            func.count(Interaction.id).label("cnt"),
        )
        .group_by(Interaction.user_id)
        .all()
    )

    # counters for users
    exactly_20 = 0
    exactly_40 = 0
    between_20_40 = 0
    below_20 = 0
    above_40 = 0

    for _, count in user_results:
        if count == 20:
            exactly_20 += 1
        elif count == 40:
            exactly_40 += 1
        elif 20 < count < 40:
            between_20_40 += 1
        elif count < 20:
            below_20 += 1
        elif count > 40:
            above_40 += 1

    # ================= ITEM INTERACTION COUNTS =================
    item_results = (
        db.query(
            Interaction.item_id,
            func.count(Interaction.id).label("cnt"),
        )
        .group_by(Interaction.item_id)
        .all()
    )

    one_interaction_items = sum(1 for _, count in item_results if count == 1)
    many_interaction_items = sum(1 for _, count in item_results if count > 1)

    # ================= INTERACTION TYPE COUNTS =================
    # e.g. view / click / purchase
    type_results = (
        db.query(
            Interaction.interaction_type,
            func.count(Interaction.id).label("cnt"),
        )
        .group_by(Interaction.interaction_type)
        .all()
    )

    # ================= RATING DISTRIBUTION =====================
    rating_results = (
        db.query(
            Interaction.rating,
            func.count(Interaction.id).label("cnt"),
        )
        .group_by(Interaction.rating)
        .order_by(Interaction.rating)
        .all()
    )

    # ================= PRINT REPORT =================
    print("\n================= USER DISTRIBUTION =================")
    print(f"Total Users Analyzed            : {len(user_results)}")
    print(f"Users with EXACTLY 20           : {exactly_20}")
    print(f"Users with EXACTLY 40           : {exactly_40}")
    print(f"Users 21–39 (in-between)        : {between_20_40}")
    print(f"Users below 20                  : {below_20}")
    print(f"Users above 40                  : {above_40}")

    print("\n================= ITEM DISTRIBUTION =================")
    print(f"Total Items Analyzed            : {len(item_results)}")
    print(f"Items with EXACTLY 1 interaction: {one_interaction_items}")
    print(f"Items with >1 interactions      : {many_interaction_items}")

    print("\n============ INTERACTION TYPE DISTRIBUTION ============")
    total_interactions = sum(cnt for _, cnt in type_results)
    print(f"Total Interactions              : {total_interactions}")
    for interaction_type, cnt in sorted(
        type_results, key=lambda x: (str(x[0]) if x[0] is not None else "")
    ):
        label = interaction_type if interaction_type is not None else "<NULL>"
        print(f"Type='{label:<9}' -> {cnt}")

    print("\n================= RATING DISTRIBUTION =================")
    for rating, cnt in rating_results:
        label = rating if rating is not None else "<NULL>"
        print(f"Rating={label} -> {cnt} interactions")

    print("====================================================\n")


if __name__ == "__main__":
    analyze_distribution()
