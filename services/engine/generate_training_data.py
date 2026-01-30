# generate_training_data.py
import random
from database import get_db
from models import User, Item, Interaction
import uuid

def generate_interactions():
    """Generate interactions for training."""
    db = next(get_db())
    
    users = db.query(User).limit(10).all()
    items = db.query(Item).limit(50).all()
    
    if not users:
        # Create some users
        for i in range(10):
            user = User(
                id=str(uuid.uuid4()),
                username=f"testuser_{i}",
                email=f"user{i}@test.com"
            )
            db.add(user)
        db.commit()
        users = db.query(User).all()
    
    # Generate interactions
    for user in users:
        # Each user interacts with 5-15 items
        num_interactions = random.randint(5, 15)
        selected_items = random.sample(items, min(num_interactions, len(items)))
        
        for item in selected_items:
            interaction = Interaction(
                id=str(uuid.uuid4()),
                user_id=user.id,
                item_id=item.id,
                interaction_type=random.choice(['view', 'click', 'purchase']),
                rating=random.uniform(1, 5)
            )
            db.add(interaction)
    
    db.commit()
    print(f"Generated {len(users) * 10} interactions")

if __name__ == "__main__":
    generate_interactions()