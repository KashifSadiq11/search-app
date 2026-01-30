from sqlalchemy import Column, String, Text, Float, Boolean, DateTime, ForeignKey, JSON, BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func, text
import uuid

try:
    from .database import Base
except ImportError:
    from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, nullable=False)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    interactions = relationship("Interaction", back_populates="user")

class Item(Base):
    __tablename__ = "items"
    id = Column(String(24), primary_key=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    category = Column(String(100), nullable=False)
    brand = Column(String(100))
    price = Column(Float)
    in_stock = Column(Boolean, default=True)
    popularity_score = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now())
    interactions = relationship("Interaction", back_populates="item")
    seller_info = Column(JSONB, nullable=True)
    product_status = Column(String, default="active")
    category_name = Column(String(100)) 
    product_rating = Column(Float, default=0.0)
    discount = Column(Float, default=0.0)

    raw_data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

class Interaction(Base):
    __tablename__ = "interactions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    item_id = Column(String(24), ForeignKey("items.id"), nullable=False)
    interaction_type = Column(String(20), nullable=False)
    rating = Column(Float, default=1.0)
    created_at = Column(DateTime, server_default=func.now())
    user = relationship("User", back_populates="interactions")
    item = relationship("Item", back_populates="interactions")

# ML-related models
class ItemEmbedding(Base):
    __tablename__ = "item_embeddings"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    item_id = Column(String(24), ForeignKey("items.id"), unique=True, index=True)
    text_embedding = Column(JSON)  # Store as JSON array
    image_embedding = Column(JSON)
    combined_embedding = Column(JSON)
    model_version = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationship
    item = relationship("Item", backref="embeddings")

class UserEmbedding(Base):
    __tablename__ = "user_embeddings"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), unique=True, index=True)
    preference_embedding = Column(JSON)  # User preference vector
    behavioral_embedding = Column(JSON)  # User behavior pattern vector
    model_version = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationship
    user = relationship("User", backref="embeddings")

class MLModel(Base):
    __tablename__ = "ml_models"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    model_name = Column(String, nullable=False)
    model_type = Column(String)  # ncf, embedding, predictor
    model_version = Column(String)
    model_path = Column(String)  # Path to saved model
    metrics = Column(JSON)  # Training metrics
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, default=True)

class TrainingLog(Base):
    __tablename__ = "training_logs"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    model_name = Column(String)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    training_metrics = Column(JSON)
    status = Column(String) 
    error_message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())