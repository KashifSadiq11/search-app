#!/usr/bin/env python3
"""
Import ONLY missing BuyPass products into the items table.
Parallel batch insertion for high speed.

USES API ENDPOINTS ONLY - no direct database access.
"""

import sys
import json
from pathlib import Path
from typing import Dict, List, Any, Set
from itertools import islice
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import requests

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# from services.engine.database import get_db
# from services.engine.models import Item

try:
    from services.engine.config import settings
except ImportError:
    settings = None


API_URL = "http://localhost:8016"  
DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.algolio_products.json"

BATCH_SIZE = 150
MAX_THREADS = 12


# ------------ utilities (null/None-safe) ------------

def calculate_popularity_score(product: Dict) -> float:
    def to_number(value) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    view_count = to_number(product.get("viewCount"))
    sold_count = to_number(product.get("soldCount")) + to_number(product.get("totalSold"))
    rating = to_number(product.get("productRating"))

    view_score = min(view_count / 100.0, 1.0) * 0.3
    sold_score = min(sold_count / 50.0, 1.0) * 0.5
    rating_score = (rating / 5.0) * 0.2

    popularity = view_score + sold_score + rating_score
    return min(popularity, 1.0)


def convert_buypass_to_recengine(buypass_product: Dict) -> Dict:
    import re

    def to_float(value, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    seller = buypass_product.get("seller") or {}
    brand = seller.get("name", "Unknown Brand") or "Unknown Brand"

    for spec in (buypass_product.get("specifications") or []):
        if spec.get("key") in ["Brand", "Brand Name"]:
            brand = spec.get("value") or brand
            break

    in_stock = (buypass_product.get("stockCount") or 0) > 0

    price = to_float(buypass_product.get("productPrice"), 0.0)
    sale_price = to_float(buypass_product.get("productSalePrice"), price)

    if sale_price < price and price > 0:
        actual_discount = ((price - sale_price) / price) * 100
    else:
        actual_discount = to_float(buypass_product.get("productDiscount"), 0.0)

    category = buypass_product.get("categoryName") or "General"
    category_path = buypass_product.get("categoryPath") or category

    raw_desc = buypass_product.get("productDescription") or ""
    raw_main_desc = buypass_product.get("productMainDescription") or ""

    description = re.sub(r"<.*?>", "", raw_desc).strip()
    main_description = re.sub(r"<.*?>", "", raw_main_desc).strip()

    if main_description and main_description != description:
        full_description = f"{main_description}. {description}".strip()
    else:
        full_description = description or (buypass_product.get("productName") or "")

    specs_dict = {
        (spec.get("key", "") or "").lower().replace(" ", "_"): spec.get("value", "") or ""
        for spec in (buypass_product.get("specifications") or [])
    }

    variants_info = [
        {
            "type": variant.get("type", "") or "",
            "value": (variant.get("value") or {}).get("name", "") or "",
        }
        for variant in (buypass_product.get("variants") or [])
    ]

    status_mapping = {"active": "active", "inactive": "inactive", "pending": "active"}
    product_status = status_mapping.get(
        buypass_product.get("productStatus", "active"), "active"
    )

    metadata = {
        "buypass_id": buypass_product.get("buyPassId") or "",
        "product_id": buypass_product.get("productId") or "",
        "image_url": buypass_product.get("productImage") or "",
        "images": buypass_product.get("productImages") or [],
        "seller_info": seller,
        "location": buypass_product.get("_geoloc") or {},
        "specifications": specs_dict,
        "variants": variants_info,
        "condition": buypass_product.get("productCondition") or "New",
        "total_reviews": buypass_product.get("productTotalReviews") or 0,
        "category_path": category_path,
        "warranty": buypass_product.get("warranty") or {},
        "delivery_type": buypass_product.get("productDeliveryType") or "standard",
        "stock_count": buypass_product.get("stockCount") or 0,
        "view_count": buypass_product.get("viewCount") or 0,
        "sold_count": (buypass_product.get("soldCount") or 0)
                      + (buypass_product.get("totalSold") or 0),
    }

    enhanced_description = (
        f"{full_description}\n"
        f"[Metadata: {json.dumps(metadata, separators=(',', ':'))}]"
    ).strip()

    return {
        "title": buypass_product.get("productName") or "Unknown Product",
        "description": enhanced_description[:5000],
        "category": category,
        "brand": brand,
        "price": sale_price if sale_price < price else price,
        "in_stock": in_stock,
        "product_status": product_status,
        "category_name": category,
        "product_rating": to_float(buypass_product.get("productRating"), 0.0),
        "discount": actual_discount,
        "popularity_score": calculate_popularity_score(buypass_product),
    }


def load_existing_item_ids_from_api() -> Set[str]:
    """
    Load all existing item IDs from the API instead of direct database.
    Returns a set of item IDs already in the system.
    """
    existing_ids: Set[str] = set()
    
    try:
        # Try to fetch all items from the API
        response = requests.get(f"{API_URL}/items/", timeout=30)
        
        if response.status_code == 200:
            items_data = response.json()
            
            # Handle different response formats
            if isinstance(items_data, dict) and "items" in items_data:
                # Paginated response
                items = items_data["items"]
            elif isinstance(items_data, list):
                # Direct list response
                items = items_data
            else:
                print(f"Unexpected API response format for items: {type(items_data)}")
                return existing_ids
            
            # Extract IDs from the response
            for item in items:
                item_id = str(item.get("id") or "").strip()
                if item_id:
                    existing_ids.add(item_id)
            
            print(f"Loaded {len(existing_ids)} existing items from API")
        else:
            print(f"API returned status {response.status_code} for items endpoint")
            print(f"Response: {response.text[:200]}")
            
    except requests.RequestException as e:
        print(f"Could not connect to API at {API_URL}/items/: {e}")
        print("Will assume no existing items (empty database)")
    
    return existing_ids


def load_existing_item_ids_from_api_paginated() -> Set[str]:
    """
    Alternative: Fetch items with pagination if the API supports it.
    """
    existing_ids: Set[str] = set()
    
    page = 1
    page_size = 100
    
    try:
        while True:
            # Adjust URL and parameters based on your API's pagination
            response = requests.get(
                f"{API_URL}/items/",
                params={"page": page, "size": page_size},
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"API returned status {response.status_code} on page {page}")
                break
            
            data = response.json()
            
            # Check if this is a paginated response
            if isinstance(data, dict) and "items" in data:
                items = data["items"]
                total_pages = data.get("total_pages", 0)
                
                for item in items:
                    item_id = str(item.get("id") or "").strip()
                    if item_id:
                        existing_ids.add(item_id)
                
                print(f"Page {page}/{total_pages}: Loaded {len(items)} items")
                
                # Check if we have more pages
                if page >= total_pages or not items:
                    break
                page += 1
                
            else:
                # Non-paginated response
                items = data if isinstance(data, list) else []
                for item in items:
                    item_id = str(item.get("id") or "").strip()
                    if item_id:
                        existing_ids.add(item_id)
                
                print(f"Loaded {len(items)} items from non-paginated API")
                break
                
    except requests.RequestException as e:
        print(f"API error: {e}")
    
    print(f"Total loaded from API: {len(existing_ids)} items")
    return existing_ids


def chunked(iterable, size):
    it = iter(iterable)
    while True:
        batch = list(islice(it, size))
        if not batch:
            return
        yield batch

# ------------ Main logic ------------

def main():
    
    if not DATA_FILE.exists():
        print(f"JSON file not found: {DATA_FILE}")
        return

    print(f"Loading JSON from: {DATA_FILE}")
    products: List[Dict] = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    print(f"Total JSON products: {len(products)}")

    # Get existing item IDs from API
    print("\n📡 Fetching existing items from API...")
    existing_ids = load_existing_item_ids_from_api()
    
    # If no items found with simple endpoint, try paginated
    if len(existing_ids) == 0:
        print("Trying paginated API endpoint...")
        existing_ids = load_existing_item_ids_from_api_paginated()

    missing_products: List[Dict] = []
    seen_missing_ids: Set[str] = set()

    for product in products:
        pid = (product.get("productId") or "").strip()
        if not pid:
            continue
        if pid in existing_ids:
            continue
        if pid in seen_missing_ids:
            continue
        seen_missing_ids.add(pid)
        missing_products.append(product)

    missing_count = len(missing_products)

    print("\n" + "=" * 60)
    print("               MISSING ITEMS REPORT               ")
    print("=" * 60)
    print(f"Total products in JSON file     : {len(products)}")
    print(f"Items already in API (by ID)   : {len(existing_ids)}")
    print(f"New items to insert             : {missing_count}")
    print(f"Duplicates/skipped in JSON      : {len(products) - len(seen_missing_ids)}")
    print("=" * 60)

    if missing_count == 0:
        print("\n✅ No missing items to import. All products are already in the system.")
        return

    # Show preview of items to be created
    print(f"\Preview of first 5 items to be created:")
    for i, product in enumerate(missing_products[:5], 1):
        pid = product.get("productId", "N/A").strip()
        name = product.get("productName", "Unknown")[:50]
        price = product.get("productPrice", 0)
        print(f"  {i}. ID: {pid}, Name: {name}, Price: {price}")
    
    if missing_count > 5:
        print(f"  ... and {missing_count - 5} more items")

    confirm = input(f"\Insert {missing_count} items via API? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Import cancelled.")
        return

    print("\Starting parallel import via API...\n")

    lock = threading.Lock()
    inserted_ids: Set[str] = set()
    successful = 0
    failed = 0
    failed_details: List[Dict[str,Any]] = []

    def process_batch(batch, batch_index):
        nonlocal successful, failed, failed_details

        local_success = 0
        local_failed = 0
        local_inserted = set()
        local_failed_details = []

        for product in batch:
            pid = (product.get("productId") or "").strip()
            if not pid:
                local_failed += 1
                local_failed_details.append({"pid": "missing", "error": "No productId"})
                continue

            with lock:
                if pid in inserted_ids:
                    continue

            # Convert to RecEngine format
            rec_product = convert_buypass_to_recengine(product)
            rec_product["id"] = pid

            try:
                # Call API to create item
                response = requests.post(
                    f"{API_URL}/items/", 
                    json=rec_product, 
                    timeout=30,
                    headers={"Content-Type": "application/json"}
                )
            except requests.RequestException as e:
                local_failed += 1
                local_failed_details.append({"pid": pid, "error": str(e)})
                continue

            if response.status_code in (200, 201):
                local_success += 1
                local_inserted.add(pid)
            else:
                local_failed += 1
                error_detail = {
                    "pid": pid,
                    "status": response.status_code,
                    "error": response.text[:500]
                }
                local_failed_details.append(error_detail)

        # Merge thread results
        with lock:
            inserted_ids.update(local_inserted)
            successful += local_success
            failed += local_failed
            failed_details.extend(local_failed_details)

        print(f"✓ Batch {batch_index} finished → inserted {local_success}, failed {local_failed}")

    # Create batches for parallel processing
    batches = list(chunked(missing_products, BATCH_SIZE))
    total_batches = len(batches)

    print(f"\n📦 Processing {missing_count} items in {total_batches} batches "
          f"with {MAX_THREADS} parallel workers...")

    completed_batches = 0
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = [
            executor.submit(process_batch, batch, i)
            for i, batch in enumerate(batches, start=1)
        ]
        
        for future in as_completed(futures):
            completed_batches += 1
            
            # Show progress every few batches
            if completed_batches % 4 == 0 or completed_batches == total_batches:
                with lock:
                    print("-" * 50)
                    print(f"  Progress: {completed_batches}/{total_batches} batches")
                    print(f"  Created so far: {successful}")
                    print(f"  Failed so far: {failed}")
                    print("-" * 50)

    # Final summary
    print("\n" + "=" * 60)
    print("              IMPORT COMPLETE               ")
    print("=" * 60)
    print(f"✅ Successfully created: {successful} items")
    print(f"❌ Failed: {failed} items")
    print(f"📊 Success rate: {(successful/missing_count*100 if missing_count > 0 else 100):.1f}%")
    print("=" * 60)
    
    if failed_details:
        print(f"\n📋 Failed items (first 10):")
        for i, detail in enumerate(failed_details[:10], 1):
            pid = detail.get("pid", "unknown")
            status = detail.get("status", "Connection Error")
            error = detail.get("error", "Unknown error")
            print(f"  {i}. ID={pid} | Status: {status} | Error: {error[:100]}...")
        
        if len(failed_details) > 10:
            print(f"  ... and {len(failed_details) - 10} more failures")
    
    print("\n🎉 Import finished!")

if __name__ == "__main__":
    main()