# 📚 **Rec Engine API Reference**

## 🌟 **Overview**

The Rec Engine API provides a complete recommendation and search platform with 19 endpoints covering user management, personalized recommendations, item similarity, advanced search, and analytics.

**Base URL:** `http://localhost:8000`  
**Documentation:** `http://localhost:8000/docs`  
**Version:** `1.0.0`

---

## 🔐 **Authentication**

Currently, the API operates without authentication for development. For production deployment, all endpoints will require API key authentication.

```bash
# Future authentication format
Authorization: Bearer YOUR_API_KEY
```

---

## 📋 **API Categories**

- [**System APIs**](#system-apis) - Health, documentation, status
- [**User Management**](#user-management) - User CRUD, preferences, insights  
- [**Item Management**](#item-management) - Item CRUD, analytics, popularity
- [**Search & Discovery**](#search--discovery) - Search, autocomplete, categories
- [**Recommendations**](#recommendations) - Personalized, similar items, email-based
- [**Interactions**](#interactions) - User behavior tracking
- [**Analytics**](#analytics) - Performance metrics, insights

---

## 🏥 **System APIs**

### **GET /** - Welcome Message
Get API welcome message and basic information.

**Request:**
```bash
GET /
```

**Response:**
```json
{
  "message": "Welcome to Rec Engine API",
  "docs": "/docs"
}
```

**cURL Example:**
```bash
curl http://localhost:8000/
```

---

### **GET /health** - Health Check
Check API health status and service availability.

**Request:**
```bash
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "service": "rec-engine"
}
```

**cURL Example:**
```bash
curl http://localhost:8000/health
```

**PowerShell Example:**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health"
```

---

### **GET /docs** - API Documentation
Access interactive API documentation (Swagger UI).

**Request:**
```bash
GET /docs
```

**Response:** Interactive HTML documentation interface

**Browser Access:**
```
http://localhost:8000/docs
```

---

## 👥 **User Management**

### **POST /users/** - Create User
Create a new user account.

**Request:**
```bash
POST /users/
Content-Type: application/json

{
  "username": "john_doe",
  "email": "john@example.com"
}
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "username": "john_doe",
  "email": "john@example.com",
  "created_at": "2024-01-15T10:30:00"
}
```

**cURL Example:**
```bash
curl -X POST "http://localhost:8000/users/" \
     -H "Content-Type: application/json" \
     -d '{"username": "john_doe", "email": "john@example.com"}'
```

**PowerShell Example:**
```powershell
$body = @{
    username = "john_doe"
    email = "john@example.com"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/users/" -Method POST -ContentType "application/json" -Body $body
```

**JavaScript Example:**
```javascript
const response = await fetch('http://localhost:8000/users/', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    username: 'john_doe',
    email: 'john@example.com'
  })
});
const user = await response.json();
```

**Python Example:**
```python
import requests

response = requests.post('http://localhost:8000/users/', json={
    'username': 'john_doe',
    'email': 'john@example.com'
})
user = response.json()
```

---

### **GET /users/{user_id}** - Get User
Retrieve user details by ID.

**Request:**
```bash
GET /users/550e8400-e29b-41d4-a716-446655440000
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "username": "john_doe",
  "email": "john@example.com",
  "created_at": "2024-01-15T10:30:00"
}
```

**cURL Example:**
```bash
curl http://localhost:8000/users/550e8400-e29b-41d4-a716-446655440000
```

**Error Response (404):**
```json
{
  "detail": "User not found"
}
```

---

### **GET /users/** - List Users
Get paginated list of all users.

**Request:**
```bash
GET /users/?skip=0&limit=50
```

**Parameters:**
- `skip` (optional): Number of users to skip for pagination (default: 0)
- `limit` (optional): Maximum number of users to return (default: 100, max: 100)

**Response:**
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "john_doe", 
    "email": "john@example.com",
    "created_at": "2024-01-15T10:30:00"
  },
  {
    "id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    "username": "jane_smith",
    "email": "jane@example.com", 
    "created_at": "2024-01-14T15:45:00"
  }
]
```

**cURL Example:**
```bash
curl "http://localhost:8000/users/?skip=0&limit=10"
```

---

### **GET /users/{user_id}/preferences** - User Preferences
Get user behavior insights and preferences.

**Request:**
```bash
GET /users/550e8400-e29b-41d4-a716-446655440000/preferences
```

**Response:**
```json
{
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "john_doe",
    "email": "john@example.com"
  },
  "preferences": {
    "total_interactions": 25,
    "favorite_categories": [
      {"category": "Electronics", "count": 15},
      {"category": "Books", "count": 8},
      {"category": "Sports", "count": 2}
    ],
    "favorite_brands": [
      {"brand": "Apple", "count": 8},
      {"brand": "Samsung", "count": 4},
      {"brand": "Nike", "count": 3}
    ],
    "average_rating": 4.2,
    "most_common_interaction": "view",
    "interaction_breakdown": {
      "view": 18,
      "click": 5,
      "purchase": 2
    }
  }
}
```

**cURL Example:**
```bash
curl http://localhost:8000/users/550e8400-e29b-41d4-a716-446655440000/preferences
```

**Use Cases:**
- User profile personalization
- Marketing segmentation
- Behavior analysis
- Recommendation tuning

---

## 📦 **Item Management**

### **POST /items/** - Create Item
Add a new item to the catalog.

**Request:**
```bash
POST /items/
Content-Type: application/json

{
  "title": "MacBook Pro 16-inch",
  "description": "High-performance laptop for professionals",
  "category": "Electronics",
  "brand": "Apple",
  "price": 2499.99,
  "in_stock": true
}
```

**Response:**
```json
{
  "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "title": "MacBook Pro 16-inch", 
  "description": "High-performance laptop for professionals",
  "category": "Electronics",
  "brand": "Apple",
  "price": 2499.99,
  "in_stock": true,
  "popularity_score": 0.0,
  "created_at": "2024-01-15T14:20:00"
}
```

**cURL Example:**
```bash
curl -X POST "http://localhost:8000/items/" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "MacBook Pro 16-inch",
       "description": "High-performance laptop for professionals", 
       "category": "Electronics",
       "brand": "Apple",
       "price": 2499.99,
       "in_stock": true
     }'
```

**Required Fields:**
- `title` (string): Item name
- `category` (string): Product category

**Optional Fields:**
- `description` (string): Item description
- `brand` (string): Brand name
- `price` (number): Price in USD
- `in_stock` (boolean): Availability status (default: true)

---

### **GET /items/{item_id}** - Get Item
Retrieve item details by ID.

**Request:**
```bash
GET /items/7c9e6679-7425-40de-944b-e07fc1f90ae7
```

**Response:**
```json
{
  "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "title": "MacBook Pro 16-inch",
  "description": "High-performance laptop for professionals",
  "category": "Electronics", 
  "brand": "Apple",
  "price": 2499.99,
  "in_stock": true,
  "popularity_score": 0.8,
  "created_at": "2024-01-15T14:20:00"
}
```

**cURL Example:**
```bash
curl http://localhost:8000/items/7c9e6679-7425-40de-944b-e07fc1f90ae7
```

---

### **GET /items/** - List Items
Get paginated list of all items.

**Request:**
```bash
GET /items/?skip=0&limit=20
```

**Parameters:**
- `skip` (optional): Number of items to skip (default: 0)
- `limit` (optional): Maximum items to return (default: 100, max: 100)

**Response:**
```json
[
  {
    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "title": "MacBook Pro 16-inch",
    "description": "High-performance laptop for professionals",
    "category": "Electronics",
    "brand": "Apple", 
    "price": 2499.99,
    "in_stock": true,
    "popularity_score": 0.8,
    "created_at": "2024-01-15T14:20:00"
  }
]
```

**cURL Example:**
```bash
curl "http://localhost:8000/items/?skip=0&limit=20"
```

---

### **GET /items/{item_id}/stats** - Item Statistics
Get performance analytics for an item.

**Request:**
```bash
GET /items/7c9e6679-7425-40de-944b-e07fc1f90ae7/stats
```

**Response:**
```json
{
  "item": {
    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "title": "MacBook Pro 16-inch",
    "category": "Electronics",
    "brand": "Apple",
    "price": 2499.99,
    "popularity_score": 0.8
  },
  "statistics": {
    "total_interactions": 125,
    "unique_users": 87,
    "average_rating": 4.6,
    "conversion_rate": 12.5,
    "interaction_breakdown": {
      "view": 100,
      "click": 20,
      "purchase": 5,
      "like": 15
    },
    "views": 100,
    "clicks": 20,
    "purchases": 5,
    "likes": 15
  }
}
```

**cURL Example:**
```bash
curl http://localhost:8000/items/7c9e6679-7425-40de-944b-e07fc1f90ae7/stats
```

**Use Cases:**
- Product performance analysis
- Inventory optimization
- Marketing effectiveness
- Revenue attribution

---

### **GET /popular/** - Popular Items
Get most popular items across the platform.

**Request:**
```bash
GET /popular/?limit=10
```

**Parameters:**
- `limit` (optional): Maximum items to return (default: 20, max: 100)

**Response:**
```json
[
  {
    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "title": "MacBook Pro 16-inch",
    "description": "High-performance laptop for professionals",
    "category": "Electronics",
    "brand": "Apple",
    "price": 2499.99,
    "in_stock": true,
    "popularity_score": 0.9,
    "created_at": "2024-01-15T14:20:00"
  },
  {
    "id": "8d0f7780-8536-51ef-055c-f18fc2f01bf8",
    "title": "iPhone 15 Pro",
    "description": "Latest smartphone with advanced features",
    "category": "Electronics", 
    "brand": "Apple",
    "price": 999.99,
    "in_stock": true,
    "popularity_score": 0.85,
    "created_at": "2024-01-14T11:30:00"
  }
]
```

**cURL Example:**
```bash
curl "http://localhost:8000/popular/?limit=10"
```

---

## 🔍 **Search & Discovery**

### **POST /search/** - Advanced Search
Search items with multiple filters and options.

**Request:**
```bash
POST /search/
Content-Type: application/json

{
  "query": "laptop gaming",
  "category": "Electronics",
  "max_price": 2000.0,
  "limit": 10
}
```

**Parameters:**
- `query` (required): Search text
- `user_id` (optional): User ID for personalized results
- `category` (optional): Filter by category
- `max_price` (optional): Maximum price filter
- `limit` (optional): Maximum results (default: 20, max: 100)

**Response:**
```json
{
  "items": [
    {
      "id": "9e1f8891-9647-62f0-166d-g29gc3g02cg9",
      "title": "Gaming Laptop Pro",
      "description": "High-performance gaming laptop with RTX graphics",
      "category": "Electronics",
      "brand": "ASUS",
      "price": 1599.99,
      "in_stock": true,
      "popularity_score": 0.7,
      "created_at": "2024-01-13T09:15:00"
    }
  ],
  "total": 1,
  "query": "laptop gaming"
}
```

**cURL Example:**
```bash
curl -X POST "http://localhost:8000/search/" \
     -H "Content-Type: application/json" \
     -d '{
       "query": "laptop gaming",
       "category": "Electronics", 
       "max_price": 2000.0,
       "limit": 10
     }'
```

**PowerShell Example:**
```powershell
$searchBody = @{
    query = "laptop gaming"
    category = "Electronics"
    max_price = 2000.0
    limit = 10
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/search/" -Method POST -ContentType "application/json" -Body $searchBody
```

---

### **GET /search/autocomplete** - Search Suggestions
Get autocomplete suggestions for search queries.

**Request:**
```bash
GET /search/autocomplete?q=lap&limit=5
```

**Parameters:**
- `q` (required): Partial search query (minimum 2 characters)
- `limit` (optional): Maximum suggestions (default: 10, max: 20)

**Response:**
```json
{
  "query": "lap",
  "suggestions": [
    {
      "text": "Laptop",
      "type": "item",
      "category": "items"
    },
    {
      "text": "Gaming Laptop",
      "type": "item", 
      "category": "items"
    },
    {
      "text": "Apple",
      "type": "brand",
      "category": "brands"
    }
  ],
  "total": 3
}
```

**cURL Example:**
```bash
curl "http://localhost:8000/search/autocomplete?q=lap&limit=5"
```

**Use Cases:**
- Search input autocomplete
- Query suggestions
- Typo correction hints
- Brand/category discovery

---

### **GET /categories/** - List Categories
Get all available product categories.

**Request:**
```bash
GET /categories/
```

**Response:**
```json
{
  "categories": [
    {
      "category": "Electronics",
      "total_items": 45,
      "in_stock_items": 38
    },
    {
      "category": "Books", 
      "total_items": 23,
      "in_stock_items": 21
    },
    {
      "category": "Sports",
      "total_items": 12,
      "in_stock_items": 10
    }
  ],
  "total_categories": 3
}
```

**cURL Example:**
```bash
curl http://localhost:8000/categories/
```

**Use Cases:**
- Category navigation
- Inventory overview
- Filter options
- Browse functionality

---

## 🎯 **Recommendations**

### **POST /recommend/** - User Recommendations
Get personalized recommendations for a user.

**Request:**
```bash
POST /recommend/
Content-Type: application/json

{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "limit": 10
}
```

**Parameters:**
- `user_id` (required): User ID for personalization
- `limit` (optional): Maximum recommendations (default: 20, max: 50)

**Response:**
```json
{
  "items": [
    {
      "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "title": "MacBook Pro 16-inch",
      "description": "High-performance laptop for professionals",
      "category": "Electronics",
      "brand": "Apple",
      "price": 2499.99,
      "in_stock": true,
      "popularity_score": 0.8,
      "created_at": "2024-01-15T14:20:00"
    }
  ],
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "algorithm": "collaborative_filtering"
}
```

**cURL Example:**
```bash
curl -X POST "http://localhost:8000/recommend/" \
     -H "Content-Type: application/json" \
     -d '{
       "user_id": "550e8400-e29b-41d4-a716-446655440000",
       "limit": 10
     }'
```

**Algorithm:** User-based collaborative filtering with category preferences and popularity fallback for cold start users.

---

### **GET /users/email/{email}/recommendations** - Email-Based Recommendations
Get recommendations using email address (more user-friendly).

**Request:**
```bash
GET /users/email/john@example.com/recommendations?limit=5
```

**Parameters:**
- `email` (required): User email address (in URL path)
- `limit` (optional): Maximum recommendations (default: 20, max: 50)

**Response:**
```json
{
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "john_doe",
    "email": "john@example.com"
  },
  "personalized_recommendations": [
    {
      "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "title": "MacBook Pro 16-inch",
      "description": "High-performance laptop for professionals",
      "category": "Electronics",
      "brand": "Apple",
      "price": 2499.99,
      "popularity_score": 0.8,
      "in_stock": true
    }
  ],
  "total_recommendations": 1,
  "algorithm": "user_collaborative_filtering_by_email",
  "recommendation_type": "personalized_by_email"
}
```

**cURL Example:**
```bash
curl "http://localhost:8000/users/email/john@example.com/recommendations?limit=5"
```

**PowerShell Example:**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/users/email/john@example.com/recommendations?limit=5"
```

**Use Cases:**
- Email marketing campaigns
- Personalized newsletters
- User-friendly integrations
- Mobile app authentication

---

### **GET /items/{item_id}/similar** - Similar Items
Get items similar to a specific item.

**Request:**
```bash
GET /items/7c9e6679-7425-40de-944b-e07fc1f90ae7/similar?limit=5
```

**Parameters:**
- `item_id` (required): Item ID to find similarities for
- `limit` (optional): Maximum similar items (default: 20, max: 50)

**Response:**
```json
{
  "target_item": {
    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "title": "MacBook Pro 16-inch",
    "category": "Electronics",
    "brand": "Apple",
    "price": 2499.99
  },
  "similar_items": [
    {
      "id": "8d0f7780-8536-51ef-055c-f18fc2f01bf8", 
      "title": "MacBook Air M2",
      "description": "Lightweight laptop with M2 chip",
      "category": "Electronics",
      "brand": "Apple",
      "price": 1299.99,
      "popularity_score": 0.75,
      "in_stock": true
    },
    {
      "id": "9e1f8891-9647-62f0-166d-g29gc3g02cg9",
      "title": "Dell XPS 15",
      "description": "Premium Windows laptop", 
      "category": "Electronics",
      "brand": "Dell",
      "price": 1999.99,
      "popularity_score": 0.65,
      "in_stock": true
    }
  ],
  "total_found": 2,
  "algorithm": "category_brand_similarity"
}
```

**cURL Example:**
```bash
curl "http://localhost:8000/items/7c9e6679-7425-40de-944b-e07fc1f90ae7/similar?limit=5"
```

**Algorithm:** Category-based + brand-based + price-range similarity matching.

**Use Cases:**
- Product page "Similar Products"
- Cross-selling recommendations
- Alternative product suggestions
- Inventory optimization

---

## 📊 **Interactions**

### **POST /interactions/** - Track Interaction
Record user interaction with an item.

**Request:**
```bash
POST /interactions/
Content-Type: application/json

{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "item_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "interaction_type": "view",
  "rating": 4.0
}
```

**Parameters:**
- `user_id` (required): User performing the interaction
- `item_id` (required): Item being interacted with
- `interaction_type` (required): Type of interaction (`view`, `click`, `purchase`, `like`, `share`)
- `rating` (optional): Interaction strength/rating (1.0-5.0, default: 1.0)

**Response:**
```json
{
  "message": "Interaction recorded"
}
```

**cURL Example:**
```bash
curl -X POST "http://localhost:8000/interactions/" \
     -H "Content-Type: application/json" \
     -d '{
       "user_id": "550e8400-e29b-41d4-a716-446655440000",
       "item_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", 
       "interaction_type": "view",
       "rating": 4.0
     }'
```

**Interaction Types:**
- `view`: User viewed the item
- `click`: User clicked on the item
- `purchase`: User purchased the item
- `like`: User liked/favorited the item
- `share`: User shared the item

**Use Cases:**
- User behavior tracking
- Recommendation algorithm training
- Analytics and insights
- Personalization improvement

---

## 📈 **Analytics**

Analytics endpoints are integrated into user and item management sections:

- **User Analytics:** `GET /users/{user_id}/preferences`
- **Item Analytics:** `GET /items/{item_id}/stats`
- **Category Analytics:** `GET /categories/`

---

## ❌ **Error Handling**

All endpoints return consistent error responses in the following format:

### **400 Bad Request**
```json
{
  "detail": "Invalid request parameters"
}
```

### **404 Not Found**
```json
{
  "detail": "User not found"
}
```

### **422 Validation Error**
```json
{
  "detail": [
    {
      "loc": ["body", "email"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### **500 Internal Server Error**
```json
{
  "detail": "Internal server error"
}
```

---

## 🔄 **Rate Limiting**

Currently no rate limiting is implemented. For production deployment, rate limiting will be:

- **Standard endpoints:** 1000 requests/hour per IP
- **Recommendation endpoints:** 500 requests/hour per IP
- **Search endpoints:** 2000 requests/hour per IP

---

## 🚀 **Performance**

### **Response Times (95th percentile):**
- **Search endpoints:** <150ms
- **Recommendation endpoints:** <120ms
- **CRUD endpoints:** <50ms
- **Analytics endpoints:** <200ms

### **Caching:**
- **Search results:** 5 minutes TTL
- **Popular items:** 10 minutes TTL
- **Recommendations:** 5 minutes TTL
- **User preferences:** 15 minutes TTL

---

## 🧪 **Testing & Postman**

### **Postman Collection**

Import this collection to test all endpoints:

```json
{
  "info": {
    "name": "Rec Engine API",
    "description": "Complete API collection for recommendation engine"
  },
  "variable": [
    {
      "key": "base_url",
      "value": "http://localhost:8000"
    }
  ],
  "item": [
    {
      "name": "Health Check",
      "request": {
        "method": "GET",
        "url": "{{base_url}}/health"
      }
    },
    {
      "name": "Create User",
      "request": {
        "method": "POST",
        "url": "{{base_url}}/users/",
        "body": {
          "mode": "raw",
          "raw": "{\n  \"username\": \"test_user\",\n  \"email\": \"test@example.com\"\n}"
        },
        "header": [
          {
            "key": "Content-Type",
            "value": "application/json"
          }
        ]
      }
    }
  ]
}
```

### **Quick Test Script**

```bash
#!/bin/bash
# Test all major endpoints

echo "Testing Rec Engine API..."

# Health check
echo "1. Health check..."
curl -s http://localhost:8000/health | jq

# Create user
echo "2. Creating user..."
USER_RESPONSE=$(curl -s -X POST "http://localhost:8000/users/" \
  -H "Content-Type: application/json" \
  -d '{"username": "test_user", "email": "test@example.com"}')
USER_ID=$(echo $USER_RESPONSE | jq -r '.id')
echo "Created user: $USER_ID"

# Create item  
echo "3. Creating item..."
ITEM_RESPONSE=$(curl -s -X POST "http://localhost:8000/items/" \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Laptop", "category": "Electronics", "price": 999.99}')
ITEM_ID=$(echo $ITEM_RESPONSE | jq -r '.id')
echo "Created item: $ITEM_ID"

# Record interaction
echo "4. Recording interaction..."
curl -s -X POST "http://localhost:8000/interactions/" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$USER_ID\", \"item_id\": \"$ITEM_ID\", \"interaction_type\": \"view\", \"rating\": 4.0}"

# Get recommendations
echo "5. Getting recommendations..."
curl -s -X POST "http://localhost:8000/recommend/" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$USER_ID\", \"limit\": 5}" | jq

echo "API test completed!"
```

---

## 📱 **SDKs & Client Libraries**

### **Python SDK Example**

```python
import requests

class RecEngineClient:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
    
    def create_user(self, username, email):
        response = requests.post(f"{self.base_url}/users/", json={
            "username": username,
            "email": email
        })
        return response.json()
    
    def get_recommendations(self, user_id, limit=20):
        response = requests.post(f"{self.base_url}/recommend/", json={
            "user_id": user_id,
            "limit": limit
        })
        return response.json()
    
    def search_items(self, query, **filters):
        payload = {"query": query, **filters}
        response = requests.post(f"{self.base_url}/search/", json=payload)
        return response.json()
    
    def track_interaction(self, user_id, item_id, interaction_type, rating=1.0):
        response = requests.post(f"{self.base_url}/interactions/", json={
            "user_id": user_id,
            "item_id": item_id,
            "interaction_type": interaction_type,
            "rating": rating
        })
        return response.json()

# Usage
client = RecEngineClient()
user = client.create_user("john", "john@example.com")
recommendations = client.get_recommendations(user["id"])
```

### **JavaScript SDK Example**

```javascript
class RecEngineClient {
  constructor(baseUrl = 'http://localhost:8000') {
    this.baseUrl = baseUrl;
  }

  async createUser(username, email) {
    const response = await fetch(`${this.baseUrl}/users/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, email })
    });
    return response.json();
  }

  async getRecommendations(userId, limit = 20) {
    const response = await fetch(`${this.baseUrl}/recommend/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, limit })
    });
    return response.json();
  }

  async searchItems(query, filters = {}) {
    const response = await fetch(`${this.baseUrl}/search/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, ...filters })
    });
    return response.json();
  }

  async trackInteraction(userId, itemId, interactionType, rating = 1.0) {
    const response = await fetch(`${this.baseUrl}/interactions/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: userId,
        item_id: itemId,
        interaction_type: interactionType,
        rating
      })
    });
    return response.json();
  }
}

// Usage
const client = new RecEngineClient();
const user = await client.createUser('john', 'john@example.com');
const recommendations = await client.getRecommendations(user.id);
```

---

## 🔧 **Environment Setup**

### **Development**
```bash
# Clone repository
git clone https://github.com/gulpcr/rec-engine.git
cd rec-engine

# Install dependencies
pip install -r requirements.txt

# Initialize database
python -c "from services.engine.database import init_db; init_db()"

# Start server
uvicorn services.engine.main:app --reload

# API available at: http://localhost:8000
# Documentation: http://localhost:8000/docs
```

### **Production**
```bash
# Environment variables
export DATABASE_URL="postgresql://user:pass@host:5432/db"
export REDIS_URL="redis://host:6379/0"
export ENVIRONMENT="production"

# Start production server
gunicorn services.engine.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

---

## 📞 **Support & Resources**

- **Documentation:** `http://localhost:8000/docs`
- **Health Check:** `http://localhost:8000/health`
- **GitHub Repository:** `https://github.com/gulpcr/rec-engine`
- **Issues:** Create GitHub issues for bugs/features

---

## 📄 **API Changelog**

### **Version 1.0.0** (Current)
- Initial release with 19 endpoints
- User management and preferences
- Item catalog and analytics
- Multiple recommendation algorithms
- Advanced search with autocomplete
- Performance analytics
- Complete documentation

---

**Built with ❤️ using FastAPI, SQLAlchemy, and modern Python practices.**

*This API documentation is automatically generated and always up-to-date. Visit `/docs` for interactive testing.*