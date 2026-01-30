# services/engine/search.py
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func, desc, asc
from collections import Counter
import logging

# Handle both relative and absolute imports
try:
    from .models import Item, Interaction
    from .memory_cache import cache
    from .schemas import SearchRequest, SearchResponse
except ImportError:
    from models import Item, Interaction
    from memory_cache import cache
    from schemas import SearchRequest, SearchResponse

logger = logging.getLogger(__name__)


class EnhancedSearchEngine:
    """Enhanced search engine with faceting and advanced features."""
    
    def __init__(self):
        self.initialized = False
        logger.info("EnhancedSearchEngine initialized")
    
    async def initialize(self):
        """Initialize the search engine."""
        self.initialized = True
        logger.info("Search engine ready")
    
    async def search(self, db: Session, request: SearchRequest) -> SearchResponse:
        """Perform enhanced search with facets and filters."""
        
        # Create cache key
        cache_key = f"search:{request.query}:{request.category}:{request.max_price}:{request.limit}:{request.offset}"
        
        # Try cache first
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result
        
        # Build base query
        query = db.query(Item)
        
        # Apply filters
        filters_applied = {}
        
        # Stock filter
        if request.in_stock_only:
            query = query.filter(Item.in_stock == True)
            filters_applied['in_stock'] = True
        
        # Product status filter (for ML features)
        if hasattr(Item, 'product_status'):
            query = query.filter(Item.product_status == "active")
        
        # Text search
        if request.query:
            search_filter = or_(
                Item.title.ilike(f"%{request.query}%"),
                Item.description.ilike(f"%{request.query}%"),
                Item.brand.ilike(f"%{request.query}%")
            )
            query = query.filter(search_filter)
            filters_applied['query'] = request.query
        
        # Category filter
        if request.category:
            # Check both category and category_name fields
            if hasattr(Item, 'category_name'):
                query = query.filter(
                    or_(
                        Item.category == request.category,
                        Item.category_name == request.category
                    )
                )
            else:
                query = query.filter(Item.category == request.category)
            filters_applied['category'] = request.category
        
        # Price filters
        if request.min_price is not None:
            query = query.filter(Item.price >= request.min_price)
            filters_applied['min_price'] = request.min_price
        
        if request.max_price is not None:
            query = query.filter(Item.price <= request.max_price)
            filters_applied['max_price'] = request.max_price
        
        # Rating filter
        if request.min_rating is not None and hasattr(Item, 'product_rating'):
            query = query.filter(Item.product_rating >= request.min_rating)
            filters_applied['min_rating'] = request.min_rating
        
        # Get total count before pagination
        total = query.count()
        
        # Apply sorting
        if request.sort_by == "price_asc":
            query = query.order_by(asc(Item.price))
        elif request.sort_by == "price_desc":
            query = query.order_by(desc(Item.price))
        elif request.sort_by == "rating" and hasattr(Item, 'product_rating'):
            query = query.order_by(desc(Item.product_rating))
        elif request.sort_by == "popularity":
            query = query.order_by(desc(Item.popularity_score))
        else:  # Default to relevance/popularity
            query = query.order_by(desc(Item.popularity_score))
        
        # Apply pagination
        items = query.offset(request.offset).limit(request.limit).all()
        
        # Generate facets
        facets = await self._generate_facets(db, request)
        
        # Generate suggestions
        suggestions = await self._generate_suggestions(db, request.query)
        
        # Create response
        response = SearchResponse(
            items=items,
            total=total,
            query=request.query or "",
            filters_applied=filters_applied,
            suggestions=suggestions,
            facets=facets
        )
        
        # Cache result
        cache.set(cache_key, response, ttl=300)
        
        return response
    
    async def _generate_facets(self, db: Session, request: SearchRequest) -> Dict[str, List[Dict[str, Any]]]:
        """Generate search facets for filtering."""
        facets = {}
        
        # Category facet
        category_counts = db.query(
            Item.category,
            func.count(Item.id).label('count')
        ).filter(
            Item.in_stock == True
        ).group_by(Item.category).all()
        
        facets['categories'] = [
            {"value": cat, "count": count}
            for cat, count in category_counts
        ]
        
        # Brand facet
        brand_counts = db.query(
            Item.brand,
            func.count(Item.id).label('count')
        ).filter(
            and_(
                Item.in_stock == True,
                Item.brand.isnot(None)
            )
        ).group_by(Item.brand).limit(10).all()
        
        facets['brands'] = [
            {"value": brand, "count": count}
            for brand, count in brand_counts if brand
        ]
        
        # Price ranges facet
        price_stats = db.query(
            func.min(Item.price).label('min_price'),
            func.max(Item.price).label('max_price')
        ).filter(
            Item.in_stock == True
        ).first()
        
        if price_stats and price_stats.min_price is not None:
            facets['price_range'] = {
                "min": float(price_stats.min_price),
                "max": float(price_stats.max_price)
            }
        
        return facets
    
    async def _generate_suggestions(self, db: Session, query: Optional[str]) -> List[str]:
        """Generate search suggestions."""
        if not query:
            return []
        
        suggestions = []
        
        # Get popular searches that contain the query
        popular_items = db.query(Item.title).filter(
            and_(
                Item.title.ilike(f"%{query}%"),
                Item.in_stock == True
            )
        ).order_by(desc(Item.popularity_score)).limit(5).all()
        
        for item in popular_items:
            if item.title not in suggestions:
                suggestions.append(item.title)
        
        return suggestions[:5]


# Keep simple functions for backward compatibility
def search_items_simple(
    db: Session, 
    query: str, 
    category: str = None,
    max_price: float = None,
    limit: int = 20
) -> List[Item]:
    """Simple database search without external search engine."""
    
    # Create cache key
    cache_key = f"search:{query}:{category}:{max_price}:{limit}"
    
    # Try cache first
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    
    # Build query
    db_query = db.query(Item).filter(Item.in_stock == True)
    
    # Text search on title and description
    if query:
        search_filter = or_(
            Item.title.ilike(f"%{query}%"),
            Item.description.ilike(f"%{query}%"),
            Item.brand.ilike(f"%{query}%")
        )
        db_query = db_query.filter(search_filter)
    
    # Category filter
    if category:
        db_query = db_query.filter(Item.category == category)
    
    # Price filter
    if max_price:
        db_query = db_query.filter(Item.price <= max_price)
    
    # Order by popularity and limit
    items = db_query.order_by(desc(Item.popularity_score)).limit(limit).all()
    
    # Cache result
    cache.set(cache_key, items, ttl=300)
    
    return items


def get_popular_items(db: Session, limit: int = 20) -> List[Item]:
    """Get popular items."""
    cache_key = f"popular:{limit}"
    
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    
    items = db.query(Item).filter(
        Item.in_stock == True
    ).order_by(
        desc(Item.popularity_score)
    ).limit(limit).all()
    
    cache.set(cache_key, items, ttl=600)
    return items