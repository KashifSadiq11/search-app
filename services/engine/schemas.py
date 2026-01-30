# services/engine/schemas.py
from pydantic import BaseModel,Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class UserBase(BaseModel):
    username: str
    email: str

class UserCreate(UserBase):
    id: str

class User(UserBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class ItemBase(BaseModel):
    title: str
    description: Optional[str] = None
    category: str
    brand: Optional[str] = None
    price: Optional[float] = None
    in_stock: bool = True
    seller_info: Optional[Dict[str, Any]] = None

class ItemCreate(ItemBase):
    id: str
    product_status: Optional[str] = "active"
    category_name: Optional[str] = None
    product_rating: Optional[float] = 0.0
    discount: Optional[float] = 0.0
    popularity_score: Optional[float] = 0.0

class Item(ItemBase):
    id: str
    popularity_score: float
    created_at: datetime
    product_status: Optional[str] = "active"
    category_name: Optional[str] = None
    product_rating: Optional[float] = 0.0
    discount: Optional[float] = 0.0

    class Config:
        from_attributes = True


class InteractionCreate(BaseModel):
    user_id: str
    item_id: str
    interaction_type: str  # view, click, purchase, like
    rating: float = 1.0

class Interaction(BaseModel):
    id: str
    user_id: str
    item_id: str
    interaction_type: str
    rating: float
    created_at: datetime

    class Config:
        from_attributes = True

class ShopSummary(BaseModel):
    seller_id: str
    name: Optional[str] = None
    user_name: Optional[str] = None
    city: Optional[str] = None
    zone: Optional[str] = None
    province: Optional[str] = None
    status: Optional[str] = None
    business_type: Optional[str] = None

    # ranking metadata
    first_item_id: Optional[str] = None
    matched_items_count: int = 0


# Enhanced search schemas
class SearchRequest(BaseModel):
    query: Optional[str] = None
    user_id: Optional[str] = None
    category: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_rating: Optional[float] = None
    in_stock_only: bool = True
    sort_by: Optional[str] = "relevance"  # relevance, price_asc, price_desc, rating, popularity
    limit: int = 20
    offset: int = 0
    filters: Optional[Dict[str, Any]] = None

class SearchResponse(BaseModel):
    items: List[Item]
    total: int
    query: str
    filters_applied: Dict[str, Any] = {}
    suggestions: List[str] = []
    facets: Dict[str, Any] = {}  # Changed to accept any structure
    shops: List[ShopSummary] = Field(default_factory=list)

# Enhanced recommendation schemas
class RecommendationItem(BaseModel):
    item: Item
    score: float
    reason: str
    algorithm_used: str

class RecommendRequest(BaseModel):
    user_id: str
    algorithm: Optional[str] = "auto"  # auto, collaborative, content, hybrid, deep_learning
    limit: int = 20
    offset: int = 0
    context: Optional[Dict[str, Any]] = None  # Additional context like time of day, device, etc.
    exclude_items: Optional[List[str]] = None

class RecommendResponse(BaseModel):
    items: List[RecommendationItem]
    user_id: str
    algorithm_used: str
    total: int
    context: Optional[Dict[str, Any]] = None

# Similar items schemas
class SimilarItemsRequest(BaseModel):
    item_id: str
    algorithm: Optional[str] = "auto"  # auto, content, collaborative, visual
    limit: int = 10
    min_similarity: Optional[float] = 0.0

class SimilarItemsResponse(BaseModel):
    items: List[RecommendationItem]
    item_id: str
    algorithm_used: str
    total: int

# Analytics schemas
class TrendingItem(BaseModel):
    item: Item
    trend_score: float
    interaction_count: int
    growth_rate: Optional[float] = None

class UserPreferences(BaseModel):
    user: User
    favorite_categories: List[Dict[str, Any]]
    favorite_brands: List[Dict[str, Any]]
    price_range: Dict[str, float]
    average_rating: float
    total_interactions: int

# Error response
class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None