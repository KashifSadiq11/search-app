#!/usr/bin/env python3
"""
Count how many products in JSON must be deleted due to duplicate productId.

This script does NOT modify or delete anything.
It only reports:

- Total productId entries
- Unique productId entries
- How many must be removed
"""

import json
from pathlib import Path
from collections import Counter

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "BuyPASS.algolio_products.json"

def count_duplicates():
    if not DATA_FILE.exists():
        print(f"❌ JSON not found: {DATA_FILE}")
        return

    print(f"📄 Loading JSON: {DATA_FILE}\n")
    products = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    product_ids = [p.get("productId", "").strip() for p in products if p.get("productId")]
    
    total = len(product_ids)
    unique = len(set(product_ids))
    to_remove = total - unique  # number of removals required

    counter = Counter(product_ids)
    duplicate_groups = {pid: c for pid, c in counter.items() if c > 1}

    print("============== DUPLICATE SUMMARY ==============")
    print(f"Total productId entries:     {total}")
    print(f"Unique productIds:           {unique}")
    print(f"❗ Must delete to clean:      {to_remove}")
    print("===============================================\n")
    
    print(f"Duplicate groups found: {len(duplicate_groups)}")
    print("Each group means 1 ID exists more than once.\n")

    # If you want, print top 10 highest duplicates
    print("Top duplicate productIDs (max 10):\n")
    for pid, count in list(duplicate_groups.items())[:10]:
        print(f"  {pid} -> {count}x")

    print("\n🔍 Script finished — No files modified.")

if __name__ == "__main__":
    count_duplicates()
