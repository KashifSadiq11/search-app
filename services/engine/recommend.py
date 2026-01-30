# services/engine/recommend.py
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, desc
from collections import defaultdict, Counter
import logging
import random

# Handle both relative and absolute imports
try:
    from .models import User, Item, Interaction
    from .memory_cache import cache
    from .search import get_popular_items
    from .schemas import RecommendRequest, RecommendResponse, RecommendationItem, SimilarItemsRequest, SimilarItemsResponse
except ImportError:
    from models import User, Item, Interaction
    from memory_cache import cache
    from search import get_popular_items
    from schemas import RecommendRequest, RecommendResponse, RecommendationItem, SimilarItemsRequest, SimilarItemsResponse

logger = logging.getLogger(__name__)


class EnhancedRecommendationEngine:
    """Enhanced recommendation engine with multiple algorithms."""
    
    def __init__(self):
        self.initialized = False
        logger.info("EnhancedRecommendationEngine initialized")
    
    async def initialize(self):
        """Initialize the recommendation engine."""
        self.initialized = True
        logger.info("Recommendation engine ready")
    
    async def get_recommendations(self, db: Session, request: RecommendRequest) -> RecommendResponse:
        """Get recommendations using the specified or auto-selected algorithm."""
        
        # Check cache
        cache_key = f"recommend:{request.user_id}:{request.algorithm}:{request.limit}:{request.offset}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result
        
        # Select algorithm
        algorithm = request.algorithm
        if algorithm == "auto":
            algorithm = await self._select_best_algorithm(db, request.user_id)
        
        # Get recommendations based on algorithm
        if algorithm == "collaborative":
            items = await self._collaborative_filtering(db, request)
        elif algorithm == "content":
            items = await self._content_based(db, request)
        elif algorithm == "hybrid":
            items = await self._hybrid(db, request)
        else:  # Default to hybrid
            items = await self._hybrid(db, request)
            algorithm = "hybrid"
        
        # Filter out excluded items
        if request.exclude_items:
            items = [item for item in items if item.item.id not in request.exclude_items]
        
        # Apply pagination
        paginated_items = items[request.offset:request.offset + request.limit]
        
        # Create response
        response = RecommendResponse(
            items=paginated_items,
            user_id=request.user_id,
            algorithm_used=algorithm,
            total=len(items),
            context=request.context
        )
        
        # Cache result
        cache.set(cache_key, response, ttl=300)
        
        return response
    
    async def _select_best_algorithm(self, db: Session, user_id: str) -> str:
        """Select the best algorithm based on available data."""
        # Check user's interaction count
        interaction_count = db.query(Interaction).filter(
            Interaction.user_id == user_id
        ).count()
        
        if interaction_count == 0:
            return "content"  # Cold start - use content-based
        elif interaction_count < 5:
            return "hybrid"  # Few interactions - use hybrid
        else:
            return "collaborative"  # Enough data for collaborative
    
    async def _collaborative_filtering(self, db: Session, request: RecommendRequest) -> List[RecommendationItem]:
        """Collaborative filtering recommendations."""
        user_id = request.user_id
        
        # Get user's interactions
        user_interactions = db.query(Interaction).filter(
            Interaction.user_id == user_id
        ).all()
        
        if not user_interactions:
            # Fall back to popular items
            return await self._get_popular_recommendations(db, request.limit)
        
        # Find users with similar interactions
        interacted_items = {i.item_id for i in user_interactions}
        
        similar_users = db.query(
            Interaction.user_id,
            func.count(Interaction.item_id).label('common_items')
        ).filter(
            and_(
                Interaction.item_id.in_(interacted_items),
                Interaction.user_id != user_id
            )
        ).group_by(
            Interaction.user_id
        ).order_by(
            desc('common_items')
        ).limit(50).all()
        
        if not similar_users:
            return await self._content_based(db, request)
        
        # Get items liked by similar users
        similar_user_ids = [u.user_id for u in similar_users]
        
        recommended_items = db.query(
            Interaction.item_id,
            func.count(Interaction.user_id).label('user_count'),
            func.avg(Interaction.rating).label('avg_rating')
        ).filter(
            and_(
                Interaction.user_id.in_(similar_user_ids),
                ~Interaction.item_id.in_(interacted_items),
                Interaction.rating >= 3.0  # Only positive interactions
            )
        ).group_by(
            Interaction.item_id
        ).order_by(
            desc('user_count'),
            desc('avg_rating')
        ).limit(request.limit * 2).all()
        
        # Build recommendation items
        recommendations = []
        for rec in recommended_items:
            item = db.query(Item).filter(
                and_(
                    Item.id == rec.item_id,
                    Item.in_stock == True
                )
            ).first()
            
            if item:
                score = rec.user_count * 0.7 + float(rec.avg_rating) * 0.3
                recommendations.append(RecommendationItem(
                    item=item,
                    score=score,
                    reason=f"Users with similar tastes also liked this",
                    algorithm_used="collaborative"
                ))
        
        return recommendations
    
    async def _content_based(self, db: Session, request: RecommendRequest) -> List[RecommendationItem]:
        """Content-based filtering recommendations."""
        user_id = request.user_id
        
        # Get user's interaction history
        user_interactions = db.query(Interaction).filter(
            Interaction.user_id == user_id
        ).order_by(desc(Interaction.created_at)).limit(20).all()
        
        if not user_interactions:
            return await self._get_popular_recommendations(db, request.limit)
        
        # Extract user preferences
        categories = []
        brands = []
        price_range = []
        
        for interaction in user_interactions:
            if interaction.item:
                categories.append(interaction.item.category)
                if interaction.item.brand:
                    brands.append(interaction.item.brand)
                if interaction.item.price:
                    price_range.append(interaction.item.price)
        
        # Get most common categories and brands
        top_categories = [cat for cat, _ in Counter(categories).most_common(3)]
        top_brands = [brand for brand, _ in Counter(brands).most_common(3)]
        avg_price = sum(price_range) / len(price_range) if price_range else None
        
        # Get already interacted items
        interacted_item_ids = {i.item_id for i in user_interactions}
        
        # Build recommendation query
        query = db.query(Item).filter(
            and_(
                Item.in_stock == True,
                ~Item.id.in_(interacted_item_ids)
            )
        )
        
        # Apply content filters
        content_filters = []
        
        if top_categories:
            content_filters.append(Item.category.in_(top_categories))
        
        if top_brands:
            content_filters.append(Item.brand.in_(top_brands))
        
        if avg_price:
            price_min = avg_price * 0.5
            price_max = avg_price * 1.5
            content_filters.append(
                and_(Item.price >= price_min, Item.price <= price_max)
            )
        
        if content_filters:
            query = query.filter(or_(*content_filters))
        
        # Order by popularity and get items
        items = query.order_by(desc(Item.popularity_score)).limit(request.limit * 2).all()
        
        # Build recommendations
        recommendations = []
        for item in items:
            # Calculate similarity score
            score = 0.0
            reasons = []
            
            if item.category in top_categories:
                score += 0.4
                reasons.append("category match")
            
            if item.brand in top_brands:
                score += 0.3
                reasons.append("brand preference")
            
            if avg_price and item.price:
                price_diff = abs(item.price - avg_price) / avg_price
                if price_diff < 0.5:
                    score += 0.3 * (1 - price_diff)
                    reasons.append("price range")
            
            score += item.popularity_score * 0.1
            
            reason_text = f"Based on your {', '.join(reasons)}" if reasons else "Similar to your interests"
            
            recommendations.append(RecommendationItem(
                item=item,
                score=score,
                reason=reason_text,
                algorithm_used="content"
            ))
        
        # Sort by score and return
        recommendations.sort(key=lambda x: x.score, reverse=True)
        return recommendations
    
    async def _hybrid(self, db: Session, request: RecommendRequest) -> List[RecommendationItem]:
        """Hybrid recommendations combining multiple algorithms."""
        # Get recommendations from both algorithms
        collab_recs = await self._collaborative_filtering(db, request)
        content_recs = await self._content_based(db, request)
        
        # Combine recommendations
        item_scores = {}
        item_reasons = {}
        
        # Add collaborative scores
        for rec in collab_recs:
            item_scores[rec.item.id] = rec.score * 0.6
            item_reasons[rec.item.id] = rec.reason
        
        # Add content scores
        for rec in content_recs:
            if rec.item.id in item_scores:
                item_scores[rec.item.id] += rec.score * 0.4
                item_reasons[rec.item.id] = "Highly recommended for you"
            else:
                item_scores[rec.item.id] = rec.score * 0.4
                item_reasons[rec.item.id] = rec.reason
        
        # Build final recommendations
        recommendations = []
        for item_id, score in item_scores.items():
            item = db.query(Item).filter(Item.id == item_id).first()
            if item:
                recommendations.append(RecommendationItem(
                    item=item,
                    score=score,
                    reason=item_reasons[item_id],
                    algorithm_used="hybrid"
                ))
        
        # Sort by score
        recommendations.sort(key=lambda x: x.score, reverse=True)
        return recommendations
    
    async def _get_popular_recommendations(self, db: Session, limit: int) -> List[RecommendationItem]:
        """Get popular items as recommendations."""
        popular_items = get_popular_items(db, limit)
        
        recommendations = []
        for i, item in enumerate(popular_items):
            score = 1.0 - (i * 0.05)  # Decreasing score by position
            recommendations.append(RecommendationItem(
                item=item,
                score=score,
                reason="Trending now",
                algorithm_used="popularity"
            ))
        
        return recommendations
    
    async def get_similar_items(self, db: Session, request: SimilarItemsRequest) -> SimilarItemsResponse:
        """Get items similar to a given item."""
        # Get target item
        target_item = db.query(Item).filter(Item.id == request.item_id).first()
        if not target_item:
            return SimilarItemsResponse(
                items=[],
                item_id=request.item_id,
                algorithm_used="none",
                total=0
            )
        
        # Find similar items
        similar_items = []
        
        # Category-based similarity
        category_items = db.query(Item).filter(
            and_(
                Item.category == target_item.category,
                Item.id != request.item_id,
                Item.in_stock == True
            )
        ).all()
        
        # Calculate similarity scores
        for item in category_items:
            score = 0.5  # Base category match score
            
            # Brand similarity
            if item.brand == target_item.brand:
                score += 0.2
            
            # Price similarity
            if item.price and target_item.price:
                price_diff = abs(item.price - target_item.price) / target_item.price
                if price_diff < 0.3:
                    score += 0.2 * (1 - price_diff)
            
            # Popularity factor
            score += item.popularity_score * 0.1
            
            if score >= request.min_similarity:
                similar_items.append(RecommendationItem(
                    item=item,
                    score=score,
                    reason=f"Similar to {target_item.title}",
                    algorithm_used="content"
                ))
        
        # Sort by similarity score
        similar_items.sort(key=lambda x: x.score, reverse=True)
        
        # Apply limit
        limited_items = similar_items[:request.limit]
        
        return SimilarItemsResponse(
            items=limited_items,
            item_id=request.item_id,
            algorithm_used="content",
            total=len(similar_items)
        )


# Keep simple function for backward compatibility
def get_user_recommendations(db: Session, user_id: str, limit: int = 20) -> List[Item]:
    """Get recommendations for a user using simple collaborative filtering."""
    
    cache_key = f"recommend:{user_id}:{limit}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    
    # Get user's interaction history
    user_interactions = db.query(Interaction).filter(
        Interaction.user_id == user_id
    ).all()
    
    if not user_interactions:
        # Cold start - return popular items
        items = get_popular_items(db, limit)
        cache.set(cache_key, items, ttl=300)
        return items
    
    # Get user's preferred categories
    user_categories = []
    for interaction in user_interactions:
        if interaction.item:
            user_categories.append(interaction.item.category)
    
    category_counts = Counter(user_categories)
    top_categories = [cat for cat, _ in category_counts.most_common(3)]
    
    # Get items the user has already interacted with
    interacted_item_ids = {interaction.item_id for interaction in user_interactions}
    
    # Get recommendations from similar users and categories
    recommended_items = []
    
    # Category-based recommendations
    if top_categories:
        for category in top_categories:
            category_items = db.query(Item).filter(
                and_(
                    Item.category == category,
                    Item.in_stock == True,
                    ~Item.id.in_(interacted_item_ids)
                )
            ).order_by(desc(Item.popularity_score)).limit(limit//2).all()
            
            for item in category_items:
                if item not in recommended_items:
                    recommended_items.append(item)
                    if len(recommended_items) >= limit:
                        break
    
    # Fill remaining slots with popular items
    if len(recommended_items) < limit:
        popular_items = get_popular_items(db, limit - len(recommended_items))
        for item in popular_items:
            if item.id not in interacted_item_ids and item not in recommended_items:
                recommended_items.append(item)
                if len(recommended_items) >= limit:
                    break
    
    # Cache and return
    final_recommendations = recommended_items[:limit]
    cache.set(cache_key, final_recommendations, ttl=300)
    return final_recommendations