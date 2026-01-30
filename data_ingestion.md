curl -X POST "http://localhost:8001/bulk-import/" \
     -H "Content-Type: application/json" \
     -d '{
       "users": [
         {
           "username": "alice_jones",
           "email": "alice@company.com"
         },
         {
           "username": "bob_smith", 
           "email": "bob.smith@email.com"
         }
       ],
       "items": [
         {
           "title": "Professional Laptop",
           "description": "High-performance business laptop",
           "category": "Electronics",
           "brand": "Dell",
           "price": 1299.99,
           "in_stock": true
         },
         {
           "title": "Office Chair",
           "description": "Ergonomic office chair",
           "category": "Furniture", 
           "brand": "Herman Miller",
           "price": 599.99,
           "in_stock": false
         }
       ],
       "interactions": [
         {
           "user_id": "user-123",
           "item_id": "item-456", 
           "interaction_type": "purchase",
           "rating": 4.5
         }
       ]
     }'


     # Create interactions
     curl -X POST "http://localhost:8001/bulk-import/" \
     -H "Content-Type: application/json" \
     -d '{
       "interactions": [
         {
           "user_id": "user-123",
           "item_id": "item-456",
           "interaction_type": "purchase",
           "rating": 4.5
         },
         {
           "user_id": "user-124",
           "item_id": "item-457",
           "interaction_type": "view",
           "rating": 3.0
         },
         {
           "user_id": "user-125",
           "item_id": "item-458",
           "interaction_type": "like",
           "rating": 5.0
         },
         {
           "user_id": "user-123",
           "item_id": "item-459",
           "interaction_type": "click",
           "rating": 2.5
         },
         {
           "user_id": "user-126",
           "item_id": "item-456",
           "interaction_type": "share",
           "rating": 4.0
         }
       ]
     }'