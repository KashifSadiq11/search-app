#!/usr/bin/env python3
import json
from pathlib import Path
from collections import Counter

# --- Load your same json file
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.algolio_products.json"

def check_duplicate_product_ids():
    if not DATA_FILE.exists():
        print(f"❌ JSON not found: {DATA_FILE}")
        return

    print(f"📄 Loading JSON: {DATA_FILE}")
    products = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    # collect all productId
    product_ids = [p.get("productId") for p in products if p.get("productId")]

    # count duplicates
    counts = Counter(product_ids)
    duplicates = {pid: count for pid, count in counts.items() if count > 1}

    print("\n===============================")
    print("🔍 Duplicate productId report")
    print("===============================")
    print(f"Total records: {len(products)}")
    print(f"Unique productId: {len(counts)}")
    print(f"Duplicate productIds found: {len(duplicates)}")

    if duplicates:
        print("\n📌 Duplicate productId values:")
        for pid, count in duplicates.items():
            print(f"  {pid} → {count} times")
    else:
        print("\n✅ No duplicate productId found!")

if __name__ == "__main__":
    check_duplicate_product_ids()
