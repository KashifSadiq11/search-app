import requests
import json
import random

API_URL = "http://localhost:8000"

def create_enhanced_items():
    """Create items with all fields needed for ML features"""
    
    enhanced_items = [
        {
            "title": "AI-Powered Smart Speaker",
            "description": "Voice assistant with premium sound quality",
            "category": "electronics",
            "brand": "TechCorp",
            "price": 199.99,
            "in_stock": True,
            "product_status": "active",          # Required for ML
            "category_name": "electronics",      # Required for ML
            "product_rating": 4.7,               # Required for ML
            "discount": 20.0,                    # Required for ML
            "popularity_score": 0.85             # Will be set automatically
        },
        {
            "title": "Organic Green Tea Collection",
            "description": "Premium tea set with antioxidants",
            "category": "food",
            "brand": "HealthyChoice",
            "price": 29.99,
            "in_stock": True,
            "product_status": "active",
            "category_name": "food",
            "product_rating": 4.3,
            "discount": 10.0
        },
        {
            "title": "Professional Yoga Mat",
            "description": "Extra thick, non-slip exercise mat",
            "category": "sports",
            "brand": "FitnessPro",
            "price": 59.99,
            "in_stock": True,
            "product_status": "active",
            "category_name": "sports",
            "product_rating": 4.6,
            "discount": 15.0
        },
        {
            "title": "Wireless Noise-Canceling Headphones",
            "description": "Premium audio with 30-hour battery",
            "category": "electronics",
            "brand": "AudioTech",
            "price": 249.99,
            "in_stock": True,
            "product_status": "active",
            "category_name": "electronics",
            "product_rating": 4.8,
            "discount": 25.0
        },
        {
            "title": "Smart Home Security Camera",
            "description": "4K resolution with night vision",
            "category": "home",
            "brand": "SecureHome",
            "price": 149.99,
            "in_stock": True,
            "product_status": "active",
            "category_name": "home",
            "product_rating": 4.4,
            "discount": 30.0
        }
    ]
    
    created = []
    for item in enhanced_items:
        response = requests.post(f"{API_URL}/items/", json=item)
        if response.status_code == 200:
            created.append(response.json())
            print(f"✓ Created enhanced item: {item['title']}")
        else:
            print(f"✗ Failed: {item['title']} - {response.text}")
    
    return created

# Create the enhanced items
created_items = create_enhanced_items()

# Test ML features with the new items
print("\n" + "="*50)
print("Testing ML features with enhanced items...")

# Test semantic search (if ML is enabled)
search_response = requests.post(
    f"{API_URL}/search/semantic/",
    json={"query": "wireless audio device", "limit": 5}
)
if search_response.status_code == 200:
    results = search_response.json()
    print(f"\nSemantic search found {results['total']} items")
    for item in results['items'][:3]:
        print(f"  - {item['title']}")

print("\nEnhanced items created successfully!")
print("These items have all fields needed for ML-powered features.")