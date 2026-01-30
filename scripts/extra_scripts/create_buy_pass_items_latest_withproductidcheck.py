import json
import sys
import os
from typing import Dict
from pathlib import Path

import psycopg2
import requests

# Optional: load .env from project root if python-dotenv is installed
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# -------------------------
# Project root & sys.path
# -------------------------

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

API_URL = "http://localhost:8000"

# Path to your JSON data file
DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.algolio_products.json"

# Logger for this script (logs/create_buy_pass_items_latest/*.log)
logger = get_rotating_logger(
    name="create_buy_pass_items_latest",
    folder_name="create_buy_pass_items_latest",
)


# ---------- DB HELPERS ----------


def get_db_connection():
    """
    Create a PostgreSQL connection using DATABASE_URL env var.

    This is required (no fallback). If not set, we fail fast.
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        msg = "DATABASE_URL environment variable is not set. Cannot connect to database."
        print(msg)
        logger.error(msg)
        raise RuntimeError(msg)

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True  # we only run SELECTs here
        logger.info("Successfully connected to PostgreSQL for deduplication checks.")
        return conn
    except Exception as exc:
        msg = f"Failed to connect to PostgreSQL: {exc}"
        print(msg)
        logger.exception(msg)
        raise


def item_exists_in_db(conn, buypass_id: str, product_id: str) -> bool:
    """
    Check if an item with this product_id already exists
    by searching inside the description JSON metadata.

    We search for:
      "product_id":"<product_id>"
    inside description.

    This is a pure SELECT (read-only), it does NOT modify any data.
    """
    # If product_id is missing, treat as not existing; we still import.
    if not product_id:
        return False

    pattern_product = f'%\"product_id\":\"{product_id}\"%'

    query = """
        SELECT 1
        FROM public.items
        WHERE description LIKE %s
        LIMIT 1;
    """

    try:
        with conn.cursor() as cur:
            cur.execute(query, (pattern_product,))
            exists = cur.fetchone() is not None
            return exists
    except Exception as exc:
        # Enterprise-style: fail loudly, do NOT silently ignore
        logger.exception(
            "Error while checking existing item in DB (product_id=%s): %s",
            product_id,
            exc,
        )
        raise


# ---------- POPULARITY & CONVERTER ----------


def calculate_popularity_score(product: Dict) -> float:
    """Calculate popularity score based on views, sales, and rating."""
    # Use "or 0" so that None (JSON null) becomes 0
    view_count = product.get("viewCount") or 0            
    sold_count = (product.get("soldCount") or 0) + (     
        product.get("totalSold") or 0
    )
    rating = product.get("productRating") or 0            

    # Normalize and weight different factors
    view_score = min(view_count / 100.0, 1.0) * 0.3       # 30% weight
    sold_score = min(sold_count / 50.0, 1.0) * 0.5        # 50% weight
    rating_score = (rating / 5.0) * 0.2                   # 20% weight

    popularity = view_score + sold_score + rating_score
    return min(popularity, 1.0)  # Cap at 1.0


def convert_buypass_to_recengine(buypass_product: Dict) -> Dict:
    """Convert BuyPass product format to RecEngine format with ALL fields."""
    # Extract brand from seller name or specifications
    brand = buypass_product.get("seller", {}).get("name", "Unknown Brand")

    # Find brand in specifications if available
    for spec in buypass_product.get("specifications", []):
        if spec.get("key") in ["Brand", "Brand Name"]:
            brand = spec.get("value")
            break

    # Determine if in stock based on stockCount
    in_stock = buypass_product.get("stockCount", 0) > 0

    # Convert prices (BuyPass uses integer, we need float)
    price = float(buypass_product.get("productPrice", 0))
    sale_price = float(buypass_product.get("productSalePrice", price))

    # Calculate actual discount if sale price differs
    if sale_price < price and price > 0:
        actual_discount = ((price - sale_price) / price) * 100
    else:
        actual_discount = float(buypass_product.get("productDiscount", 0))

    # Get the main category name and full path
    category = buypass_product.get("categoryName", "General")
    category_path = buypass_product.get("categoryPath", category)

    # Clean and combine descriptions
    import re
    description = buypass_product.get("productDescription", "") or ""
    main_description = buypass_product.get("productMainDescription", "") or ""

    # Remove HTML tags
    description = re.sub(r"<.*?>", "", description).strip()
    main_description = re.sub(r"<.*?>", "", main_description).strip()

    # Combine descriptions intelligently
    if main_description and main_description != description:
        full_description = f"{main_description}. {description}".strip()
    else:
        full_description = description or buypass_product.get("productName", "")

    # Extract useful info from specifications
    specs_dict: Dict[str, str] = {}
    for spec in buypass_product.get("specifications", []):
        key = spec.get("key", "")
        normalized_key = key.lower().replace(" ", "_")
        specs_dict[normalized_key] = spec.get("value", "")

    # Extract variant information
    variants_info = []
    for variant in buypass_product.get("variants", []):
        variants_info.append(
            {
                "type": variant.get("type", ""),
                "value": variant.get("value", {}).get("name", ""),
            }
        )

    # Map productStatus - BuyPass uses "active", "inactive", "pending"
    status_mapping = {
        "active": "active",
        "inactive": "inactive",
        "pending": "active",  # Treat pending as active for now
    }
    product_status = status_mapping.get(
        buypass_product.get("productStatus", "active"), "active"
    )

    # Store additional metadata as JSON string for the description field
    metadata = {
        "buypass_id": buypass_product.get("buyPassId", ""),
        "product_id": buypass_product.get("productId", ""),
        "image_url": buypass_product.get("productImage", ""),
        "images": buypass_product.get("productImages", []),
        "seller_info": buypass_product.get("seller", {}),
        "location": buypass_product.get("_geoloc", {}),
        "specifications": specs_dict,
        "variants": variants_info,
        "condition": buypass_product.get("productCondition", "New"),
        "total_reviews": buypass_product.get("productTotalReviews", 0),
        "category_path": category_path,
        "warranty": buypass_product.get("warranty", {}),
        "delivery_type": buypass_product.get("productDeliveryType", "standard"),
        "stock_count": buypass_product.get("stockCount", 0),
        "view_count": buypass_product.get("viewCount", 0),
        "sold_count": buypass_product.get("soldCount", 0)
        + buypass_product.get("totalSold", 0),
    }

    # Create enhanced description with metadata hint
    enhanced_description = (
        f"{full_description}\n"
        f"[Metadata: {json.dumps(metadata, separators=(',', ':'))}]"
    ).strip()

    # Convert to RecEngine format with ALL relevant fields
    rec_engine_product = {
        # Basic fields
        "title": buypass_product.get("productName", "Unknown Product"),
        "description": enhanced_description[:5000],  # Limit description length
        "category": category,
        "brand": brand,
        "price": sale_price if sale_price < price else price,  # Use sale price if lower
        "in_stock": in_stock,
        # Enhanced ML fields
        "product_status": product_status,
        "category_name": category,  # Alias for ML
        "product_rating": float(buypass_product.get("productRating", 0)),
        "discount": actual_discount,
        # Additional fields for better recommendations
        "popularity_score": calculate_popularity_score(buypass_product),
    }

    return rec_engine_product


# ---------- IMPORT LOGIC ----------


def import_products(products_json: str, db_conn):
    """Import products from JSON data, skipping already-existing items."""
    products = json.loads(products_json)

    print(f"Found {len(products)} products to import")
    logger.info("Found %s products to import", len(products))

    successful_imports = []
    failed_imports = []
    skipped_imports = []

    for i, product in enumerate(products):
        product_name = product.get("productName", "Unknown Product")
        buypass_id = product.get("buyPassId", "")
        product_id = product.get("productId", "")

        try:
            # --- Deduplication check in DB (read-only) ---
            if item_exists_in_db(db_conn, buypass_id, product_id):
                msg = (
                    f"↷ [{i+1}/{len(products)}] Skipped (already exists): "
                    f"{product_name[:50]} (buypass_id={buypass_id}, product_id={product_id})"
                )
                print(msg)
                logger.info(
                    "Skipped existing item [%s/%s]: %s (buypass_id=%s, product_id=%s)",
                    i + 1,
                    len(products),
                    product_name,
                    buypass_id,
                    product_id,
                )
                skipped_imports.append(
                    {"product": product_name, "buypass_id": buypass_id, "product_id": product_id}
                )
                continue

            # Convert to RecEngine format
            rec_product = convert_buypass_to_recengine(product)

            # Send to API (this endpoint inserts into DB)
            response = requests.post(f"{API_URL}/items/", json=rec_product)

            if response.status_code == 200:
                result = response.json()
                successful_imports.append(result)

                print(f"✓ [{i+1}/{len(products)}] Imported: {rec_product['title'][:50]}")
                logger.info(
                    "Imported item [%s/%s]: %s",
                    i + 1,
                    len(products),
                    rec_product["title"],
                )
            else:
                failed_imports.append(
                    {
                        "product": rec_product["title"],
                        "error": response.text,
                    }
                )

                print(f"✗ [{i+1}/{len(products)}] Failed: {rec_product['title'][:50]}")
                print(f"  Error: {response.text}")

                logger.error(
                    "Failed importing item [%s/%s]: %s | Status: %s | Error: %s",
                    i + 1,
                    len(products),
                    rec_product["title"],
                    response.status_code,
                    response.text[:300],
                )

        except Exception as e:
            failed_imports.append(
                {
                    "product": product_name,
                    "error": str(e),
                }
            )

            print(f"✗ [{i+1}/{len(products)}] Error: {e}")
            logger.exception(
                "Exception while importing item [%s/%s]: %s",
                i + 1,
                len(products),
                product_name,
            )

    # Summary
    print("\n" + "=" * 60)
    print("Import Summary:")
    print(f"  Successful: {len(successful_imports)}")
    print(f"  Skipped (already in DB): {len(skipped_imports)}")
    print(f"  Failed: {len(failed_imports)}")

    logger.info(
        "Import summary | Successful: %s | Skipped: %s | Failed: %s",
        len(successful_imports),
        len(skipped_imports),
        len(failed_imports),
    )

    if failed_imports:
        print("\nFailed imports:")
        logger.warning(
            "There were %s failed imports. Showing up to 5 examples.",
            len(failed_imports),
        )
        for fail in failed_imports[:5]:  # Show first 5 failures
            print(f"  - {fail['product']}: {fail['error'][:100]}")
            logger.warning(
                "Failed item: %s | Error: %s",
                fail["product"],
                fail["error"][:300],
            )

    # Save imported item IDs
    if successful_imports:
        with open("imported_items.json", "w", encoding="utf-8") as f:
            json.dump(successful_imports, f, indent=2)

        print("\nImported items saved to imported_items.json")
        logger.info("Imported items saved to imported_items.json")

    return successful_imports, failed_imports, skipped_imports


# ---------- MAIN ----------


if __name__ == "__main__":
    # Read from JSON file (project-relative)
    if not DATA_FILE.exists():
        msg = f"Data file not found: {DATA_FILE}"
        print(msg)
        logger.error(msg)
        raise FileNotFoundError(msg)

    print(f"Loading products from {DATA_FILE}")
    logger.info("Loading products from %s", DATA_FILE)

    with DATA_FILE.open("r", encoding="utf-8") as f:
        buypass_products = f.read()

    # Connect to DB for deduplication checks
    db_conn = get_db_connection()

    try:
        # Import the products (with dedup)
        successful, failed, skipped = import_products(buypass_products, db_conn=db_conn)
    finally:
        db_conn.close()
        logger.info("Closed PostgreSQL connection for deduplication checks.")

    # Test search if items were imported
    if successful:
        print("\n" + "=" * 60)
        print("Testing search with imported items...")
        logger.info("Testing search with imported items...")

        # Test basic search
        try:
            test_search = requests.post(
                f"{API_URL}/search/enhanced/",
                json={"query": "phone", "limit": 5},
            )

            if test_search.status_code == 200:
                results = test_search.json()
                # Adjust according to your /search/enhanced response schema
                total = results.get("total") or len(results.get("items", []))

                print(f"Search for 'phone' found {total} items")
                logger.info("Search for 'phone' found %s items", total)
            else:
                print("Search failed")
                logger.error(
                    "Search API failed | Status: %s | Error: %s",
                    test_search.status_code,
                    test_search.text[:300],
                )

        except Exception as e:
            print("Search test failed:", e)
            logger.exception("Search test failed with exception")

        print("\nItems are ready for recommendations!")
        print("Create users and interactions to test recommendation features.")
        logger.info(
            "Items are ready for recommendations. Create users and interactions to test recommendation features."
        )
