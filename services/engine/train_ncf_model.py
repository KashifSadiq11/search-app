#!/usr/bin/env python3
# train_ncf_model.py - Train the NCF model with your data
import torch
import torch.nn as nn
import numpy as np
from sklearn.preprocessing import LabelEncoder
import joblib
from database import get_db
from models import User, Item, Interaction
from ml_engine import NeuralCollaborativeFiltering
import os

def train_ncf():
    """Train Neural Collaborative Filtering model."""
    db = next(get_db())
    
    # Get all interactions
    interactions = db.query(Interaction).all()
    if len(interactions) < 10:
        print("Need at least 10 interactions to train")
        return
    
    # Prepare data
    users = list(set([i.user_id for i in interactions]))
    items = list(set([i.item_id for i in interactions]))
    
    # Create encoders
    user_encoder = LabelEncoder()
    item_encoder = LabelEncoder()
    
    user_encoder.fit(users)
    item_encoder.fit(items)
    
    # Save encoders
    os.makedirs("ml_models", exist_ok=True)
    joblib.dump(user_encoder, "ml_models/user_encoder.pkl")
    joblib.dump(item_encoder, "ml_models/item_encoder.pkl")
    
    # Prepare training data
    user_ids = user_encoder.transform([i.user_id for i in interactions])
    item_ids = item_encoder.transform([i.item_id for i in interactions])
    ratings = np.array([i.rating for i in interactions], dtype=np.float32)
    
    # Create model
    model = NeuralCollaborativeFiltering(
        num_users=len(users),
        num_items=len(items),
        embedding_dim=50
    )
    
    # Training setup
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.MSELoss()
    
    # Convert to tensors
    user_tensor = torch.LongTensor(user_ids)
    item_tensor = torch.LongTensor(item_ids)
    rating_tensor = torch.FloatTensor(ratings)
    
    # Training loop
    print("Training NCF model...")
    model.train()
    for epoch in range(100):
        optimizer.zero_grad()
        predictions = model(user_tensor, item_tensor).squeeze()
        loss = criterion(predictions, rating_tensor)
        loss.backward()
        optimizer.step()
        
        if epoch % 20 == 0:
            print(f"Epoch {epoch}, Loss: {loss.item():.4f}")
    
    # Save model
    torch.save({
        'model_state_dict': model.state_dict(),
        'num_users': len(users),
        'num_items': len(items),
        'embedding_dim': 50
    }, "ml_models/ncf_final.pth")
    
    print(f"Model trained and saved! Users: {len(users)}, Items: {len(items)}")

if __name__ == "__main__":
    train_ncf()