# search_optimized.py
from sqlalchemy import func, text
from typing import List, Optional
import asyncio
from concurrent.futures import ThreadPoolExecutor

class OptimizedSearchEngine:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=4)
    
    async def search(self, db, request):
        """Optimized search for 100k+ products."""
        
        # Use full-text search for text queries
        if request.query:
            # PostgreSQL full-text search
            search_query = func.plainto_tsquery('english', request.query)
            
            base_query = db.query(Item).filter(
                Item.search_vector.op('@@')(search_query)
            )
            
            # Add relevance ranking
            base_query = base_query.order_by(
                func.ts_rank(Item.search_vector, search_query).desc()
            )
        else:
            base_query = db.query(Item)
        
        # Apply filters efficiently
        if request.category:
            base_query = base_query.filter(Item.category == request.category)
        
        if request.in_stock_only:
            base_query = base_query.filter(Item.in_stock == True)
        
        if request.min_price:
            base_query = base_query.filter(Item.price >= request.min_price)
        
        if request.max_price:
            base_query = base_query.filter(Item.price <= request.max_price)
        
        # Use LIMIT and OFFSET for pagination
        total = base_query.count()
        items = base_query.offset(request.offset).limit(request.limit).all()
        
        return {
            'items': items,
            'total': total
        }