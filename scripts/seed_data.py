# scripts/seed_data.py (SAMPLE DATA)
#!/usr/bin/env python3
"""Seed database with sample data."""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from services.engine.database import SessionLocal, init_db
from services.engine.models import User, Item, Interaction

def create_sample_data():
    """Create sample users, items, and interactions."""
    db = SessionLocal()
    
    try:
        # Create sample users
        users = [
            User(username="alice", email="alice@example.com"),
            User(username="bob", email="bob@example.com"),
            User(username="charlie", email="charlie@example.com"),
        ]
        
        for user in users:
            db.add(user)
        db.commit()
        
        # Create sample items
        items = [
            Item(title="Laptop", description="High-performance laptop", category="Electronics", brand="TechBrand", price=999.99, popularity_score=0.8),
            Item(title="Coffee Mug", description="Ceramic coffee mug", category="Home", brand="HomeBrand", price=19.99, popularity_score=0.6),
            Item(title="Running Shoes", description="Comfortable running shoes", category="Sports", brand="SportsBrand", price=129.99, popularity_score=0.7),
            Item(title="Book: Python Programming", description="Learn Python programming", category="Books", brand="TechPublisher", price=39.99, popularity_score=0.5),
            Item(title="Wireless Headphones", description="Noise-cancelling headphones", category="Electronics", brand="AudioBrand", price=199.99, popularity_score=0.9),
        ]
        
        for item in items:
            db.add(item)
        db.commit()
        
        # Create sample interactions
        interactions = [
            Interaction(user_id=users[0].id, item_id=items[0].id, interaction_type="view", rating=4.0),
            Interaction(user_id=users[0].id, item_id=items[4].id, interaction_type="purchase", rating=5.0),
            Interaction(user_id=users[1].id, item_id=items[2].id, interaction_type="view", rating=3.0),
            Interaction(user_id=users[1].id, item_id=items[1].id, interaction_type="purchase", rating=4.0),
            Interaction(user_id=users[2].id, item_id=items[3].id, interaction_type="view", rating=4.0),
        ]
        
        for interaction in interactions:
            db.add(interaction)
        db.commit()
        
        print("✅ Sample data created successfully!")
        print(f"Created {len(users)} users, {len(items)} items, {len(interactions)} interactions")
        
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
    create_sample_data()