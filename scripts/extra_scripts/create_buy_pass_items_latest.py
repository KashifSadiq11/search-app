import json
import requests
from typing import List, Dict
from pathlib import Path

API_URL = "http://localhost:8000"

# Resolve project root (rec-engine/)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Path to your JSON data file
DATA_FILE = PROJECT_ROOT / "data" / "BuyPASS.algolio_products.json"


def calculate_popularity_score(product: Dict) -> float:
    """Calculate popularity score based on views, sales, and rating."""
    view_count = product.get("viewCount", 0)
    sold_count = product.get("soldCount", 0) + product.get("totalSold", 0)
    rating = product.get("productRating", 0)

    # Normalize and weight different factors
    view_score = min(view_count / 100, 1.0) * 0.3   # 30% weight
    sold_score = min(sold_count / 50, 1.0) * 0.5    # 50% weight
    rating_score = (rating / 5.0) * 0.2             # 20% weight

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


def import_products(products_json: str):
    """Import products from JSON data."""
    products = json.loads(products_json)
    print(f"Found {len(products)} products to import")

    successful_imports = []
    failed_imports = []

    for i, product in enumerate(products):
        try:
            # Convert to RecEngine format
            rec_product = convert_buypass_to_recengine(product)

            # Send to API
            response = requests.post(f"{API_URL}/items/", json=rec_product)

            if response.status_code == 200:
                result = response.json()
                successful_imports.append(result)
                print(f"✓ [{i+1}/{len(products)}] Imported: {rec_product['title'][:50]}")
            else:
                failed_imports.append(
                    {
                        "product": rec_product["title"],
                        "error": response.text,
                    }
                )
                print(f"✗ [{i+1}/{len(products)}] Failed: {rec_product['title'][:50]}")
                print(f"  Error: {response.text}")

        except Exception as e:
            failed_imports.append(
                {
                    "product": product.get("productName", "Unknown"),
                    "error": str(e),
                }
            )
            print(f"✗ [{i+1}/{len(products)}] Error: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("Import Summary:")
    print(f"  Successful: {len(successful_imports)}")
    print(f"  Failed: {len(failed_imports)}")

    if failed_imports:
        print("\nFailed imports:")
        for fail in failed_imports[:5]:  # Show first 5 failures
            print(f"  - {fail['product']}: {fail['error'][:100]}")

    # Save imported item IDs
    if successful_imports:
        with open("imported_items.json", "w", encoding="utf-8") as f:
            json.dump(successful_imports, f, indent=2)
        print("\nImported items saved to imported_items.json")

    return successful_imports, failed_imports


if __name__ == "__main__":
    # Read from JSON file (project-relative)
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Data file not found: {DATA_FILE}")

    with DATA_FILE.open("r", encoding="utf-8") as f:
        buypass_products = f.read()

    # Import the products
    successful, failed = import_products(buypass_products)

    # Test search if items were imported
    if successful:
        print("\n" + "=" * 60)
        print("Testing search with imported items...")

        # Test basic search
        test_search = requests.post(
            f"{API_URL}/search/enhanced/",
            json={"query": "phone", "limit": 5},
        )

        if test_search.status_code == 200:
            results = test_search.json()
            # Adjust according to your /search/enhanced response schema
            total = results.get("total") or len(results.get("items", []))
            print(f"Search for 'phone' found {total} items")

        print("\nItems are ready for recommendations!")
        print("Create users and interactions to test recommendation features.")
