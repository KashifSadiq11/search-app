#!/usr/bin/env python3
import random
import uuid
from database import get_db
from models import User, Item, Interaction

def generate_test_data():
    db = next(get_db())
    
    # Create test users
    print("Creating users...")
    users = []
    for i in range(20):
        user = User(
            id=str(uuid.uuid4()),
            username=f"testuser_{i}",
            email=f"user{i}@test.com"
        )
        db.add(user)
        users.append(user)
    db.commit()
    
    # Get all items
    items = db.query(Item).all()
    print(f"Found {len(items)} items")
    
    # Generate interactions
    print("Generating interactions...")
    interaction_count = 0
    for user in users:
        # Each user interacts with 10-30 items
        num_interactions = random.randint(10, min(30, len(items)))
        selected_items = random.sample(items, num_interactions)
        
        for item in selected_items:
            interaction = Interaction(
                id=str(uuid.uuid4()),
                user_id=user.id,
                item_id=item.id,
                interaction_type=random.choice(['view', 'click', 'purchase']),
                rating=random.uniform(2.0, 5.0)
            )
            db.add(interaction)
            interaction_count += 1
    
    db.commit()
    print(f"Created {len(users)} users and {interaction_count} interactions")
    db.close()

if __name__ == "__main__":
    generate_test_data()