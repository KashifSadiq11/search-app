"""
Enhanced Rec Engine API with Intent-Aware Semantic Search

Key improvements:
1. Full query intent validation - ALL query tokens must be supported
2. Strict filtering for multi-word queries to prevent unrelated results
3. Semantic coherence scoring for better ranking
4. Configurable thresholds via environment variables
5. Intelligent spell correction for typos, phonetic errors, and common mistakes
"""

from sqlalchemy import and_, or_, func, desc, asc
from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks, Query
from sqlalchemy.orm import Session, load_only
from typing import List, Optional, Dict, Any, Tuple, Set
from collections import Counter
from datetime import datetime, timedelta
import logging
import os
import time
import sys
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json
import asyncio
from fastapi.responses import HTMLResponse
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
import numpy as np
import faiss
from functools import lru_cache
from llm_query_processor import get_llm_metrics, reset_llm_metrics
import uuid


# ============================================================
# OBSERVABILITY MODULE (non-invasive instrumentation)
# ============================================================
try:
    from observability import setup_observability, metrics, get_logger as get_obs_logger
    from observability.decorators import timed_stage, validate_and_track_query
    from observability.collectors import search_quality
    OBSERVABILITY_AVAILABLE = True
except ImportError:
    OBSERVABILITY_AVAILABLE = False
    # Provide no-op fallbacks
    def setup_observability(app): pass
    class NoOpMetrics:
        def record_search_quality(self, *args, **kwargs): pass
        def record_dependency(self, *args, **kwargs): pass
        def record_cache(self, *args, **kwargs): pass
        vector_index_size = type('obj', (object,), {'set': lambda self, x: None})()
        llm_cache_size = type('obj', (object,), {'set': lambda self, x: None})()
    metrics = NoOpMetrics()
    class NoOpStage:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def timed_stage(name): return NoOpStage()
    def validate_and_track_query(q): return None
    class NoOpCollector:
        def record_search(self, *args, **kwargs): pass
    search_quality = NoOpCollector()

from dotenv import load_dotenv
from pydantic import BaseModel, Field
try:
    from rapidfuzz.distance import Levenshtein as RapidLevenshtein
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    RapidLevenshtein = None
# ============================================================
# ENTERPRISE-SAFE .ENV LOADING (absolute + deterministic)
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

if not RAPIDFUZZ_AVAILABLE:
    logger.warning("rapidfuzz not installed. Using slower pure-Python fuzzy matching. "
                   "Install with: pip install rapidfuzz")
else:
    logger.info("✓ rapidfuzz available for optimized fuzzy matching")

# ============================================================
# SUPPRESS VERBOSE PYMONGO LOGS
# ============================================================
logging.getLogger("pymongo").setLevel(logging.WARNING)
logging.getLogger("pymongo.topology").setLevel(logging.WARNING)
logging.getLogger("pymongo.connection").setLevel(logging.WARNING)
logging.getLogger("pymongo.command").setLevel(logging.WARNING)
logging.getLogger("pymongo.serverSelection").setLevel(logging.WARNING)

print("=" * 60)
print("STARTING REC ENGINE API (ENHANCED)")
print("=" * 60)

logger.info("Step 1: Starting imports...")

logger.warning(f"✅ Loaded .env from: {ENV_PATH} (exists={ENV_PATH.exists()})")
logger.warning(f"✅ SEMANTIC_MIN_SIMILARITY(env)={os.getenv('SEMANTIC_MIN_SIMILARITY')}")

try:
    from .mongo_lookup import (
        fetch_business_identities_by_seller_ids,
        init_mongo_connection,
        close_mongo_connection,
    )
except ImportError:
    from mongo_lookup import (
        fetch_business_identities_by_seller_ids,
        init_mongo_connection,
        close_mongo_connection,
    )

# Import database and models
try:
    from .database import get_db, init_db
    from .models import User, Item as ItemModel, Interaction
    from .schemas import (
        User as UserSchema,
        Item as ItemSchema,
        UserCreate,
        ItemCreate,
        InteractionCreate,
        SearchRequest,
        SearchResponse,
        RecommendRequest,
        RecommendResponse,
        RecommendationItem,
        SimilarItemsRequest,
        ShopSummary,
    )
    from .search import EnhancedSearchEngine
    from .recommend import EnhancedRecommendationEngine
    from .memory_cache import cache, get_redis_client
    logger.info("  ✓ Using relative imports")
except ImportError:
    from database import get_db, init_db
    from models import User, Item as ItemModel, Interaction
    from schemas import (
        User as UserSchema,
        Item as ItemSchema,
        UserCreate,
        ItemCreate,
        InteractionCreate,
        SearchRequest,
        SearchResponse,
        RecommendRequest,
        RecommendResponse,
        RecommendationItem,
        SimilarItemsRequest,
        ShopSummary,
    )
    from search import EnhancedSearchEngine
    from recommend import EnhancedRecommendationEngine
    from memory_cache import cache, get_redis_client
    logger.info("  ✓ Using absolute imports")

# Try to import ML modules
logger.info("Step 2: Checking ML modules...")
ML_ENABLED = False
try:
    logger.info("  Importing ml_engine...")
    try:
        from .ml_engine import MLRecommendationEngine, DeepLearningRecommender
    except ImportError:
        from ml_engine import MLRecommendationEngine, DeepLearningRecommender
    logger.info("  ✓ ml_engine imported")

    logger.info("  Importing nlp_search...")
    try:
        from .nlp_search import NLPSearchEngine
    except ImportError:
        from nlp_search import NLPSearchEngine
    logger.info("  ✓ nlp_search imported")

    logger.info("  Importing image_ml...")
    try:
        from .image_ml import ImageSimilarityEngine
    except ImportError:
        from image_ml import ImageSimilarityEngine
    logger.info("  ✓ image_ml imported")

    logger.info("  Importing predictive_analytics...")
    try:
        from .predictive_analytics import PredictiveAnalytics
    except ImportError:
        from predictive_analytics import PredictiveAnalytics
    logger.info("  ✓ predictive_analytics imported")

    ML_ENABLED = True
    logger.info("✓ All ML modules loaded successfully")
except ImportError as e:
    logger.warning(f"✗ ML modules not available: {e}")
    ML_ENABLED = False

# Import LLM query processor (OpenAI-based)
logger.info("Step 2b: Importing LLM query processor...")
LLM_QUERY_PROCESSOR_AVAILABLE = False
try:
    try:
        from .llm_query_processor import (
            get_query_processor,
            process_query_async,
            process_query,
            QueryProcessingResult,
            clear_cache as clear_llm_cache,
            get_cache_stats as get_llm_cache_stats,
            get_cache_entry,
            delete_cache_entry,
            update_cache_entry,
            LLM_ENABLED,
            OPENAI_AVAILABLE,
        )
    except ImportError:
        from llm_query_processor import (
            get_query_processor,
            process_query_async,
            process_query,
            QueryProcessingResult,
            clear_cache as clear_llm_cache,
            get_cache_stats as get_llm_cache_stats,
            get_cache_entry,
            delete_cache_entry,
            update_cache_entry,
            LLM_ENABLED,
            OPENAI_AVAILABLE,
        )
    LLM_QUERY_PROCESSOR_AVAILABLE = OPENAI_AVAILABLE
    logger.info(f"  ✓ LLM query processor imported (enabled={LLM_ENABLED}, openai={OPENAI_AVAILABLE})")
except ImportError as e:
    logger.warning(f"  ✗ LLM query processor not available: {e}")
    LLM_QUERY_PROCESSOR_AVAILABLE = False
    LLM_ENABLED = False
    OPENAI_AVAILABLE = False
    
    # Create dummy function
    async def process_query_async(query: str):
        from dataclasses import dataclass
        @dataclass
        class DummyResult:
            original_query: str = query
            corrected_query: str = query
            was_corrected: bool = False
            intent: str = "product_search"
            product_type: str = None
            modifier: str = None
            related_terms: list = None
            confidence: float = 1.0
            processing_time_ms: float = 0.0
            from_cache: bool = False
            error: str = None
            def __post_init__(self):
                self.related_terms = []
        return DummyResult()

# Initialize engines
logger.info("Step 3: Creating engines...")
search_engine = EnhancedSearchEngine()
logger.info("  ✓ Search engine created")
recommendation_engine = EnhancedRecommendationEngine()
logger.info("  ✓ Recommendation engine created")

# Initialize ML engines 
ml_engine = None
nlp_engine = None
image_engine = None
predictive_engine = None

app = FastAPI(
    title="Enhanced Rec Engine API",
    description="Production-ready recommendation engine with ML/AI capabilities",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# OBSERVABILITY SETUP (adds /metrics endpoint + middleware)
# ============================================================
if OBSERVABILITY_AVAILABLE:
    setup_observability(app)
    logger.info("✅ Observability module initialized")
else:
    logger.warning("⚠️ Observability module not available (optional)")

# Replace the startup_event function in main.py with this version:

@app.on_event("startup")
async def startup_event():
    """Initialize database and engines on startup."""
    global ML_ENABLED, ml_engine, nlp_engine, image_engine, predictive_engine

    logger.info("=" * 60)
    logger.info("STARTUP EVENT TRIGGERED")
    logger.info("=" * 60)

    # Initialize MongoDB connection (logs only once internally)
    mongo_ok = init_mongo_connection()
    if not mongo_ok:
        logger.warning("MongoDB connection failed (will retry on first query)")

    logger.info("Initializing database...")
    init_db()
    logger.info("  ✓ Database initialized")

    logger.info("Initializing search engine...")
    await search_engine.initialize()
    logger.info("  ✓ Search engine initialized")

    logger.info("Initializing recommendation engine...")
    await recommendation_engine.initialize()
    logger.info("  ✓ Recommendation engine initialized")

    if ML_ENABLED:
        try:
            logger.info("Initializing ML components...")

            ml_engine = MLRecommendationEngine()
            nlp_engine = NLPSearchEngine()
            image_engine = ImageSimilarityEngine()
            predictive_engine = PredictiveAnalytics()

            index_obj = getattr(ml_engine, "index", None)
            index_total = int(getattr(index_obj, "ntotal", 0) or 0)

            if index_total > 0:
                logger.info(f"  ✓ Loaded vector index with {index_total} items")
                # Update observability metrics
                if OBSERVABILITY_AVAILABLE:
                    metrics.vector_index_size.set(index_total)
            else:
                logger.warning(
                    "⚠ No FAISS index loaded. "
                    "Semantic search will NOT work until you build indexes offline."
                )

            logger.info("✓ ML/AI engines initialized successfully!")

        except Exception as e:
            logger.error(f"Failed to initialize ML engines: {e}")
            import traceback
            logger.error(traceback.format_exc())
            ML_ENABLED = False
    else:
        logger.warning("Running without ML/AI features")

    # Log LLM query processor status
    if LLM_QUERY_PROCESSOR_AVAILABLE and LLM_ENABLED:
        logger.info("✓ LLM Query Processor ready (OpenAI-based)")

    logger.info("=" * 60)
    logger.info("🚀 API STARTED SUCCESSFULLY!")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    close_mongo_connection()

@app.get("/")
def read_root():
    """Root endpoint."""
    return {
        "message": "Welcome to Enhanced Rec Engine API",
        "version": "2.2.0",
        "ml_enabled": ML_ENABLED,
        "llm_query_processing": LLM_QUERY_PROCESSOR_AVAILABLE and LLM_ENABLED,
        "docs": "/docs",
    }

@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "rec-engine",
        "ml_enabled": ML_ENABLED,
        "llm_query_processing": LLM_QUERY_PROCESSOR_AVAILABLE and LLM_ENABLED,
        "observability_enabled": OBSERVABILITY_AVAILABLE,
    }


# ================================
# OBSERVABILITY ENDPOINTS
# ================================

@app.get("/observability/stats")
def get_observability_stats(window_seconds: int = 300):
    """
    Get aggregated observability statistics for the given time window.

    Args:
        window_seconds: Time window for aggregation (default 5 minutes)

    Returns:
        Aggregated metrics including latency percentiles, error rates, and search quality.
    """
    if not OBSERVABILITY_AVAILABLE:
        return {"error": "Observability module not available", "enabled": False}

    try:
        stats = search_quality.get_stats(window_seconds)
        return {
            "enabled": True,
            "window_seconds": window_seconds,
            "stats": stats.to_dict(),
        }
    except Exception as e:
        return {"error": str(e), "enabled": True}


@app.get("/observability/recent")
def get_recent_search_events(limit: int = 50, since_seconds: int = 300):
    """
    Get recent search events for debugging.

    Args:
        limit: Maximum number of events to return
        since_seconds: Only events within last N seconds

    Returns:
        List of recent search events with anonymized data.
    """
    if not OBSERVABILITY_AVAILABLE:
        return {"error": "Observability module not available", "enabled": False}

    try:
        events = search_quality.get_recent_events(limit=limit, since_seconds=since_seconds)
        return {
            "enabled": True,
            "count": len(events),
            "events": events,
        }
    except Exception as e:
        return {"error": str(e), "enabled": True}


# ================================
# LLM QUERY PROCESSING ENDPOINTS
# ================================
# Add these endpoints to your main.py file
# Replace the existing LLM endpoints section

@app.get("/llm/process-query")
async def test_llm_query_processing(query: str):
    """
    Test LLM query processing (spell correction + intent + related terms).
    
    Examples:
    - /llm/process-query?query=office%20beg -> office bag + related terms
    - /llm/process-query?query=fry%20pen -> fry pan + related terms
    - /llm/process-query?query=hodie -> hoodie + related terms
    """
    if not LLM_QUERY_PROCESSOR_AVAILABLE or not LLM_ENABLED:
        return {
            "error": "LLM query processing is not enabled",
            "original_query": query,
            "corrected_query": query,
        }
    
    try:
        result = await process_query_async(query)
        
        return {
            "original_query": result.original_query,
            "corrected_query": result.corrected_query,
            "was_corrected": result.was_corrected,
            "intent": result.intent,
            "product_type": result.product_type,
            "modifier": result.modifier,
            "related_terms": result.related_terms,
            "confidence": round(result.confidence, 3),
            "processing_time_ms": round(result.processing_time_ms, 1),
            "from_cache": result.from_cache,
        }
    except Exception as e:
        logger.error(f"LLM query processing test failed: {e}")
        return {
            "error": str(e),
            "original_query": query,
            "corrected_query": query,
        }


@app.get("/llm/cache-stats")
def get_llm_cache_statistics():
    """
    Get LLM query cache statistics from Redis.
    
    Returns:
    - Total cached queries
    - Redis connection status
    - TTL settings
    - Sample entries
    - Memory usage
    """
    if not LLM_QUERY_PROCESSOR_AVAILABLE:
        return {"error": "LLM query processing is not available"}
    
    try:
        return get_llm_cache_stats()
    except Exception as e:
        return {"error": str(e)}


@app.post("/llm/clear-cache")
def clear_llm_query_cache():
    """
    Clear ALL LLM query cache entries from Redis.
    
    WARNING: This will delete all cached queries!
    
    Returns:
    - Number of entries deleted
    - Success/failure status
    """
    if not LLM_QUERY_PROCESSOR_AVAILABLE:
        return {"error": "LLM query processing is not available"}
    
    try:
        return clear_llm_cache()
    except Exception as e:
        return {"error": str(e)}


@app.get("/llm/cache/get")
def get_llm_cache_entry(query: str = Query(..., description="The search query to lookup (e.g., 'air pods pro')")):
    """
    Get cached data for a specific query from Redis.
    
    Args:
        query: The user's search query (e.g., "air pods pro")
    
    Returns:
        - original_query: What user typed
        - corrected_query: LLM corrected version
        - related_terms: Semantically related terms
        - TTL remaining
        - Redis key used
    
    Example:
        GET /llm/cache/get?query=air%20pods%20pro
    """
    if not LLM_QUERY_PROCESSOR_AVAILABLE:
        return {"error": "LLM query processing is not available"}
    
    if not query or not query.strip():
        return {"error": "Query parameter is required"}
    
    try:
        # Import the function from llm_query_processor
        from llm_query_processor import get_cache_entry
        return get_cache_entry(query.strip())
    except ImportError:
        from .llm_query_processor import get_cache_entry
        return get_cache_entry(query.strip())
    except Exception as e:
        logger.error(f"Failed to get cache entry for '{query}': {e}")
        return {
            "error": str(e),
            "query": query,
            "found": False,
        }


@app.delete("/llm/cache/delete")
def delete_llm_cache_entry(query: str = Query(..., description="The search query to delete (e.g., 'air pods pro')")):
    """
    Delete cached data for a specific query from Redis.
    
    Args:
        query: The user's search query to delete (e.g., "air pods pro")
    
    Returns:
        - deleted: True/False
        - deleted_data: What was deleted (original_query, corrected_query, related_terms)
        - Redis key that was deleted
    
    Example:
        DELETE /llm/cache/delete?query=air%20pods%20pro
    """
    if not LLM_QUERY_PROCESSOR_AVAILABLE:
        return {"error": "LLM query processing is not available"}
    
    if not query or not query.strip():
        return {"error": "Query parameter is required"}
    
    try:
        # Import the function from llm_query_processor
        from llm_query_processor import delete_cache_entry
        return delete_cache_entry(query.strip())
    except ImportError:
        from .llm_query_processor import delete_cache_entry
        return delete_cache_entry(query.strip())
    except Exception as e:
        logger.error(f"Failed to delete cache entry for '{query}': {e}")
        return {
            "error": str(e),
            "query": query,
            "deleted": False,
        }

@app.get("/llm/metrics")
def get_llm_performance_metrics():
    """
    Get LLM performance metrics including:
    - Cache hit/miss rate
    - OpenAI latency percentiles (p50, p90, p95, p99)
    """
    if not LLM_QUERY_PROCESSOR_AVAILABLE:
        return {"error": "LLM query processing is not available"}
    return get_llm_metrics()


@app.post("/llm/metrics/reset")
def reset_llm_performance_metrics():
    """Reset LLM metrics counters."""
    if not LLM_QUERY_PROCESSOR_AVAILABLE:
        return {"error": "LLM query processing is not available"}
    return reset_llm_metrics()

# ================================
# LLM CACHE UPDATE MODEL
# ================================
class LLMCacheUpdateRequest(BaseModel):
    """Request model for updating LLM cache entries."""
    corrected_query: Optional[str] = Field(None, description="Override the corrected query")
    was_corrected: Optional[bool] = Field(None, description="Override the was_corrected flag")
    intent: Optional[str] = Field(None, description="Override intent: 'product_search' or 'shop_search'")
    product_type: Optional[str] = Field(None, description="Override product type (e.g., 'pen', 'bag')")
    modifier: Optional[str] = Field(None, description="Override modifier (e.g., 'ball', 'office')")
    related_terms: Optional[List[str]] = Field(None, description="Override related search terms")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Override confidence score")


@app.patch("/llm/cache/update")
def update_llm_cache_entry_endpoint(
    query: str = Query(..., description="The search query to update (e.g., 'bell pen')"),
    updates: LLMCacheUpdateRequest = None
):
    """
    Update specific fields in a cached LLM query result.
    
    This allows manual correction of LLM outputs for specific queries.
    Only non-null fields in the request body will be updated.
    
    **Use Cases:**
    - Fix spelling correction: "bell pen" → corrected_query: "ball pen"
    - Add better related terms for query expansion
    - Change product_type/modifier for better search matching
    - Override intent detection
    
    **Example:**
```
    PATCH /llm/cache/update?query=bell%20pen
    {
        "corrected_query": "ball pen",
        "product_type": "pen", 
        "modifier": "ball",
        "related_terms": ["ballpoint pen", "ball point pen", "writing pen"]
    }
```
    """
    if not LLM_QUERY_PROCESSOR_AVAILABLE:
        return {"error": "LLM query processing is not available"}
    
    if not query or not query.strip():
        return {"error": "Query parameter is required"}
    
    if updates is None:
        return {"error": "Request body with updates is required"}
    
    # Build updates dict from non-None fields
    updates_dict: Dict[str, Any] = {}
    if updates.corrected_query is not None:
        updates_dict["corrected_query"] = updates.corrected_query
    if updates.was_corrected is not None:
        updates_dict["was_corrected"] = updates.was_corrected
    if updates.intent is not None:
        updates_dict["intent"] = updates.intent
    if updates.product_type is not None:
        updates_dict["product_type"] = updates.product_type
    if updates.modifier is not None:
        updates_dict["modifier"] = updates.modifier
    if updates.related_terms is not None:
        updates_dict["related_terms"] = updates.related_terms
    if updates.confidence is not None:
        updates_dict["confidence"] = updates.confidence
    
    if not updates_dict:
        return {
            "updated": False,
            "query": query,
            "error": "No fields to update. Provide at least one field."
        }
    
    try:
        return update_cache_entry(query.strip(), updates_dict)
    except Exception as e:
        logger.error(f"Failed to update cache entry for '{query}': {e}")
        return {
            "error": str(e),
            "query": query,
            "updated": False,
        }


@app.put("/llm/cache/set")
def set_llm_cache_entry_endpoint(
    query: str = Query(..., description="The search query to cache"),
    data: LLMCacheUpdateRequest = None
):
    """
    Create or completely replace a cached LLM query result.
    
    Unlike PATCH /update, this creates a new entry or replaces existing one entirely.
    Useful for pre-populating cache with known good corrections.
    
    **Example:**
```
    PUT /llm/cache/set?query=bell%20pen
    {
        "corrected_query": "ball pen",
        "intent": "product_search",
        "product_type": "pen",
        "modifier": "ball",
        "related_terms": ["ballpoint pen", "ball point pen"],
        "confidence": 0.95
    }
```
    """
    if not LLM_QUERY_PROCESSOR_AVAILABLE:
        return {"error": "LLM query processing is not available"}
    
    if not query or not query.strip():
        return {"error": "Query parameter is required"}
    
    if data is None:
        return {"error": "Request body is required"}
    
    query = query.strip()
    
    try:
        try:
            from llm_query_processor import _get_cache, QueryProcessingResult
        except ImportError:
            from .llm_query_processor import _get_cache, QueryProcessingResult
        
        cache = _get_cache()
        
        # Build complete cache entry
        corrected = data.corrected_query or query
        was_corrected = data.was_corrected if data.was_corrected is not None else (corrected.lower().strip() != query.lower().strip())
        
        result = QueryProcessingResult(
            original_query=query,
            corrected_query=corrected,
            was_corrected=was_corrected,
            intent=data.intent or "product_search",
            product_type=data.product_type,
            modifier=data.modifier,
            related_terms=data.related_terms or [],
            confidence=data.confidence or 0.9,
            processing_time_ms=0.0,
        )
        
        # Cache by both original and corrected
        cache.set_both(query, result)
        
        return {
            "created": True,
            "query": query,
            "cached_data": {
                "original_query": query,
                "corrected_query": corrected,
                "was_corrected": was_corrected,
                "intent": data.intent or "product_search",
                "product_type": data.product_type,
                "modifier": data.modifier,
                "related_terms": data.related_terms or [],
                "confidence": data.confidence or 0.9,
                "manually_created": True,
            },
            "message": "Cache entry created/replaced successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to set cache entry for '{query}': {e}")
        return {
            "error": str(e),
            "query": query,
            "created": False,
        }

# ================================
# SHOP ITEMS ENDPOINT (Product-First)
# ================================
# Note: Shops are derived from products, not searched independently
# This endpoint gets products for a specific seller

@app.get("/search/shops/{seller_id}/items")
async def get_shop_items(
    seller_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    Get all items from a specific shop/seller.
    Products first - shop info derived from products.
    """
    try:
        # Get items for this seller
        items = db.query(ItemModel).filter(
            ItemModel.seller_info.isnot(None)
        ).all()
        
        # Filter by seller_id
        seller_items = [
            item for item in items
            if item.seller_info and str(item.seller_info.get("id", "")) == seller_id
        ]
        
        # Get seller info from first item (derived from products)
        seller_info = seller_items[0].seller_info if seller_items else {}
        
        # Paginate
        total = len(seller_items)
        page_items = seller_items[offset:offset + limit]
        
        return {
            "seller_id": seller_id,
            "seller_name": seller_info.get("name"),
            "user_name": seller_info.get("userName"),
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [ItemSchema.model_validate(item) for item in page_items],
        }
        
    except Exception as e:
        logger.error(f"[SHOP_ITEMS] Error: {e}")
        return {
            "seller_id": seller_id,
            "error": str(e),
            "total": 0,
            "items": [],
        }


# ================================
# USER ENDPOINTS
# ================================

@app.post("/users/", response_model=UserSchema)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    """Create a new user."""
    try:
        db_user = User(id=user.id, username=user.username, email=user.email)
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        logger.info(f"Created user: {db_user.id} ({db_user.username})")
        return db_user
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/users/{user_id}", response_model=UserSchema)
def get_user(user_id: str, db: Session = Depends(get_db)):
    """Get user by ID."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/users/", response_model=List[UserSchema])
def list_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """List all users."""
    users = db.query(User).offset(skip).limit(limit).all()
    return users


# ================================
# ITEM ENDPOINTS
# ================================

@app.post("/items/", response_model=ItemSchema)
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    """Create a new item."""
    try:
        db_item = ItemModel(**item.dict())
        db.add(db_item)
        db.commit()
        db.refresh(db_item)
        logger.info(f"Created item: {item.title}")
        return db_item
    except Exception as e:
        logger.error(f"Error creating item: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/items/{item_id}", response_model=ItemSchema)
def get_item(item_id: str, db: Session = Depends(get_db)):
    """Get item by ID."""
    item = db.query(ItemModel).filter(ItemModel.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.get("/items/", response_model=List[ItemSchema])
def list_items(
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
    in_stock_only: bool = False,
    db: Session = Depends(get_db),
):
    """List all items with optional filters."""
    query = db.query(ItemModel)

    if category:
        query = query.filter(ItemModel.category == category)
    if in_stock_only:
        query = query.filter(ItemModel.in_stock == True)

    items = query.offset(skip).limit(limit).all()
    return items


# ================================
# UI
# ================================

@app.get("/ui", response_class=HTMLResponse)
async def serve_ui():
    """Serve the recommendation engine UI."""
    current_dir = Path(__file__).parent
    ui_path = (current_dir / ".." / ".." / "ui" / "rec_engine_ui.html").resolve()

    if ui_path.exists():
        with open(ui_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        html_content = html_content.replace(
            "const API_URL = 'http://localhost:8000'",
            "const API_URL = window.location.origin",
        )
        return HTMLResponse(content=html_content)

    return HTMLResponse(
        content=f"<h1>UI file not found</h1><p>Looking for: {ui_path}</p>",
        status_code=404,
    )


# ================================
# INTERACTION ENDPOINTS
# ================================

@app.post("/interactions/")
def create_interaction(interaction: InteractionCreate, db: Session = Depends(get_db)):
    """Record user interaction with item."""
    try:
        db_interaction = Interaction(**interaction.dict())
        db.add(db_interaction)
        db.commit()
        logger.info(f"Recorded interaction: {interaction.user_id} -> {interaction.item_id}")
        return {"message": "Interaction recorded", "id": db_interaction.id}
    except Exception as e:
        logger.error(f"Error recording interaction: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ================================
# ENHANCED SEARCH ENDPOINTS
# ================================

@app.post("/search/enhanced/", response_model=SearchResponse)
async def enhanced_search(request: SearchRequest, db: Session = Depends(get_db)):
    """Enhanced search with facets and filters."""
    try:
        results = await search_engine.search(db, request)
        return results
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


# ============================================================
# INTENT-AWARE SEMANTIC SEARCH IMPLEMENTATION
# ============================================================

# Configuration via environment variables
INTENT_MATCH_STRICT = os.getenv("INTENT_MATCH_STRICT", "true").lower() == "true"
INTENT_MIN_TOKEN_MATCH_RATIO = float(os.getenv("INTENT_MIN_TOKEN_MATCH_RATIO", "1.0"))  # 1.0 = ALL tokens must match
INTENT_FALLBACK_TO_FAISS = os.getenv("INTENT_FALLBACK_TO_FAISS", "true").lower() == "true"
INTENT_MAX_TIER_THRESHOLD = int(os.getenv("INTENT_MAX_TIER_THRESHOLD", "50"))  # Filter out items with tier > this

logger.info(f"✅ Intent Search Config: STRICT={INTENT_MATCH_STRICT}, MIN_RATIO={INTENT_MIN_TOKEN_MATCH_RATIO}, FALLBACK={INTENT_FALLBACK_TO_FAISS}")

_WORD_RE = re.compile(r"[a-z0-9]+")

@lru_cache(maxsize=5000)
def _norm_text(s: Optional[str]) -> str:
    """Normalize text: lowercase, collapse whitespace. CACHED."""
    if not s:
        return ""
    return " ".join(s.strip().lower().split())

@lru_cache(maxsize=10000)
def _cached_tokens(text: str) -> Tuple[str, ...]:
    """
    Tokenize text and return as frozen tuple (hashable for caching).
    Cache prevents re-tokenizing the same title/category multiple times.
    """
    if not text:
        return ()
    return tuple(_WORD_RE.findall(text.lower()))

def _tokens(s: str) -> List[str]:
    """Extract alphanumeric tokens from text. (returns list for compatibility)"""
    return list(_cached_tokens(_norm_text(s)))


def _norm_sku(s: Optional[str]) -> str:
    """Normalize SKU: remove all non-alphanumeric, lowercase."""
    if not s:
        return ""
    return "".join(ch for ch in s.strip().lower() if ch.isalnum())


def _looks_like_sku_query(q: str) -> bool:
    """Check if query looks like a SKU (alphanumeric with digits)."""
    t = _norm_sku(q)
    if len(t) < 3:
        return False
    has_digit = any(c.isdigit() for c in t)
    has_alpha = any(c.isalpha() for c in t)
    return has_digit and (has_alpha or len(t) >= 6)


# --------------------------------
# OPTIMIZED LEVENSHTEIN DISTANCE
# --------------------------------
@lru_cache(maxsize=50000)
def _levenshtein_distance_cached(a: str, b: str, max_distance: int) -> int:
    """
    Cached Levenshtein distance computation.
    Uses rapidfuzz if available, otherwise falls back to pure Python.
    
    Returns max_distance + 1 if distance exceeds max_distance (early termination).
    """
    if a == b:
        return 0
    if not a:
        return len(b) if len(b) <= max_distance else max_distance + 1
    if not b:
        return len(a) if len(a) <= max_distance else max_distance + 1
    
    # Length-based early termination
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    
    if RAPIDFUZZ_AVAILABLE and RapidLevenshtein is not None:
        # rapidfuzz with score_cutoff for early termination
        dist = RapidLevenshtein.distance(a, b, score_cutoff=max_distance)
        # rapidfuzz returns max_distance + 1 when exceeding cutoff
        return dist if dist <= max_distance else max_distance + 1
    else:
        # Pure Python fallback
        return _levenshtein_pure_python(a, b, max_distance)


def _levenshtein_pure_python(a: str, b: str, max_distance: int) -> int:
    """Pure Python Levenshtein with bounded computation."""
    if len(a) > len(b):
        a, b = b, a
    
    la, lb = len(a), len(b)
    prev = list(range(la + 1))
    
    for j in range(1, lb + 1):
        bj = b[j - 1]
        cur = [j] + [0] * la
        row_min = cur[0]
        
        for i in range(1, la + 1):
            cost = 0 if a[i - 1] == bj else 1
            cur[i] = min(prev[i] + 1, cur[i - 1] + 1, prev[i - 1] + cost)
            if cur[i] < row_min:
                row_min = cur[i]
        
        if row_min > max_distance:
            return max_distance + 1
        prev = cur
    
    return prev[la] if prev[la] <= max_distance else max_distance + 1


def _levenshtein_distance_bounded(a: str, b: str, max_distance: int) -> int:
    """
    Compute Levenshtein distance with early termination.
    Returns max_distance + 1 if distance exceeds max_distance.
    
    OPTIMIZED: Uses caching and rapidfuzz when available.
    """
    return _levenshtein_distance_cached(a or "", b or "", max_distance)

def _max_dist_for_query(raw_query: str) -> int:
    """Determine max typo distance based on query token lengths."""
    q_toks = _tokens(raw_query)
    longest = max((len(t) for t in q_toks), default=0)
    # Short words (<=4): no typos allowed
    # Medium words (5-7): 1 typo
    # Long words (8+): 2 typos
    if longest <= 4:
        return 0
    elif longest <= 7:
        return 1
    else:
        return 2


# --------------------------------
# OPTIMIZED TOKEN MATCHING
# --------------------------------
@lru_cache(maxsize=100000)
def _token_matches_word_cached(token: str, word: str, max_distance: int) -> bool:
    """Cached check if token matches word (exact or fuzzy)."""
    if token == word:
        return True
    if max_distance > 0:
        return _levenshtein_distance_cached(token, word, max_distance) <= max_distance
    return False

def _token_matches_word(token: str, word: str, max_distance: int) -> bool:
    """Check if token matches word (exact or fuzzy). Uses cache."""
    return _token_matches_word_cached(token, word, max_distance)


def _token_matches_any_in_text(token: str, text: str, max_distance: int) -> bool:
    """Check if token matches any word in text. Optimized with caching."""
    text_tokens = _cached_tokens(_norm_text(text or ""))
    if not text_tokens:
        return False
    
    for tt in text_tokens:
        if _token_matches_word_cached(token, tt, max_distance):
            return True
    return False

# --------------------------------
# CACHE MANAGEMENT FUNCTIONS
# --------------------------------
def clear_fuzzy_match_caches():
    """Clear all fuzzy matching caches. Call periodically to free memory."""
    _norm_text.cache_clear()
    _cached_tokens.cache_clear()
    _levenshtein_distance_cached.cache_clear()
    _token_matches_word_cached.cache_clear()
    logger.info("Fuzzy matching caches cleared")


def get_fuzzy_match_cache_stats() -> Dict[str, Any]:
    """Get cache statistics for monitoring."""
    return {
        "norm_text_cache": {
            "hits": _norm_text.cache_info().hits,
            "misses": _norm_text.cache_info().misses,
            "size": _norm_text.cache_info().currsize,
            "maxsize": _norm_text.cache_info().maxsize,
        },
        "tokens_cache": {
            "hits": _cached_tokens.cache_info().hits,
            "misses": _cached_tokens.cache_info().misses,
            "size": _cached_tokens.cache_info().currsize,
            "maxsize": _cached_tokens.cache_info().maxsize,
        },
        "levenshtein_cache": {
            "hits": _levenshtein_distance_cached.cache_info().hits,
            "misses": _levenshtein_distance_cached.cache_info().misses,
            "size": _levenshtein_distance_cached.cache_info().currsize,
            "maxsize": _levenshtein_distance_cached.cache_info().maxsize,
        },
        "token_match_cache": {
            "hits": _token_matches_word_cached.cache_info().hits,
            "misses": _token_matches_word_cached.cache_info().misses,
            "size": _token_matches_word_cached.cache_info().currsize,
            "maxsize": _token_matches_word_cached.cache_info().maxsize,
        },
        "rapidfuzz_available": RAPIDFUZZ_AVAILABLE,
    }

# --------------------------------
# QUERY INTENT ANALYSIS
# --------------------------------
# NOTE: All semantic understanding is now handled by OpenAI LLM
# No hardcoded product types or relationships needed!

@dataclass
class QueryIntent:
    """Structured representation of query intent."""
    raw_query: str
    normalized: str
    tokens: List[str]
    token_count: int
    is_phrase_query: bool  # 2+ tokens
    is_sku_query: bool
    head_noun: Optional[str]  # e.g., "bag" in "office bag" - set by LLM
    modifier: Optional[str]   # e.g., "office" in "office bag" - set by LLM
    is_product_type_query: bool  # True if LLM detected product_type
    related_terms: List[str]  # Related search terms from LLM
    
    @classmethod
    def from_query(cls, query: str, llm_result=None) -> "QueryIntent":
        """
        Create QueryIntent from query string.
        If llm_result is provided, use it for product_type, modifier, and related_terms.
        """
        raw = (query or "").strip()
        normalized = _norm_text(raw)
        tokens = _tokens(raw)
        
        # Get values from LLM result if available
        head_noun = None
        modifier = None
        is_product_type_query = False
        related_terms = []
        
        if llm_result:
            head_noun = llm_result.product_type
            modifier = llm_result.modifier
            is_product_type_query = head_noun is not None
            related_terms = llm_result.related_terms or []
        
        return cls(
            raw_query=raw,
            normalized=normalized,
            tokens=tokens,
            token_count=len(tokens),
            is_phrase_query=len(tokens) >= 2,
            is_sku_query=_looks_like_sku_query(raw),
            head_noun=head_noun,
            modifier=modifier,
            is_product_type_query=is_product_type_query,
            related_terms=related_terms,
        )


# --------------------------------
# INTENT MATCHING LOGIC
# --------------------------------
@dataclass
class IntentMatchResult:
    """Result of intent matching against an item."""
    is_supported: bool  # Does item support the full query intent?
    matched_tokens: int  # Number of query tokens matched
    total_tokens: int  # Total query tokens
    match_ratio: float  # matched_tokens / total_tokens
    match_type: str  # 'exact', 'phrase_adjacent', 'phrase_scattered', 'partial', 'none'
    match_location: str  # 'title', 'category', 'both', 'none'
    tier: int  # Ranking tier (lower = better)
    score: int  # Score within tier (higher = better)


def _compute_token_matches(
    query_tokens: List[str],
    text_tokens: List[str],
    max_distance: int
) -> Tuple[int, List[int]]:
    """
    Compute how many query tokens match text tokens.
    Returns (match_count, list of matched positions in text).
    """
    if not query_tokens or not text_tokens:
        return 0, []
    
    matched = 0
    positions: List[int] = []
    
    for qt in query_tokens:
        for i, tt in enumerate(text_tokens):
            if _token_matches_word(qt, tt, max_distance):
                matched += 1
                positions.append(i)
                break  # Each query token matches at most once
    
    return matched, positions


def _is_adjacent_sequence(positions: List[int]) -> bool:
    """Check if positions form an adjacent (contiguous) sequence."""
    if len(positions) < 2:
        return True
    
    sorted_pos = sorted(positions)
    for i in range(1, len(sorted_pos)):
        if sorted_pos[i] - sorted_pos[i-1] != 1:
            return False
    return True


def _title_matches_related_terms(title: str, related_terms: List[str]) -> bool:
    """
    Check if title matches any of the LLM-provided related terms.
    This replaces hardcoded relationship checking.
    """
    if not related_terms:
        return False
    
    title_lower = title.lower()
    for term in related_terms:
        term_lower = term.lower()
        # Check if term appears in title
        if term_lower in title_lower:
            return True
        # Check if all words of term appear in title
        term_words = _tokens(term_lower)
        if all(_token_matches_any_in_text(tw, title, 1) for tw in term_words):
            return True
    
    return False


def _find_conflicting_product_type(
    intent: QueryIntent,
    title: str,
    max_distance: int = 0
) -> Optional[str]:
    """
    Simplified conflict detection using LLM-provided related terms.
    
    If title contains a different product type that's NOT in related_terms,
    it's considered a conflict.
    
    For query "office bag" with related_terms ["laptop bag", "business bag"]:
    - Title "Laptop Bag" → NO conflict (in related_terms)
    - Title "Business Bag" → NO conflict (in related_terms)
    - Title "Lunch Bag" → CONFLICT (not in related_terms)
    """
    if not intent.is_product_type_query or not intent.head_noun or not intent.modifier:
        return None
    
    # If title matches query directly, no conflict
    title_lower = title.lower()
    query_lower = intent.raw_query.lower()
    if query_lower in title_lower:
        return None
    
    # If title contains both modifier and head_noun from query, no conflict
    if intent.modifier.lower() in title_lower and intent.head_noun.lower() in title_lower:
        return None
    
    # If title matches any related term from LLM, no conflict
    if _title_matches_related_terms(title, intent.related_terms):
        return None
    
    # Check if title has the head noun - if not, no conflict to detect
    head = intent.head_noun.lower()
    if head not in title_lower:
        # Also check plural forms
        if not (head + 's' in title_lower or head + 'es' in title_lower or head.rstrip('s') in title_lower):
            return None
    
    # Title has the head noun but doesn't match query or related terms
    # This is likely a different type of the same product category
    # Extract what modifier is actually in the title
    title_tokens = _tokens(title)
    for i, tok in enumerate(title_tokens):
        if tok == head or tok == head + 's' or tok.rstrip('s') == head.rstrip('s'):
            if i > 0:
                preceding = title_tokens[i - 1]
                # It's a different modifier before the head noun
                if preceding != intent.modifier.lower():
                    return f"{preceding} {head}"
    
    return None


def _query_modifier_precedes_head_in_title(
    intent: QueryIntent,
    title: str,
    max_distance: int = 0
) -> Tuple[bool, bool, Optional[str]]:
    """
    Check if the query modifier (or a related modifier) appears BEFORE the head noun in title.
    
    Returns: (has_valid_modifier, is_exact_match, found_modifier)
    
    For query "office bag":
    - "Office Laptop Bag" → (True, True, "office") - exact match
    - "Laptop Bag for Office" → (True, False, "laptop") - related modifier before head
    - "Lunch Bag for Office" → (False, False, "lunch") - unrelated modifier before head
    - "Bag for Office" → (False, False, None) - no modifier before head
    """
    if not intent.is_product_type_query or not intent.head_noun or not intent.modifier:
        return False, False, None
    
    title_tokens = _tokens(title)
    if not title_tokens:
        return False, False, None
    
    head = intent.head_noun.lower()
    mod = intent.modifier.lower()
    
    # Find position of head noun and any preceding modifier
    head_pos = -1
    for i, tok in enumerate(title_tokens):
        if tok == head or tok == head + 's' or tok.rstrip('s') == head.rstrip('s'):
            head_pos = i
            break
    
    if head_pos < 0:
        return False, False, None
    
    # Check if modifier appears before head
    modifier_pos = -1
    for i, tok in enumerate(title_tokens):
        if i >= head_pos:
            break
        if _token_matches_word(tok, mod, 1):
            modifier_pos = i
            break
    
    # Exact match - modifier is in title before head
    if modifier_pos >= 0:
        return True, True, mod
    
    # Check if any related term's modifier is before head
    if head_pos > 0 and intent.related_terms:
        preceding_token = title_tokens[head_pos - 1]
        for related in intent.related_terms:
            related_tokens = _tokens(related)
            if len(related_tokens) >= 2:
                related_mod = related_tokens[0]
                if _token_matches_word(preceding_token, related_mod, 1):
                    return True, False, preceding_token
    
    # No valid modifier found
    if head_pos > 0:
        return False, False, title_tokens[head_pos - 1]
    
    return False, False, None


def _tokens_are_adjacent_in_text(
    query_tokens: List[str],
    text: str,
    max_distance: int
) -> bool:
    """
    Check if query tokens appear ADJACENTLY in text (in order).
    
    "office bag" in "Office Laptop Bag" -> False (not adjacent)
    "office bag" in "Office Bag" -> True (adjacent)
    "leather jacket" in "Leather Jacket Men" -> True (adjacent)
    """
    if not query_tokens:
        return True
    
    text_tokens = _tokens(text or "")
    if not text_tokens:
        return False
    
    if len(query_tokens) > len(text_tokens):
        return False
    
    # Sliding window approach
    window_size = len(query_tokens)
    for start in range(len(text_tokens) - window_size + 1):
        window = text_tokens[start:start + window_size]
        all_match = True
        for qt, wt in zip(query_tokens, window):
            if not _token_matches_word(qt, wt, max_distance):
                all_match = False
                break
        if all_match:
            return True
    
    return False


def _match_intent_against_item(
    intent: QueryIntent,
    title: str,
    category: str,
    sku: str,
) -> IntentMatchResult:
    """
    Match query intent against item metadata.
    
    This is the core matching logic that determines:
    1. Whether the item supports the full query intent
    2. How well it matches (for ranking)
    
    Key principle for phrase queries (2+ tokens):
    - ALL query tokens must be present in title OR category
    - Items missing any query token are NOT supported
    - This ensures "office bag" doesn't match "lunch bag"
    """
    max_dist = _max_dist_for_query(intent.raw_query)
    
    # Handle empty query
    if not intent.tokens:
        return IntentMatchResult(
            is_supported=False,
            matched_tokens=0,
            total_tokens=0,
            match_ratio=0.0,
            match_type='none',
            match_location='none',
            tier=99,
            score=0,
        )
    
    title_norm = _norm_text(title)
    category_norm = _norm_text(category)
    title_tokens = _tokens(title)
    category_tokens = _tokens(category)
    
    # --- EXACT TITLE MATCH ---
    if title_norm and intent.normalized == title_norm:
        return IntentMatchResult(
            is_supported=True,
            matched_tokens=intent.token_count,
            total_tokens=intent.token_count,
            match_ratio=1.0,
            match_type='exact',
            match_location='title',
            tier=0,
            score=100_000,
        )
    
    # --- SKU MATCH ---
    if intent.is_sku_query and sku:
        if _norm_sku(intent.raw_query) == _norm_sku(sku):
            return IntentMatchResult(
                is_supported=True,
                matched_tokens=intent.token_count,
                total_tokens=intent.token_count,
                match_ratio=1.0,
                match_type='exact',
                match_location='sku',
                tier=1,
                score=90_000,
            )
    
    # --- CONFLICTING PRODUCT TYPE DETECTION ---
    # For "office bag" query, reject items with unrelated modifiers like "lunch bag", "school bag"
    # But ALLOW related modifiers like "laptop bag", "business bag"
    if intent.is_product_type_query:
        conflict = _find_conflicting_product_type(intent, title, max_dist)
        if conflict:
            # Found an UNRELATED product type - NOT supported
            logger.debug(f"[CONFLICT] Query '{intent.raw_query}' | Unrelated: '{conflict}' | Title: '{title[:50]}...'")
            return IntentMatchResult(
                is_supported=False,
                matched_tokens=0,
                total_tokens=intent.token_count,
                match_ratio=0.0,
                match_type='conflict',
                match_location='none',
                tier=99,
                score=0,
            )
    
    # --- COMPUTE TOKEN MATCHES ---
    title_matched, title_positions = _compute_token_matches(intent.tokens, title_tokens, max_dist)
    cat_matched, cat_positions = _compute_token_matches(intent.tokens, category_tokens, max_dist)
    
    # Combined match: check if ALL tokens are covered by title OR category
    # For each query token, check if it matches title or category
    all_tokens_covered = True
    for qt in intent.tokens:
        in_title = _token_matches_any_in_text(qt, title, max_dist)
        in_category = _token_matches_any_in_text(qt, category, max_dist)
        if not in_title and not in_category:
            all_tokens_covered = False
            break
    
    # --- PHRASE QUERY HANDLING (2+ tokens) ---
    if intent.is_phrase_query:
        # For product type queries, use semantic matching
        if intent.is_product_type_query:
            # Check if there's a valid (related or exact) modifier before head
            has_valid_mod, is_exact_mod, found_mod = _query_modifier_precedes_head_in_title(intent, title, max_dist)
            
            if has_valid_mod:
                if is_exact_mod:
                    # Exact modifier match (e.g., "office bag" -> "Office Bag")
                    if _tokens_are_adjacent_in_text(intent.tokens, title, max_dist):
                        return IntentMatchResult(
                            is_supported=True,
                            matched_tokens=intent.token_count,
                            total_tokens=intent.token_count,
                            match_ratio=1.0,
                            match_type='exact_phrase',
                            match_location='title',
                            tier=1,
                            score=95_000 + title_matched * 1000,
                        )
                    else:
                        # Exact modifier but not adjacent
                        return IntentMatchResult(
                            is_supported=True,
                            matched_tokens=intent.token_count,
                            total_tokens=intent.token_count,
                            match_ratio=1.0,
                            match_type='exact_modifier_scattered',
                            match_location='title',
                            tier=2,
                            score=85_000 + title_matched * 1000,
                        )
                else:
                    # Related modifier match (e.g., "office bag" -> "Laptop Bag")
                    return IntentMatchResult(
                        is_supported=True,
                        matched_tokens=intent.token_count,
                        total_tokens=intent.token_count,
                        match_ratio=0.9,  # Slightly lower ratio for related
                        match_type=f'related_modifier:{found_mod}',
                        match_location='title',
                        tier=3,
                        score=75_000 + title_matched * 500,
                    )
            
            # No valid modifier found before head, check if tokens are scattered
            if all_tokens_covered:
                return IntentMatchResult(
                    is_supported=True,
                    matched_tokens=intent.token_count,
                    total_tokens=intent.token_count,
                    match_ratio=1.0,
                    match_type='phrase_scattered_no_modifier',
                    match_location='title',
                    tier=10,
                    score=40_000 + title_matched * 100,
                )
        
        # Non product-type phrase queries (standard handling)
        # STRICT MODE: All tokens must be covered
        if not all_tokens_covered:
            # Calculate partial match ratio for scoring
            combined_matched = 0
            for qt in intent.tokens:
                if _token_matches_any_in_text(qt, title, max_dist) or _token_matches_any_in_text(qt, category, max_dist):
                    combined_matched += 1
            
            ratio = combined_matched / intent.token_count if intent.token_count > 0 else 0.0
            
            return IntentMatchResult(
                is_supported=False,  # NOT supported - missing tokens
                matched_tokens=combined_matched,
                total_tokens=intent.token_count,
                match_ratio=ratio,
                match_type='partial',
                match_location='partial',
                tier=99,  # Demote heavily
                score=int(ratio * 1000),
            )
        
        # All tokens covered - determine quality of match
        # Check for adjacent phrase match in title (best case)
        if _tokens_are_adjacent_in_text(intent.tokens, title, max_dist):
            return IntentMatchResult(
                is_supported=True,
                matched_tokens=intent.token_count,
                total_tokens=intent.token_count,
                match_ratio=1.0,
                match_type='phrase_adjacent',
                match_location='title',
                tier=1,
                score=90_000 + title_matched * 1000,
            )
        
        # Check for adjacent phrase match in category
        if _tokens_are_adjacent_in_text(intent.tokens, category, max_dist):
            return IntentMatchResult(
                is_supported=True,
                matched_tokens=intent.token_count,
                total_tokens=intent.token_count,
                match_ratio=1.0,
                match_type='phrase_adjacent',
                match_location='category',
                tier=2,
                score=80_000 + cat_matched * 1000,
            )
        
        # All tokens present in title but scattered (not adjacent)
        if title_matched == intent.token_count:
            return IntentMatchResult(
                is_supported=True,
                matched_tokens=title_matched,
                total_tokens=intent.token_count,
                match_ratio=1.0,
                match_type='phrase_scattered',
                match_location='title',
                tier=3,
                score=70_000 + title_matched * 1000,
            )
        
        # Tokens split between title and category
        return IntentMatchResult(
            is_supported=True,
            matched_tokens=intent.token_count,  # We know all are covered
            total_tokens=intent.token_count,
            match_ratio=1.0,
            match_type='phrase_scattered',
            match_location='both',
            tier=4,
            score=60_000 + (title_matched + cat_matched) * 500,
        )
    
    # --- SINGLE TOKEN QUERY ---
    token = intent.tokens[0]
    
    if _token_matches_any_in_text(token, title, max_dist):
        return IntentMatchResult(
            is_supported=True,
            matched_tokens=1,
            total_tokens=1,
            match_ratio=1.0,
            match_type='single_token',
            match_location='title',
            tier=5,
            score=50_000,
        )
    
    if _token_matches_any_in_text(token, category, max_dist):
        return IntentMatchResult(
            is_supported=True,
            matched_tokens=1,
            total_tokens=1,
            match_ratio=1.0,
            match_type='single_token',
            match_location='category',
            tier=6,
            score=40_000,
        )
    
    # No match at all
    return IntentMatchResult(
        is_supported=False,
        matched_tokens=0,
        total_tokens=intent.token_count,
        match_ratio=0.0,
        match_type='none',
        match_location='none',
        tier=99,
        score=0,
    )


@dataclass(frozen=True)
class _CandidateMeta:
    """Metadata for a candidate item."""
    title: str
    sku: str
    category: str


# --------------------------------
# LEGACY COMPATIBILITY
# --------------------------------
def _match_tier(raw_query: str, meta: _CandidateMeta) -> Tuple[int, int]:
    """
    Legacy function for backward compatibility.
    Now uses the new intent-aware matching.
    Note: Without LLM result, related_terms will be empty.
    """
    intent = QueryIntent.from_query(raw_query, llm_result=None)
    result = _match_intent_against_item(
        intent,
        meta.title,
        meta.category,
        meta.sku,
    )
    return (result.tier, result.score)


# ================================
# SHOP/SELLER MATCHING (Product-First Approach)
# ================================
# Shops are ALWAYS derived from products - never searched independently
# This ensures:
# 1. Products are always the primary result
# 2. Shops only appear when they have matching products
# 3. Shop ranking is based on product relevance

@dataclass
class ShopMatchResult:
    """Result of shop name matching against seller_info."""
    seller_id: str
    seller_name: str
    user_name: Optional[str]
    match_type: str  # 'exact', 'starts_with', 'contains', 'fuzzy'
    match_score: float  # 0.0 - 1.0
    seller_info: Dict[str, Any]


def _normalize_shop_name(name: str) -> str:
    """Normalize shop name for comparison."""
    if not name:
        return ""
    normalized = name.lower().strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein edit distance between two strings."""
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    
    prev_row = list(range(len(s2) + 1))
    
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    
    return prev_row[-1]


def _fuzzy_match_score(query: str, target: str) -> float:
    """
    Calculate fuzzy match score between query and target shop name.
    Returns 0.0 - 1.0 (higher is better match).
    
    Key principle: Query must match a SIGNIFICANT portion of the shop name.
    Single common words like "shoes" should NOT match "SHOES WALA".
    """
    if not query or not target:
        return 0.0
    
    query_norm = _normalize_shop_name(query)
    target_norm = _normalize_shop_name(target)
    
    if not query_norm or not target_norm:
        return 0.0
    
    # Calculate coverage: how much of the target does the query cover?
    coverage = len(query_norm) / len(target_norm)
    
    # Exact match - full score
    if query_norm == target_norm:
        return 1.0
    
    # For partial matches, require significant coverage (at least 60%)
    # This prevents "shoes" from matching "SHOES WALA"
    MIN_COVERAGE_FOR_PARTIAL = 0.6
    
    # Starts with - only high score if query covers most of target
    if target_norm.startswith(query_norm):
        if coverage >= MIN_COVERAGE_FOR_PARTIAL:
            return 0.90 + (coverage * 0.05)  # 0.90 - 0.95 based on coverage
        else:
            # Low score for partial prefix match
            return coverage * 0.4  # e.g., "shoes" (5 chars) / "shoes wala" (10 chars) = 0.5 * 0.4 = 0.2
    
    # Query starts with target (target is shorter) - target must cover query
    if query_norm.startswith(target_norm):
        reverse_coverage = len(target_norm) / len(query_norm)
        if reverse_coverage >= MIN_COVERAGE_FOR_PARTIAL:
            return 0.85 + (reverse_coverage * 0.05)
        else:
            return reverse_coverage * 0.3
    
    # Contains - query is a substring of target
    if query_norm in target_norm:
        if coverage >= MIN_COVERAGE_FOR_PARTIAL:
            return 0.80 + (coverage * 0.05)
        else:
            return coverage * 0.3
    
    # Target contained in query
    if target_norm in query_norm:
        reverse_coverage = len(target_norm) / len(query_norm)
        if reverse_coverage >= MIN_COVERAGE_FOR_PARTIAL:
            return 0.75 + (reverse_coverage * 0.05)
        else:
            return reverse_coverage * 0.3
    
    # Check if all query words are in target (word-level match)
    query_words = query_norm.split()
    target_words = target_norm.split()
    
    # Word coverage: how many target words does query cover?
    word_coverage = len(query_words) / len(target_words) if target_words else 0
    
    if all(any(qw in tw or tw in qw for tw in target_words) for qw in query_words):
        if word_coverage >= MIN_COVERAGE_FOR_PARTIAL:
            return 0.70 + (word_coverage * 0.10)
        else:
            return word_coverage * 0.3
    
    # Levenshtein distance based score - only for similar length strings
    max_len = max(len(query_norm), len(target_norm))
    min_len = min(len(query_norm), len(target_norm))
    
    # Don't use fuzzy matching if lengths are too different
    length_ratio = min_len / max_len
    if length_ratio < 0.5:
        return 0.0
    
    distance = _levenshtein_distance(query_norm, target_norm)
    
    # Allow more distance for longer strings
    max_allowed_distance = max(2, int(max_len * 0.25))
    
    if distance <= max_allowed_distance:
        score = 1.0 - (distance / max_len)
        # Scale by length ratio to penalize very different lengths
        return max(0.5, min(0.70, score * length_ratio))
    
    # Word-level fuzzy matching
    matched_words = 0
    for qw in query_words:
        for tw in target_words:
            word_dist = _levenshtein_distance(qw, tw)
            max_word_len = max(len(qw), len(tw))
            if word_dist <= max(1, int(max_word_len * 0.25)):
                matched_words += 1
                break
    
    if matched_words > 0:
        word_match_ratio = matched_words / max(len(query_words), len(target_words))
        if word_match_ratio >= MIN_COVERAGE_FOR_PARTIAL:
            return 0.5 + (word_match_ratio * 0.2)
    
    return 0.0


def _match_seller_name(query: str, seller_info: Dict[str, Any]) -> Tuple[float, str]:
    """
    Match query against seller name/userName.
    Returns (score, match_type).
    """
    if not seller_info:
        return 0.0, "none"
    
    seller_name = seller_info.get("name", "") or ""
    user_name = seller_info.get("userName", "") or ""
    
    name_score = _fuzzy_match_score(query, seller_name)
    username_score = _fuzzy_match_score(query, user_name)
    
    best_score = max(name_score, username_score)
    
    if best_score <= 0:
        return 0.0, "none"
    
    # Determine match type
    query_lower = query.lower().strip()
    name_lower = seller_name.lower().strip()
    
    if query_lower == name_lower:
        return best_score, "exact"
    elif name_lower.startswith(query_lower):
        return best_score, "starts_with"
    elif query_lower in name_lower:
        return best_score, "contains"
    else:
        return best_score, "fuzzy"


def _find_matching_shop_from_products(
    db: Session,
    query: str,
    min_score: float = 0.7
) -> Optional[Tuple[str, str, float, str, Dict[str, Any]]]:
    """
    Find the best matching shop by scanning seller_info from products.
    
    Returns: (seller_id, seller_name, score, match_type, seller_info) or None
    
    This is used for shop-intent queries to identify which shop to filter by.
    """
    if not query or not query.strip():
        return None
    
    try:
        # Get items with seller_info (limited for performance)
        items = db.query(ItemModel.seller_info).filter(
            ItemModel.seller_info.isnot(None)
        ).all()
        
        # Track unique sellers and their best match
        seen_sellers: Dict[str, Tuple[float, str, Dict[str, Any]]] = {}
        
        for (seller_info,) in items:
            if not seller_info or not isinstance(seller_info, dict):
                continue
            
            seller_id = seller_info.get("id")
            if not seller_id:
                continue
            
            seller_id_str = str(seller_id).strip()
            if not seller_id_str:
                continue
            
            # Already processed this seller?
            if seller_id_str in seen_sellers:
                continue
            
            score, match_type = _match_seller_name(query, seller_info)
            
            if score >= min_score:
                seen_sellers[seller_id_str] = (score, match_type, seller_info)
        
        if not seen_sellers:
            return None
        
        # Get best match
        best_seller_id = max(seen_sellers.keys(), key=lambda k: seen_sellers[k][0])
        best_score, best_match_type, best_info = seen_sellers[best_seller_id]
        
        return (
            best_seller_id,
            best_info.get("name", ""),
            best_score,
            best_match_type,
            best_info,
        )
        
    except Exception as e:
        logger.error(f"[SHOP_MATCH] Error finding shop: {e}")
        return None


def _derive_shops_from_products(
    product_ids: List[str],
    seller_by_item_id: Dict[str, Dict[str, Any]],
    prioritized_seller_id: Optional[str] = None
) -> List[ShopSummary]:
    """
    Derive shop summaries from matched products.
    
    This is the ONLY way shops appear in results - derived from products.
    Shops are ranked by:
    1. Prioritized seller (if shop-intent query)
    2. Number of matching products
    
    Args:
        product_ids: List of product IDs from search results
        seller_by_item_id: Mapping of item_id -> seller_info
        prioritized_seller_id: If set, this shop appears first (for shop queries)
    """
    if not product_ids:
        return []
    
    # Count products per shop and track first item
    shop_counts: Dict[str, int] = {}
    first_item_for_shop: Dict[str, str] = {}
    shop_info: Dict[str, Dict[str, Any]] = {}
    
    for item_id in product_ids:
        seller = seller_by_item_id.get(str(item_id)) or {}
        seller_id = seller.get("id")
        if not seller_id:
            continue
        
        seller_id_str = str(seller_id).strip()
        if not seller_id_str:
            continue
        
        shop_counts[seller_id_str] = shop_counts.get(seller_id_str, 0) + 1
        
        if seller_id_str not in first_item_for_shop:
            first_item_for_shop[seller_id_str] = str(item_id)
        
        if seller_id_str not in shop_info:
            shop_info[seller_id_str] = seller
    
    if not shop_counts:
        return []
    
    # Sort shops: prioritized first, then by product count
    def shop_sort_key(sid: str) -> Tuple[int, int]:
        is_prioritized = 0 if sid == prioritized_seller_id else 1
        product_count = -shop_counts.get(sid, 0)  # Negative for descending
        return (is_prioritized, product_count)
    
    sorted_seller_ids = sorted(shop_counts.keys(), key=shop_sort_key)
    
    # Build shop summaries
    shops: List[ShopSummary] = []
    for seller_id_str in sorted_seller_ids:
        seller = shop_info.get(seller_id_str, {})
        shops.append(ShopSummary(
            seller_id=seller_id_str,
            name=seller.get("name"),
            user_name=seller.get("userName"),
            city=seller.get("city"),
            zone=seller.get("zone"),
            province=seller.get("province"),
            status=seller.get("status"),
            business_type=seller.get("businessType"),
            first_item_id=first_item_for_shop.get(seller_id_str),
            matched_items_count=shop_counts.get(seller_id_str, 0),
        ))
    
    return shops

def _ensure_prioritized_shop_in_list(
    shops: List[ShopSummary],
    prioritized_seller_id: Optional[str],
    matched_shop_info: Optional[Tuple[str, str, float, str, Dict[str, Any]]],
    seller_by_item_id: Dict[str, Dict[str, Any]],
) -> List[ShopSummary]:
    """
    Ensure detected shop is always shown at rank #1 in shops list.
    Does NOT affect product ranking.
    """
    if not prioritized_seller_id or not matched_shop_info:
        return shops

    # If shop already exists → move to top
    for i, shop in enumerate(shops):
        if shop.seller_id == prioritized_seller_id:
            if i == 0:
                return shops
            return [shop] + shops[:i] + shops[i + 1 :]

    # Inject shop if missing
    seller_id, _, _, _, seller_info = matched_shop_info

    first_item_id = None
    matched_items_count = 0
    for item_id, info in seller_by_item_id.items():
        if str((info or {}).get("id", "")) == str(prioritized_seller_id):
            matched_items_count += 1
            if first_item_id is None:
                first_item_id = item_id

    injected_shop = ShopSummary(
        seller_id=str(seller_id),
        name=seller_info.get("name"),
        user_name=seller_info.get("userName"),
        city=seller_info.get("city"),
        zone=seller_info.get("zone"),
        province=seller_info.get("province"),
        status=seller_info.get("status"),
        business_type=seller_info.get("businessType"),
        first_item_id=first_item_id,
        matched_items_count=matched_items_count,
    )

    return [injected_shop] + shops


MAX_SEMANTIC_RESULTS = 400


def _chunked(seq: List[str], size: int = 1000):
    """Yield successive chunks from seq."""
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


# ================================
# SEMANTIC SEARCH ENDPOINT
# ================================
@app.post("/search/semantic/", response_model=SearchResponse)
async def semantic_search(request: SearchRequest, db: Session = Depends(get_db)):
    """
    Intent-aware semantic search with LLM-based query processing.
    Includes request tracing for performance debugging.
    """
    # Generate trace ID for request correlation
    trace_id = str(uuid.uuid4())[:8]
    request_start = time.time()
    
    if not ML_ENABLED or not ml_engine:
        logger.warning(f"[{trace_id}] ML not available. Returning empty result.")
        return SearchResponse(items=[], total=0, query=request.query or "", shops=[])

    try:
        limit = max(1, int(request.limit or 21))
        offset = max(0, int(request.offset or 0))

        raw_query = (request.query or "").strip()
        if not raw_query:
            return SearchResponse(
                items=[], total=0, query="",
                filters_applied={}, suggestions=[], facets={}, shops=[],
            )

        # ============================================================
        # LLM QUERY PROCESSING (Spell Correction + Intent)
        # ============================================================
        original_query = raw_query
        llm_result = None
        related_terms = []
        llm_time_ms = 0.0
        
        if LLM_QUERY_PROCESSOR_AVAILABLE and LLM_ENABLED:
            try:
                llm_start = time.time()
                llm_result = await process_query_async(raw_query)
                llm_time_ms = (time.time() - llm_start) * 1000
                
                if llm_result.corrected_query and llm_result.corrected_query.lower().strip() != raw_query.lower().strip():
                    raw_query = llm_result.corrected_query
                    logger.info(
                        f"[{trace_id}] LLM: {llm_time_ms:.0f}ms | '{original_query}'->'{raw_query}' | "
                        f"cache={'HIT' if llm_result.from_cache else 'MISS'}"
                    )
                elif llm_result.was_corrected:
                    raw_query = llm_result.corrected_query
                    logger.info(f"[{trace_id}] LLM: {llm_time_ms:.0f}ms | no correction")
                else:
                    logger.info(f"[{trace_id}] LLM: {llm_time_ms:.0f}ms | no correction")
                    
                related_terms = llm_result.related_terms or []
                    
            except Exception as e:
                logger.warning(f"[{trace_id}] LLM: FAILED - {e}")

        # ============================================================
        # SHOP NAME MATCHING
        # ============================================================
        prioritized_seller_id: Optional[str] = None
        matched_shop_info: Optional[Tuple[str, str, float, str, Dict[str, Any]]] = None
        
        matched_shop_info = _find_matching_shop_from_products(db, raw_query, min_score=0.80)
        if matched_shop_info:
            seller_id, seller_name, score, match_type, seller_info = matched_shop_info
            if score >= 0.85:
                prioritized_seller_id = seller_id
        
        # Parse query intent
        intent = QueryIntent.from_query(raw_query, llm_result)
        query_clean = ml_engine._normalize_phrase(raw_query)

        # ============================================================
        # FAISS SEMANTIC SEARCH
        # ============================================================
        faiss_start = time.time()
        candidate_pool = MAX_SEMANTIC_RESULTS
        candidate_ids = []
        
        for pool_size in [100, 250, MAX_SEMANTIC_RESULTS]:
            candidate_ids, _ = await ml_engine.semantic_search_async(
                query=query_clean, k=pool_size, offset=0, candidate_pool=pool_size,
            )
            if len(candidate_ids) >= limit * 2:
                break
        
        faiss_time_ms = (time.time() - faiss_start) * 1000
        logger.info(f"[{trace_id}] FAISS: {faiss_time_ms:.0f}ms, candidates={len(candidate_ids)}")
        
        # Expand with related terms if needed
        if related_terms and len(candidate_ids) < limit * 2:
            existing = set(candidate_ids)
            for related_term in related_terms[:3]:
                try:
                    related_ids, _ = await ml_engine.semantic_search_async(
                        query=related_term, k=50, offset=0, candidate_pool=50,
                    )
                    existing.update(related_ids)
                except Exception:
                    pass
            candidate_ids = list(existing)

        if not candidate_ids:
            total_time_ms = (time.time() - request_start) * 1000
            logger.info(f"[{trace_id}] TOTAL: {total_time_ms:.0f}ms | no candidates")
            return SearchResponse(
                items=[], total=0, query=query_clean,
                filters_applied={
                    "llm_corrected": llm_result.was_corrected if llm_result else False,
                    "original_query": original_query if llm_result and llm_result.was_corrected else None,
                },
                suggestions=related_terms[:5] if related_terms else [],
                facets={}, shops=[]
            )

        # ============================================================
        # DATABASE: Fetch metadata
        # ============================================================
        db_start = time.time()
        
        sku_col = None
        for name in ("sku", "product_sku", "sku_code"):
            if hasattr(ItemModel, name):
                sku_col = getattr(ItemModel, name)
                break

        category_col = getattr(ItemModel, "category", None) or getattr(ItemModel, "category_name", None)
        title_col = getattr(ItemModel, "title", None)

        select_cols = [ItemModel.id]
        col_indices = {"id": 0}
        idx = 1
        
        if title_col is not None:
            select_cols.append(title_col)
            col_indices["title"] = idx
            idx += 1
        if sku_col is not None:
            select_cols.append(sku_col)
            col_indices["sku"] = idx
            idx += 1
        if category_col is not None:
            select_cols.append(category_col)
            col_indices["category"] = idx
            idx += 1
        
        select_cols.append(ItemModel.seller_info)
        col_indices["seller_info"] = idx

        meta_by_id: Dict[str, _CandidateMeta] = {}
        seller_by_item_id: Dict[str, Dict[str, Any]] = {}

        for batch in _chunked(candidate_ids, 1000):
            rows = db.query(*select_cols).filter(ItemModel.id.in_(batch)).all()
            for row in rows:
                rid = str(row[col_indices["id"]])
                title_val = str(row[col_indices["title"]] or "") if "title" in col_indices else ""
                sku_val = str(row[col_indices["sku"]] or "") if "sku" in col_indices else ""
                cat_val = str(row[col_indices["category"]] or "") if "category" in col_indices else ""

                meta_by_id[rid] = _CandidateMeta(title=title_val, sku=sku_val, category=cat_val)
                seller_info = row[col_indices["seller_info"]]
                seller_by_item_id[rid] = seller_info if isinstance(seller_info, dict) else {}
        
        db_meta_time_ms = (time.time() - db_start) * 1000
        logger.info(f"[{trace_id}] DB: {db_meta_time_ms:.0f}ms, fetched={len(meta_by_id)}")
                
        # ============================================================
        # INTENT MATCHING & RANKING
        # ============================================================
        rank_start = time.time()
        faiss_rank: Dict[str, int] = {cid: i for i, cid in enumerate(candidate_ids)}
        
        ranked: List[Tuple[str, IntentMatchResult, int]] = []
        supported_count = 0
        conflict_count = 0
        
        for cid in candidate_ids:
            meta = meta_by_id.get(cid, _CandidateMeta(title="", sku="", category=""))
            match_result = _match_intent_against_item(intent, meta.title, meta.category, meta.sku)
            ranked.append((cid, match_result, faiss_rank.get(cid, 10**9)))
            if match_result.is_supported:
                supported_count += 1
            if match_result.match_type == 'conflict':
                conflict_count += 1

        # Filter based on intent support
        if intent.is_phrase_query and INTENT_MATCH_STRICT:
            filtered_ranked = [
                (cid, result, fr) for cid, result, fr in ranked 
                if result.is_supported or result.match_ratio >= INTENT_MIN_TOKEN_MATCH_RATIO
            ]
            if not filtered_ranked and INTENT_FALLBACK_TO_FAISS:
                filtered_ranked = [
                    (cid, result, fr) for cid, result, fr in ranked
                    if result.tier <= INTENT_MAX_TIER_THRESHOLD
                ]
            ranked = filtered_ranked if filtered_ranked else []
        else:
            ranked = [(cid, result, fr) for cid, result, fr in ranked if result.tier <= INTENT_MAX_TIER_THRESHOLD]

        ranked.sort(key=lambda x: (x[1].tier, -x[1].score, x[2]))
        candidate_ids = [r[0] for r in ranked]
        
        rank_time_ms = (time.time() - rank_start) * 1000

        # Handle empty results after filtering
        total_matches = len(candidate_ids)
        if total_matches == 0:
            # Check for shop-only fallback
            if prioritized_seller_id and matched_shop_info:
                seller_id, _, _, _, seller_info = matched_shop_info
                seller_id_str = str(seller_id)

                total_inventory = (
                    db.query(func.count(ItemModel.id))
                    .filter(
                        ItemModel.seller_info.isnot(None),
                        ItemModel.seller_info["id"].astext == seller_id_str,
                    )
                    .scalar() or 0
                )

                injected_shop = ShopSummary(
                    seller_id=seller_id_str,
                    name=seller_info.get("name"),
                    user_name=seller_info.get("userName"),
                    city=seller_info.get("city"),
                    zone=seller_info.get("zone"),
                    province=seller_info.get("province"),
                    status=seller_info.get("status"),
                    business_type=seller_info.get("businessType"),
                    first_item_id=None,
                    matched_items_count=int(total_inventory)
                )

                total_time_ms = (time.time() - request_start) * 1000
                logger.info(f"[{trace_id}] TOTAL: {total_time_ms:.0f}ms | shop_fallback")

                return SearchResponse(
                    items=[],
                    total=0,
                    query=query_clean,
                    filters_applied={
                        "search_type": "shop_search",
                        "prioritized_shop": True,
                        "prioritized_seller_id": seller_id_str,
                        "shop_inventory_count": int(total_inventory),
                    },
                    suggestions=related_terms[:5] if related_terms else [],
                    facets={},
                    shops=[injected_shop],
                )

            total_time_ms = (time.time() - request_start) * 1000
            logger.info(f"[{trace_id}] TOTAL: {total_time_ms:.0f}ms | no results after filtering")
            return SearchResponse(
                items=[], total=0, query=query_clean,
                filters_applied={"intent_tokens": intent.tokens},
                suggestions=[], facets={}, shops=[]
            )

        # ============================================================
        # DATABASE: Fetch full items for page
        # ============================================================
        page_ids = candidate_ids[offset : offset + limit]
        
        db_items_start = time.time()
        page_items = db.query(ItemModel).filter(ItemModel.id.in_(page_ids)).all()
        items_by_id = {str(item.id): item for item in page_items}
        ordered_models = [items_by_id[_id] for _id in page_ids if _id in items_by_id]
        ordered_items = [ItemSchema.model_validate(m) for m in ordered_models]
        db_items_time_ms = (time.time() - db_items_start) * 1000

        # ============================================================
        # DERIVE SHOPS FROM PRODUCTS
        # ============================================================
        shops = _derive_shops_from_products(
            product_ids=candidate_ids,
            seller_by_item_id=seller_by_item_id,
            prioritized_seller_id=prioritized_seller_id,
        )
        shops = _ensure_prioritized_shop_in_list(
            shops=shops,
            prioritized_seller_id=prioritized_seller_id,
            matched_shop_info=matched_shop_info,
            seller_by_item_id=seller_by_item_id,
        )

        # Build response metadata
        llm_info = {}
        if llm_result:
            if llm_result.was_corrected:
                llm_info = {
                    "llm_corrected": True,
                    "original_query": original_query,
                    "corrected_query": raw_query,
                    "correction_confidence": llm_result.confidence,
                    "processing_time_ms": llm_result.processing_time_ms,
                    "from_cache": llm_result.from_cache,
                }
            if llm_result.product_type:
                llm_info["detected_product_type"] = llm_result.product_type
            if llm_result.modifier:
                llm_info["detected_modifier"] = llm_result.modifier
            if llm_result.intent:
                llm_info["detected_intent"] = llm_result.intent
        
        shop_match_info = {}
        if matched_shop_info and prioritized_seller_id:
            seller_id, seller_name, score, match_type, _ = matched_shop_info
            shop_match_info = {
                "prioritized_shop": True,
                "prioritized_seller_id": seller_id,
                "prioritized_seller_name": seller_name,
                "shop_match_score": round(score, 3),
                "shop_match_type": match_type,
            }

        # ============================================================
        # FINAL TIMING LOG
        # ============================================================
        total_time_ms = (time.time() - request_start) * 1000
        logger.info(
            f"[{trace_id}] TOTAL: {total_time_ms:.0f}ms | "
            f"LLM={llm_time_ms:.0f}ms FAISS={faiss_time_ms:.0f}ms "
            f"DB={db_meta_time_ms + db_items_time_ms:.0f}ms | "
            f"results={len(ordered_items)}/{total_matches}"
        )

        return SearchResponse(
            items=ordered_items,
            total=total_matches,
            query=query_clean,
            filters_applied={
                "search_type": "product_search",
                "intent_supported": supported_count,
                "intent_tokens": intent.tokens,
                **llm_info,
                **shop_match_info,
            },
            suggestions=related_terms[:5] if related_terms else [],
            facets={},
            shops=shops,
        )

    except Exception as e:
        total_time_ms = (time.time() - request_start) * 1000
        logger.error(f"[{trace_id}] ERROR: {total_time_ms:.0f}ms | {e}")
        import traceback
        logger.error(traceback.format_exc())
        return SearchResponse(items=[], total=0, query=request.query or "", shops=[])
    
# ============================================================
# CLIENT RESPONSE TRANSFORMERS 
# ============================================================
# ============================================================
# RAW_DATA HELPERS 
# ============================================================

def _as_dict(val: Any) -> Dict[str, Any]:
    return val if isinstance(val, dict) else {}

def _as_list(val: Any) -> List[Any]:
    return val if isinstance(val, list) else []

def _mongo_number(val: Any) -> Optional[int]:
    """
    Converts BuyPASS/Mongo Extended JSON formats like:
      {"$numberLong": "1737972328369"} -> 1737972328369
    If val is already int/float/string-digit, converts to int.
    Returns None if not convertible.
    """
    if isinstance(val, dict):
        if "$numberLong" in val:
            try:
                return int(val["$numberLong"])
            except Exception:
                return None
        # {"$oid": "..."} is not numeric
        return None

    if isinstance(val, (int, float)):
        return int(val)

    if isinstance(val, str):
        s = val.strip()
        if s.isdigit():
            try:
                return int(s)
            except Exception:
                return None

    return None

def _pick(raw: Dict[str, Any], item_dict: Dict[str, Any], raw_key: str, item_key: Optional[str] = None, default: Any = None) -> Any:
    """
    Prefer raw_data[raw_key] if present and not None, else fallback to item_dict[item_key].
    """
    if raw_key in raw and raw.get(raw_key) is not None:
        return raw.get(raw_key)
    if item_key and item_key in item_dict and item_dict.get(item_key) is not None:
        return item_dict.get(item_key)
    return default

def _coerce_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val if val is not None else default)
    except Exception:
        return default

def _coerce_int(val: Any, default: int = 0) -> int:
    try:
        return int(val if val is not None else default)
    except Exception:
        return default

def _coerce_bool(val: Any, default: bool = False) -> bool:
    try:
        return bool(val) if val is not None else default
    except Exception:
        return default
    
# ============================================================
# HIGHLIGHT RESULT HELPER 
# ============================================================

def _build_highlight_result(item_dict: Dict[str, Any], query: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build Algolia-compatible _highlightResult for a product hit.
    Wraps matched query terms in <em> tags.
    """
    # Tokenize query into words
    query_tokens = [t.lower() for t in _WORD_RE.findall(query.lower())] if query else []
    
    def highlight_text(text: str) -> Tuple[str, str, bool, List[str]]:
        """
        Highlight query terms in text.
        Returns: (highlighted_value, match_level, fully_highlighted, matched_words)
        """
        if not text or not query_tokens:
            return text or "", "none", False, []
        
        text_str = str(text)
        text_lower = text_str.lower()
        matched_words: List[str] = []
        
        # Find which query tokens appear in the text
        for token in query_tokens:
            if token in text_lower:
                matched_words.append(token)
        
        if not matched_words:
            return text_str, "none", False, []
        
        # Highlight matched words (case-insensitive replacement)
        highlighted = text_str
        for word in matched_words:
            pattern = re.compile(re.escape(word), re.IGNORECASE)
            highlighted = pattern.sub(lambda m: f"<em>{m.group()}</em>", highlighted)
        
        # Determine match level
        text_tokens = [t.lower() for t in _WORD_RE.findall(text_lower)]
        all_text_matched = all(t in matched_words for t in text_tokens) if text_tokens else False
        
        if len(matched_words) == len(query_tokens):
            match_level = "full"
        elif matched_words:
            match_level = "partial"
        else:
            match_level = "none"
        
        fully_highlighted = match_level == "full" and all_text_matched
        
        return highlighted, match_level, fully_highlighted, matched_words
    
    def build_highlight_obj(value: Any) -> Dict[str, Any]:
        """Build a highlight object for a single value."""
        if value is None:
            value = ""
        str_val = str(value) if not isinstance(value, str) else value
        highlighted, match_level, fully_highlighted, matched_words = highlight_text(str_val)
        
        result: Dict[str, Any] = {
            "value": highlighted,
            "matchLevel": match_level,
            "matchedWords": matched_words
        }
        if match_level != "none":
            result["fullyHighlighted"] = fully_highlighted
        return result
    
    # Get values from raw_data or item_dict
    product_name = _pick(raw, item_dict, "productName", "title", default="")
    category_id = _pick(raw, item_dict, "categoryId", "category_id", default="")
    category_obj = _as_dict(raw.get("category")) or {}
    category_name = category_obj.get("name") or _pick(raw, item_dict, "categoryName", None, default="")
    category_path = _pick(raw, item_dict, "categoryPath", "category_path", default="")
    
    seller_raw = _as_dict(raw.get("seller")) or _as_dict(item_dict.get("seller_info")) or {}
    geoloc = _as_dict(raw.get("_geoloc")) or _as_dict(item_dict.get("geoloc")) or {}
    
    specifications = raw.get("specifications") or item_dict.get("specifications") or []
    variants = raw.get("variants") or item_dict.get("variants") or []
    
    product_desc = _pick(raw, item_dict, "productDescription", "description", default="")
    product_main_desc = _pick(raw, item_dict, "productMainDescription", "main_description", default="")
    product_status = _pick(raw, item_dict, "productStatus", None, 
                          default=(item_dict.get("product_status") or item_dict.get("status") or "active"))
    
    # Build _highlightResult structure
    highlight_result: Dict[str, Any] = {
        "productName": build_highlight_obj(product_name),
        "categoryId": build_highlight_obj(category_id),
        "category": {
            "name": build_highlight_obj(category_name)
        },
        "categoryPath": build_highlight_obj(category_path),
        "seller": {
            "name": build_highlight_obj(seller_raw.get("name", "")),
            "zone": build_highlight_obj(seller_raw.get("zone", "")),
            "city": build_highlight_obj(seller_raw.get("city", ""))
        },
        "_geoloc": {
            "lat": build_highlight_obj(str(geoloc.get("lat", ""))),
            "lng": build_highlight_obj(str(geoloc.get("lng", "")))
        },
        "specifications": [],
        "variants": [],
        "productDescription": build_highlight_obj(product_desc),
        "productMainDescription": build_highlight_obj(product_main_desc),
        "productStatus": build_highlight_obj(product_status),
    }
    
    # Build specifications highlights
    if isinstance(specifications, list):
        for spec in specifications:
            if isinstance(spec, dict):
                spec_value = spec.get("value", "")
                highlight_result["specifications"].append({
                    "value": build_highlight_obj(spec_value)
                })
    
    # Build variants highlights
    if isinstance(variants, list):
        for variant in variants:
            if isinstance(variant, dict):
                variant_value = variant.get("value", {})
                if isinstance(variant_value, dict):
                    variant_name = variant_value.get("name", "")
                else:
                    variant_name = str(variant_value)
                highlight_result["variants"].append({
                    "value": {
                        "name": build_highlight_obj(variant_name)
                    }
                })
    
    return highlight_result

# ============================================================
# TRANSFORMERS
# ============================================================

def transform_item_to_client_hit(item: Any, rank: int = 1, query: str = "") -> Dict[str, Any]:
    """
    Transform internal item to client product hit format.
    Now includes _highlightResult for Algolia compatibility.

    RULE:
      - productId MUST come from SQL items.id
      - all other fields come from items.raw_data if available,
        otherwise fallback to SQL fields
      - _highlightResult wraps matched query terms in <em> tags
    """
    if hasattr(item, "model_dump"):
        item_dict = item.model_dump()
    elif hasattr(item, "__dict__"):
        item_dict = vars(item)
    else:
        item_dict = dict(item) if item else {}

    item_id = str(item_dict.get("id", ""))
    raw = _as_dict(item_dict.get("raw_data"))

    # ----------------------------
    # Seller
    # ----------------------------
    seller_raw = _as_dict(raw.get("seller"))
    seller_info = seller_raw or _as_dict(item_dict.get("seller_info"))

    seller_obj = None
    if seller_info:
        seller_obj = {
            "id": str(seller_info.get("id", "")),
            "name": seller_info.get("name", "") or "",
            "userName": seller_info.get("userName", "") or "",
            "businessType": seller_info.get("businessType", "") or "",
            "zone": seller_info.get("zone", "") or "",
            "city": seller_info.get("city", "") or "",
            "province": seller_info.get("province", "") or "",
            "status": seller_info.get("status", "active") or "active",
        }

    # ----------------------------
    # Images
    # ----------------------------
    images = raw.get("productImages")
    if not isinstance(images, list):
        images = item_dict.get("images") or []
    main_image = raw.get("productImage") or item_dict.get("image") or (images[0] if images else None)

    # ----------------------------
    # Category
    # ----------------------------
    category_raw = _as_dict(raw.get("category"))

    cat_id = _pick(raw, item_dict, "categoryId", "category_id", default=None)
    cat_name = _pick(
        raw,
        item_dict,
        "categoryName",
        None,
        default=(item_dict.get("category") or item_dict.get("category_name"))
    )
    cat_path = _pick(raw, item_dict, "categoryPath", "category_path", default="") or ""

    category_obj = None
    if category_raw or cat_id or cat_name:
        category_obj = {
            "_id": (category_raw.get("_id") if category_raw else None) or cat_id,
            "name": (category_raw.get("name") if category_raw else None) or cat_name,
            "cateogryPath": (category_raw.get("cateogryPath") if category_raw else None) or (cat_path.split(",") if cat_path else []),
            "cateogryIdPath": (category_raw.get("cateogryIdPath") if category_raw else None) or _pick(raw, item_dict, "cateogryIdPath", "category_id_path", default=[]),
        }

    # ----------------------------
    # Filter data
    # ----------------------------
    filter_data = _as_dict(raw.get("filterData")) or {
        "routingType": "productdetail",
        "filterQuery": f"?productId={item_id}",
    }
    filter_data["routingType"] = filter_data.get("routingType") or "productdetail"
    filter_data["filterQuery"] = f"?productId={item_id}"

    # ----------------------------
    # Times
    # ----------------------------
    created_on_raw = raw.get("createdOn")
    last_updated_raw = raw.get("lastUpdatedTime")

    created_on = _mongo_number(created_on_raw)
    last_updated = _mongo_number(last_updated_raw)

    if created_on is None:
        created_on = _pick(raw, item_dict, "createdOn", "created_on", default=None)
    if last_updated is None:
        last_updated = _pick(raw, item_dict, "lastUpdatedTime", "last_updated_time", default=None)

    # ----------------------------
    # Build response
    # ----------------------------
    result = {
        "productId": item_id,
        "subProductId": _pick(raw, item_dict, "subProductId", "sub_product_id"),
        "historyId": _pick(raw, item_dict, "historyId", "history_id"),

        "productImage": main_image,
        "productImages": images,
        "productName": _pick(raw, item_dict, "productName", "title", default=""),
        "productPrice": _coerce_float(_pick(raw, item_dict, "productPrice", "price", default=0), default=0.0),
        "productSalePrice": _pick(raw, item_dict, "productSalePrice", "sale_price"),
        "filterData": filter_data,
        "categoryId": cat_id,
        "categoryName": cat_name,
        "category": category_obj,
        "categoryPath": cat_path,

        "weight": _pick(raw, item_dict, "weight", "weight"),
        "_geoloc": _pick(raw, item_dict, "_geoloc", "geoloc"),

        "specifications": _pick(raw, item_dict, "specifications", "specifications", default=[]),
        "stockCount": _coerce_int(_pick(raw, item_dict, "stockCount", "stock_count", default=0), default=0),
        "soldCount": _coerce_int(_pick(raw, item_dict, "soldCount", "sold_count", default=0), default=0),
        "viewCount": _coerce_int(_pick(raw, item_dict, "viewCount", "view_count", default=0), default=0),
        "variants": _pick(raw, item_dict, "variants", "variants", default=[]),

        "lastUpdatedTime": last_updated,
        "createdOn": created_on,

        "productFooter": _pick(raw, item_dict, "productFooter", "product_footer"),
        "productTotalReviews": _coerce_int(_pick(raw, item_dict, "productTotalReviews", "total_reviews", default=0), default=0),
        "productStatus": _pick(raw, item_dict, "productStatus", None, default=(item_dict.get("product_status") or item_dict.get("status") or "active")),
        "productCondition": _pick(raw, item_dict, "productCondition", "condition", default="New"),

        "productDescription": _pick(raw, item_dict, "productDescription", "description"),
        "productMainDescription": _pick(raw, item_dict, "productMainDescription", "main_description"),
        "productHeader": _pick(raw, item_dict, "productHeader", "product_header"),
        "productDiscount": _pick(raw, item_dict, "productDiscount", "discount"),
        "productDeliveryType": _pick(raw, item_dict, "productDeliveryType", "delivery_type"),

        "productRating": _coerce_float(_pick(raw, item_dict, "productRating", "rating", default=0), default=0.0),
        "totalSold": _coerce_int(_pick(raw, item_dict, "totalSold", "total_sold", default=0), default=0),

        "commission": _pick(raw, item_dict, "commission", "commission"),
        "warranty": _pick(raw, item_dict, "warranty", "warranty"),
        "containDangerousGoods": _coerce_bool(_pick(raw, item_dict, "containDangerousGoods", "contain_dangerous_goods", default=False), default=False),

        "seller": seller_obj,

        "rank": rank,
        "objectID": item_id,

        "isLike": _coerce_bool(_pick(raw, item_dict, "isLike", "is_like", default=False), default=False),
        
        # Add _highlightResult for Algolia compatibility
        "_highlightResult": _build_highlight_result(item_dict, query, raw),
    }
    
    return result


def build_client_product_response(
    items: List[Any],
    total: int,
    query: str,
    page: int,
    hits_per_page: int,
    query_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build Algolia-compatible product search response.
    Uses raw_data-first transformation with _highlightResult.
    """

    hits = [
        transform_item_to_client_hit(item, rank=idx + 1, query=query)  # Pass query for highlighting
        for idx, item in enumerate(items)
    ]

    nb_pages = (total // hits_per_page) + (1 if total % hits_per_page else 0)

    data: Dict[str, Any] = {
        "hits": hits,
        "nbHits": total,
        "query": query,
        "page": page,
        "nbPages": nb_pages,
        "hitsPerPage": hits_per_page,
    }
    
    if query_id:
        data["queryID"] = query_id

    return {
        "isSuccess": True,
        "data": data,
    }

def transform_shop_for_client(
    shop_info: Dict[str, Any],
    products: List[Any],
    identity: Optional[Any] = None,
) -> Dict[str, Any]:
    """Transform shop data with nested products for client."""
    seller_id = str(shop_info.get("seller_id") or shop_info.get("id", ""))

    mongo_object_id = getattr(identity, "mongo_id", None) if identity else None
    
    # Fix: Properly extract mongo_business_id from identity.id
    # identity.id is already a string (or None) from BusinessIdentity
    mongo_business_id = None
    if identity:
        identity_id = getattr(identity, "id", None)
        if identity_id is not None:
            # identity.id is already a string like "2547113617"
            try:
                mongo_business_id = int(identity_id)
            except (ValueError, TypeError):
                # If conversion fails, try to see if it's already usable
                if str(identity_id).strip().isdigit():
                    mongo_business_id = int(str(identity_id).strip())
                else:
                    mongo_business_id = None

    shop_products: List[Dict[str, Any]] = []
    for p in products[:10]:
        if hasattr(p, "model_dump"):
            p_dict = p.model_dump()
        elif hasattr(p, "__dict__"):
            p_dict = vars(p)
        else:
            p_dict = dict(p) if p else {}

        raw = _as_dict(p_dict.get("raw_data"))
        pid = str(p_dict.get("id", ""))

        p_images = raw.get("productImages")
        if not isinstance(p_images, list):
            p_images = p_dict.get("images") or []

        raw_seller = _as_dict(raw.get("seller"))
        fallback_seller = _as_dict(p_dict.get("seller_info"))
        seller_for_id = raw_seller or fallback_seller

        shop_products.append({
            "productId": pid,
            "productName": _pick(raw, p_dict, "productName", "title", default=""),
            "productImage": raw.get("productImage") or p_dict.get("image") or (p_images[0] if p_images else None),
            "productImages": p_images,
            "productPrice": _coerce_float(_pick(raw, p_dict, "productPrice", "price", default=0), default=0.0),
            "commission": {},  # <-- CHANGED: Always return empty object
            "sellerId": str(seller_for_id.get("id", "")),  
        })

    mongo_logo = getattr(identity, "shop_logo_url", None) if identity else None
    # CHANGED: Return empty string instead of None when image not available
    image_value = mongo_logo.strip() if isinstance(mongo_logo, str) and mongo_logo.strip() else ""

    return {
        "_id": mongo_object_id,         
        "id": mongo_business_id,  # Now properly converted to int        
        "sellerId": seller_id,           
        "name": shop_info.get("name", "") or "",
        "image": image_value,  # Now returns "" instead of None
        "storeName": shop_info.get("storeName") or shop_info.get("name", "") or "",
        "followerCount": _coerce_int(shop_info.get("followerCount", 0), default=0),
        "products": shop_products,
    }

def build_client_shop_response(
    shops: List[Dict[str, Any]],
    products_by_seller: Dict[str, List[Any]],
    identities_by_seller: Optional[Dict[str, Any]] = None,
    query_id: Optional[str] = None,  # NEW PARAMETER
) -> Dict[str, Any]:
    """Build client response for shop search."""
    identities_by_seller = identities_by_seller or {}

    shop_list: List[Dict[str, Any]] = []
    for shop in shops:
        seller_id = str(shop.get("seller_id") or shop.get("id", ""))
        shop_products = products_by_seller.get(seller_id, [])
        identity = identities_by_seller.get(seller_id)
        shop_list.append(transform_shop_for_client(shop, shop_products, identity))

    # Build response data with optional queryID
    data: Dict[str, Any] = {"shops": shop_list}
    if query_id:
        data["queryID"] = query_id

    return {"isSuccess": True, "data": data}

def find_all_matching_shops_for_client(db: Session, query: str, min_score: float = 0.60, limit: int = 10) -> List[Dict[str, Any]]:
    """Find all shops matching the query (still uses seller_info because shop list comes from SQL seller_info)."""
    if not query or not query.strip():
        return []

    query_lower = query.lower().strip()

    try:
        seller_rows = (
            db.query(ItemModel.seller_info)
            .filter(ItemModel.seller_info.isnot(None))
            .limit(2000)
            .all()
        )

        seen_sellers: Dict[str, Dict[str, Any]] = {}
        for (seller_info,) in seller_rows:
            if not seller_info or not seller_info.get("id"):
                continue
            seller_id = str(seller_info["id"])
            if seller_id not in seen_sellers:
                seen_sellers[seller_id] = seller_info

        matches: List[Dict[str, Any]] = []
        for seller_id, info in seen_sellers.items():
            name = (info.get("name") or "").lower()
            username = (info.get("userName") or "").lower()

            name_score = SequenceMatcher(None, query_lower, name).ratio()
            username_score = SequenceMatcher(None, query_lower, username).ratio()

            if query_lower in name or query_lower in username:
                best_score = max(name_score, username_score, 0.85)
            else:
                best_score = max(name_score, username_score)

            if best_score >= min_score:
                matches.append({
                    "seller_id": seller_id,
                    "id": seller_id,
                    "name": info.get("userName", "") or "",
                    "storeName": info.get("name", "") or "",
                    "userName": info.get("userName", "") or "",
                    "image": info.get("image"),
                    "city": info.get("city"),
                    "zone": info.get("zone"),
                    "province": info.get("province"),
                    "businessType": info.get("businessType"),
                    "status": info.get("status", "active") or "active",
                    "followerCount": 0,
                    "score": best_score,
                })

        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches[:limit]

    except Exception as e:
        logger.error(f"[CLIENT_SHOP_SEARCH] Error: {e}")
        return []


# ============================================================
# CLIENT SEARCH ENDPOINT 
# ============================================================

@app.get("/v1/rec-engine/search")
async def client_search(
    q: str = Query(..., description="Search query"),
    isShop: Optional[bool] = Query(False, description="Is user searching for shops"),
    isCategory: Optional[bool] = Query(False, description="Is user navigating from categories section"),
    lat: Optional[str] = Query(None, description="Logged-in user latitude"),
    lng: Optional[str] = Query(None, description="Logged-in user longitude"),
    page: int = Query(0, ge=0, description="Page number (default 0)"),
    hitsPerPage: int = Query(20, ge=1, le=100, description="Results per page (default 20)"),
    db: Session = Depends(get_db),
):
    """
    Client-facing search endpoint (Algolia-compatible).
    """

    try:
        raw_query = (q or "").strip()
        limit = hitsPerPage
        offset = page * hitsPerPage

        # ----------------------------
        # Empty query handling
        # ----------------------------
        if not raw_query:
            if isShop:
                return {"isSuccess": True, "data": {"shops": []}}

            return {
                "isSuccess": True,
                "data": {
                    "hits": [],
                    "nbHits": 0,
                    "query": "",
                    "page": page,
                    "nbPages": 0,
                    "hitsPerPage": limit,
                },
            }

        # ----------------------------
        # Shop search
        # ----------------------------
        if isShop:
            resp = await _client_handle_shop_search(
                db=db,
                query=raw_query,
                limit=limit,
                offset=offset,
            )

            if isinstance(resp, dict) and resp.get("isSuccess") is True:
                resp["message"] = "Shops with keyword fetched successfully"

            return resp

        # ----------------------------
        # Product search
        # ----------------------------
        resp = await _client_handle_product_search(
            db=db,
            query=raw_query,
            limit=limit,
            offset=offset,
            page=page,
        )

        if isinstance(resp, dict) and resp.get("isSuccess") is True:
            resp["message"] = "Products with keyword fetched successfully"

        return resp

    except Exception as e:
        logger.error(f"Client search failed: {e}", exc_info=True)

        if isShop:
            return {"isSuccess": False, "data": {"shops": []}, "error": str(e)}

        return {"isSuccess": False, "data": {"hits": []}, "error": str(e)}
    
async def _client_handle_product_search(db: Session, query: str, limit: int, offset: int, page: int) -> Dict[str, Any]:
    """Handle product search and return client format."""

    if not ML_ENABLED or not ml_engine:
        return {"isSuccess": True, "data": {"hits": [], "nbHits": 0, "query": query, "page": page, "nbPages": 0, "hitsPerPage": limit}}

    # LLM Query Processing
    llm_result = None
    related_terms: List[str] = []
    query_id: Optional[str] = None 

    if LLM_QUERY_PROCESSOR_AVAILABLE and LLM_ENABLED:
        try:
            llm_result = await process_query_async(query)
            if llm_result.corrected_query and llm_result.corrected_query.lower().strip() != query.lower().strip():
                query = llm_result.corrected_query
            elif llm_result.was_corrected:
                query = llm_result.corrected_query
            related_terms = llm_result.related_terms or []
            
            # NEW: Extract OpenAI request ID for queryID
            query_id = getattr(llm_result, 'openai_request_id', None)  
            
        except Exception as e:
            logger.warning(f"[LLM] Query processing failed: {e}")

    # Parse intent
    intent = QueryIntent.from_query(query, llm_result)
    query_clean = ml_engine._normalize_phrase(query)

    # Semantic search
    candidate_ids = []
    for pool_size in [100, 250, MAX_SEMANTIC_RESULTS]:
        candidate_ids, _ = await ml_engine.semantic_search_async(
            query=query_clean, 
            k=pool_size, 
            offset=0, 
            candidate_pool=pool_size
        )
        if len(candidate_ids) >= limit * 2:
            break

    # Expand with related terms if needed
    if related_terms and len(candidate_ids) < limit * 2:
        existing = set(candidate_ids)
        for term in related_terms[:3]:
            try:
                extra_ids, _ = await ml_engine.semantic_search_async(
                    query=term, 
                    k=50, 
                    offset=0, 
                    candidate_pool=50
                )
                existing.update(extra_ids)
            except Exception:
                pass
        candidate_ids = list(existing)

    if not candidate_ids:
        return {"isSuccess": True, "data": {"hits": [], "nbHits": 0, "query": query_clean, "page": page, "nbPages": 0, "hitsPerPage": limit}}

    # Fetch metadata
    sku_col = None
    for name in ("sku", "product_sku", "sku_code"):
        if hasattr(ItemModel, name):
            sku_col = getattr(ItemModel, name)
            break

    category_col = getattr(ItemModel, "category", None) or getattr(ItemModel, "category_name", None)
    title_col = getattr(ItemModel, "title", None)

    select_cols = [ItemModel.id]
    if title_col is not None:
        select_cols.append(title_col)
    if sku_col is not None:
        select_cols.append(sku_col)
    if category_col is not None:
        select_cols.append(category_col)

    meta_by_id: Dict[str, _CandidateMeta] = {}
    for batch in _chunked(candidate_ids, 1000):
        rows = db.query(*select_cols).filter(ItemModel.id.in_(batch)).all()
        for row in rows:
            rid = str(row[0])
            idx = 1
            title_val, sku_val, cat_val = "", "", ""
            if title_col is not None:
                title_val = str(row[idx] or "")
                idx += 1
            if sku_col is not None:
                sku_val = str(row[idx] or "")
                idx += 1
            if category_col is not None:
                cat_val = str(row[idx] or "")
            meta_by_id[rid] = _CandidateMeta(title=title_val, sku=sku_val, category=cat_val)

    # Apply intent-aware matching
    faiss_rank = {cid: i for i, cid in enumerate(candidate_ids)}
    ranked = []
    for cid in candidate_ids:
        meta = meta_by_id.get(cid, _CandidateMeta(title="", sku="", category=""))
        match_result = _match_intent_against_item(intent, meta.title, meta.category, meta.sku)
        ranked.append((cid, match_result, faiss_rank.get(cid, 10**9)))

    # Filter and sort
    if intent.is_phrase_query and INTENT_MATCH_STRICT:
        ranked = [(c, r, f) for c, r, f in ranked if r.is_supported or r.match_ratio >= INTENT_MIN_TOKEN_MATCH_RATIO]
        if not ranked and INTENT_FALLBACK_TO_FAISS:
            ranked = [(c, r, f) for c, r, f in ranked if r.tier <= INTENT_MAX_TIER_THRESHOLD]
    else:
        ranked = [(c, r, f) for c, r, f in ranked if r.tier <= INTENT_MAX_TIER_THRESHOLD]

    ranked.sort(key=lambda x: (x[1].tier, -x[1].score, x[2]))
    candidate_ids = [r[0] for r in ranked]

    total_matches = len(candidate_ids)
    page_ids = candidate_ids[offset : offset + limit]

    if not page_ids:
        return {
            "isSuccess": True,
            "data": {
                "hits": [],
                "nbHits": total_matches,
                "query": query_clean,
                "page": page,
                "nbPages": (total_matches // limit) + (1 if total_matches % limit else 0),
                "hitsPerPage": limit,
            },
        }

    # Fetch full items
    page_items = db.query(ItemModel).filter(ItemModel.id.in_(page_ids)).all()
    items_by_id = {str(item.id): item for item in page_items}
    ordered_items = [items_by_id[_id] for _id in page_ids if _id in items_by_id]

    # Transform to client format (raw_data-first)
    return build_client_product_response(
        items=ordered_items, 
        total=total_matches, 
        query=query_clean, 
        page=page, 
        hits_per_page=limit,
        query_id=query_id, 
    )

async def _client_handle_shop_search(db: Session, query: str, limit: int, offset: int) -> Dict[str, Any]:
    """
    Handle shop search and return client format with nested products.

    PRODUCT-FIRST APPROACH:
    1. Always search for products first using semantic search
    2. Derive shops from matching products
    3. If a shop name matches the query, prioritize that shop (inject if missing)

    Mongo identity mapping:
      - response "_id" -> Mongo ObjectId (business._id)
      - response "id"  -> Mongo business.id (numeric)
      - response "sellerId" -> seller_info.id (from SQL items.seller_info)
    """

    if not ML_ENABLED or not ml_engine:
        return {"isSuccess": True, "data": {"shops": []}}

    # ============================================================
    # LLM QUERY PROCESSING
    # ============================================================
    llm_result = None
    related_terms: List[str] = []
    query_id: Optional[str] = None  # NEW: Track OpenAI request ID

    if LLM_QUERY_PROCESSOR_AVAILABLE and LLM_ENABLED:
        try:
            llm_result = await process_query_async(query)
            if llm_result.corrected_query and llm_result.corrected_query.lower().strip() != query.lower().strip():
                query = llm_result.corrected_query
            elif llm_result.was_corrected:
                query = llm_result.corrected_query
            
            # NEW: Extract OpenAI request ID for queryID
            query_id = getattr(llm_result, 'openai_request_id', None)
            
        except Exception as e:
            logger.warning(f"[LLM] Query processing failed: {e}")

    # Parse intent
    intent = QueryIntent.from_query(query, llm_result)
    query_clean = ml_engine._normalize_phrase(query)

    # ============================================================
    # CHECK IF QUERY IS A SHOP NAME (for prioritization)
    # ============================================================
    prioritized_seller_id: Optional[str] = None
    matched_shop_info: Optional[Tuple[str, str, float, str, Dict[str, Any]]] = None

    matched_shop_info = _find_matching_shop_from_products(db, query, min_score=0.80)
    if matched_shop_info:
        seller_id, seller_name, score, match_type, seller_info = matched_shop_info
        if score >= 0.85:
            prioritized_seller_id = str(seller_id)
            logger.info(f"[SHOP_SEARCH] Detected shop name match: '{seller_name}' (score={score:.2f})")

    # ============================================================
    # SEMANTIC SEARCH FOR PRODUCTS (Always)
    # ============================================================
    candidate_ids = []
    for pool_size in [100, 250, MAX_SEMANTIC_RESULTS]:
        candidate_ids, _ = await ml_engine.semantic_search_async(
            query=query_clean,   
            k=pool_size, 
            offset=0, 
            candidate_pool=pool_size
        )
        if len(candidate_ids) >= limit * 2:
            break
        
    # Expand with related terms if needed
    if related_terms and len(candidate_ids) < 100:
        existing = set(candidate_ids)
        for term in related_terms[:3]:
            try:
                extra_ids, _ = await ml_engine.semantic_search_async(
                    query=term, 
                    k=50, 
                    offset=0, 
                    candidate_pool=50
                )
                existing.update(extra_ids)
            except Exception:
                pass
        candidate_ids = list(existing)

    # ============================================================
    # HANDLE CASE: No products found BUT shop name matched
    # ============================================================
    if not candidate_ids:
        if prioritized_seller_id and matched_shop_info:
            seller_id, _, _, _, seller_info = matched_shop_info
            seller_id_str = str(seller_id)

            shop_products = (
                db.query(ItemModel)
                .filter(
                    ItemModel.seller_info.isnot(None),
                    ItemModel.seller_info["id"].astext == seller_id_str,
                )
                .limit(10)
                .all()
            )

            shop_data = {
                "seller_id": seller_id_str,
                "id": seller_id_str,
                "name": seller_info.get("userName") or "",
                "storeName": seller_info.get("name", ""),
                "image": seller_info.get("image"),
                "followerCount": 0,
            }

            identities_by_seller = fetch_business_identities_by_seller_ids([seller_id_str])

            return build_client_shop_response(
                [shop_data],
                {seller_id_str: shop_products},
                identities_by_seller=identities_by_seller,
                query_id=query_id,  # UPDATED: Pass query_id
            )

        # UPDATED: Include queryID in empty response
        data: Dict[str, Any] = {"shops": []}
        if query_id:
            data["queryID"] = query_id
        return {"isSuccess": True, "data": data}

    # ============================================================
    # APPLY INTENT-AWARE MATCHING (same as semantic_search)
    # ============================================================
    sku_col = None
    for name in ("sku", "product_sku", "sku_code"):
        if hasattr(ItemModel, name):
            sku_col = getattr(ItemModel, name)
            break

    category_col = getattr(ItemModel, "category", None) or getattr(ItemModel, "category_name", None)
    title_col = getattr(ItemModel, "title", None)

    select_cols = [ItemModel.id]
    col_indices = {"id": 0}
    idx = 1
    
    if title_col is not None:
        select_cols.append(title_col)
        col_indices["title"] = idx
        idx += 1
    if sku_col is not None:
        select_cols.append(sku_col)
        col_indices["sku"] = idx
        idx += 1
    if category_col is not None:
        select_cols.append(category_col)
        col_indices["category"] = idx
        idx += 1
    
    select_cols.append(ItemModel.seller_info)
    col_indices["seller_info"] = idx

    meta_by_id: Dict[str, _CandidateMeta] = {}
    seller_by_item_id: Dict[str, Dict[str, Any]] = {}

    for batch in _chunked(candidate_ids, 1000):
        rows = db.query(*select_cols).filter(ItemModel.id.in_(batch)).all()
        for row in rows:
            rid = str(row[col_indices["id"]])
            title_val = str(row[col_indices["title"]] or "") if "title" in col_indices else ""
            sku_val = str(row[col_indices["sku"]] or "") if "sku" in col_indices else ""
            cat_val = str(row[col_indices["category"]] or "") if "category" in col_indices else ""

            meta_by_id[rid] = _CandidateMeta(title=title_val, sku=sku_val, category=cat_val)
            seller_info = row[col_indices["seller_info"]]
            seller_by_item_id[rid] = seller_info if isinstance(seller_info, dict) else {}

    faiss_rank = {cid: i for i, cid in enumerate(candidate_ids)}
    ranked: List[Tuple[str, IntentMatchResult, int]] = []
    for cid in candidate_ids:
        meta = meta_by_id.get(cid, _CandidateMeta(title="", sku="", category=""))
        match_result = _match_intent_against_item(intent, meta.title, meta.category, meta.sku)
        ranked.append((cid, match_result, faiss_rank.get(cid, 10**9)))

    if intent.is_phrase_query and INTENT_MATCH_STRICT:
        ranked = [(c, r, f) for c, r, f in ranked if r.is_supported or r.match_ratio >= INTENT_MIN_TOKEN_MATCH_RATIO]
        if not ranked and INTENT_FALLBACK_TO_FAISS:
            ranked = [(c, r, f) for c, r, f in ranked if r.tier <= INTENT_MAX_TIER_THRESHOLD]
    else:
        ranked = [(c, r, f) for c, r, f in ranked if r.tier <= INTENT_MAX_TIER_THRESHOLD]

    ranked.sort(key=lambda x: (x[1].tier, -x[1].score, x[2]))
    candidate_ids = [r[0] for r in ranked]

    # ============================================================
    # DERIVE SHOPS FROM MATCHING PRODUCTS
    # ============================================================

    seller_products_map: Dict[str, Dict[str, Any]] = {}
    for item_id in candidate_ids:
        seller = seller_by_item_id.get(str(item_id), {}) or {}
        seller_id = str(seller.get("id", "")).strip()
        if not seller_id:
            continue
        if seller_id not in seller_products_map:
            seller_products_map[seller_id] = {"info": seller, "item_ids": []}
        seller_products_map[seller_id]["item_ids"].append(item_id)

    # ============================================================
    # INJECT PRIORITIZED SHOP IF NOT IN RESULTS
    # ============================================================
    if prioritized_seller_id and prioritized_seller_id not in seller_products_map and matched_shop_info:
        _, _, _, _, seller_info = matched_shop_info

        shop_product_rows = (
            db.query(ItemModel.id)
            .filter(
                ItemModel.seller_info.isnot(None),
                ItemModel.seller_info["id"].astext == prioritized_seller_id,
            )
            .limit(10)
            .all()
        )

        shop_product_ids = [str(r[0]) for r in shop_product_rows]

        seller_products_map[prioritized_seller_id] = {"info": seller_info, "item_ids": shop_product_ids}

        for pid in shop_product_ids:
            seller_by_item_id[pid] = seller_info

        logger.info(
            f"[SHOP_SEARCH] Injected prioritized shop '{seller_info.get('name')}' with {len(shop_product_ids)} products"
        )

    # ============================================================
    # HANDLE EMPTY RESULTS AFTER FILTERING
    # ============================================================
    if not candidate_ids and not seller_products_map:
        if prioritized_seller_id and matched_shop_info:
            _, _, _, _, seller_info = matched_shop_info

            shop_products = (
                db.query(ItemModel)
                .filter(
                    ItemModel.seller_info.isnot(None),
                    ItemModel.seller_info["id"].astext == prioritized_seller_id,
                )
                .limit(10)
                .all()
            )

            shop_data = {
                "seller_id": prioritized_seller_id,
                "id": prioritized_seller_id,
                "name": seller_info.get("userName") or "",
                "storeName": seller_info.get("name", ""),
                "image": seller_info.get("image"),
                "followerCount": 0,
            }

            identities_by_seller = fetch_business_identities_by_seller_ids([prioritized_seller_id])

            return build_client_shop_response(
                [shop_data],
                {prioritized_seller_id: shop_products},
                identities_by_seller=identities_by_seller,
                query_id=query_id,  # UPDATED: Pass query_id
            )

        # UPDATED: Include queryID in empty response
        data: Dict[str, Any] = {"shops": []}
        if query_id:
            data["queryID"] = query_id
        return {"isSuccess": True, "data": data}

    # ============================================================
    # SORT SELLERS: prioritized first, then by product count
    # ============================================================
    def seller_sort_key(seller_id: str) -> Tuple[int, int]:
        is_prioritized = 0 if seller_id == prioritized_seller_id else 1
        product_count = -len(seller_products_map.get(seller_id, {}).get("item_ids", []))
        return (is_prioritized, product_count)

    sorted_seller_ids = sorted(seller_products_map.keys(), key=seller_sort_key)[:limit]

    shops: List[Dict[str, Any]] = []
    for seller_id in sorted_seller_ids:
        data = seller_products_map[seller_id]
        info = data.get("info") or {}
        shops.append(
            {
                "seller_id": seller_id,
                "id": seller_id,
                "name": info.get("userName") or "",
                "storeName": info.get("name", ""),
                "image": info.get("image"),
                "followerCount": 0,
            }
        )

    # Fetch actual product objects for each shop
    all_product_ids: List[str] = []
    for seller_id in sorted_seller_ids:
        all_product_ids.extend(seller_products_map[seller_id]["item_ids"][:10])

    products = db.query(ItemModel).filter(ItemModel.id.in_(all_product_ids)).all()
    products_dict = {str(p.id): p for p in products}

    final_products_by_seller: Dict[str, List[Any]] = {}
    for seller_id in sorted_seller_ids:
        final_products_by_seller[seller_id] = [
            products_dict[pid]
            for pid in seller_products_map[seller_id]["item_ids"][:10]
            if pid in products_dict
        ]

    logger.info(
        f"[SHOP_SEARCH] Query: '{query_clean}' | Products: {len(candidate_ids)} | "
        f"Shops derived: {len(shops)} | Prioritized: {prioritized_seller_id}"
    )

    identities_by_seller = fetch_business_identities_by_seller_ids(sorted_seller_ids)

    return build_client_shop_response(
        shops,
        final_products_by_seller,
        identities_by_seller=identities_by_seller,
        query_id=query_id,  
    )

# ================================
# FUZZY MATCHING CACHE ENDPOINTS
# ================================

@app.get("/cache/fuzzy-stats")
async def get_fuzzy_cache_statistics():
    """Get fuzzy matching cache statistics for monitoring."""
    return get_fuzzy_match_cache_stats()

@app.post("/cache/fuzzy-clear")
async def clear_fuzzy_caches():
    """Clear fuzzy matching caches to free memory."""
    clear_fuzzy_match_caches()
    return {"message": "Fuzzy matching caches cleared"}

# ================================
# EMBEDDING CACHE ENDPOINTS
# ================================

@app.get("/cache/embedding-stats")
async def get_embedding_cache_statistics():
    """
    Get embedding cache statistics.
    
    Shows:
    - Number of cached embeddings
    - Cache hit rate potential
    - Memory usage estimate
    - TTL configuration
    """
    if not ML_ENABLED or not ml_engine:
        return {"error": "ML engine not available"}
    
    return ml_engine.get_embedding_cache_stats()


@app.post("/cache/embedding-clear")
async def clear_embedding_cache():
    """
    Clear all cached embeddings from Redis.
    
    Use this when:
    - Changing embedding models
    - Testing cache behavior
    - Freeing up Redis memory
    """
    if not ML_ENABLED or not ml_engine:
        return {"error": "ML engine not available"}
    
    return ml_engine.clear_embedding_cache()

@app.post("/search/nlp-enhanced/", response_model=SearchResponse)
async def nlp_enhanced_search(request: SearchRequest, db: Session = Depends(get_db)):
    """NLP-enhanced search with intent recognition."""
    if not ML_ENABLED or not nlp_engine:
        return await enhanced_search(request, db)

    try:
        intent_data = nlp_engine.extract_intent(request.query or "")
        nlp_filters = nlp_engine.extract_filters_from_nlp(request.query or "")

        if "max_price" in nlp_filters and not request.max_price:
            request.max_price = nlp_filters["max_price"]
        if "min_price" in nlp_filters and not request.min_price:
            request.min_price = nlp_filters["min_price"]

        expanded_queries = nlp_engine.query_expansion(request.query or "")

        all_items = []
        for query in expanded_queries[:3]:
            request.query = query
            results = await search_engine.search(db, request)
            all_items.extend(results.items)

        seen = set()
        unique_items = []
        for item in all_items:
            if item.id not in seen:
                seen.add(item.id)
                unique_items.append(item)

        return SearchResponse(
            items=unique_items[: request.limit],
            total=len(unique_items),
            query=request.query or "",
            filters_applied={"intent": intent_data["intent"], **nlp_filters},
            suggestions=expanded_queries,
            facets={},
            shops=[],
        )
    except Exception as e:
        logger.error(f"NLP search failed: {e}")
        return await enhanced_search(request, db)


# ================================
# ENHANCED RECOMMENDATION ENDPOINTS
# ================================

@app.post("/recommend/enhanced/", response_model=RecommendResponse)
async def enhanced_recommendations(
    request: RecommendRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Enhanced recommendations with multiple algorithms."""
    try:
        results = await recommendation_engine.get_recommendations(db, request)

        background_tasks.add_task(
            log_recommendation,
            db,
            request.user_id,
            results.algorithm_used,
            [item.item.id for item in results.items],
        )

        return results
    except Exception as e:
        logger.error(f"Recommendations failed: {e}")
        raise HTTPException(status_code=500, detail=f"Recommendations failed: {str(e)}")


@app.post("/recommend/deep-learning/", response_model=RecommendResponse)
async def deep_learning_recommendations(request: RecommendRequest, db: Session = Depends(get_db)):
    """Deep learning based recommendations."""
    if not ML_ENABLED:
        return await enhanced_recommendations(request, BackgroundTasks(), db)

    try:
        dl_recommender = DeepLearningRecommender()

        candidate_items = (
            db.query(ItemModel)
            .filter(ItemModel.product_status == "active")
            .limit(100)
            .all()
        )

        item_ids: List[str] = [str(item.id) for item in candidate_items]
        predictions = dl_recommender.predict(request.user_id, item_ids)

        if not predictions:
            return await enhanced_recommendations(request, BackgroundTasks(), db)

        sorted_items = sorted(predictions.items(), key=lambda x: x[1], reverse=True)[: request.limit]

        recommendations = []
        for item_id, score in sorted_items:
            item = next((i for i in candidate_items if str(i.id) == str(item_id)), None)
            if item:
                recommendations.append(
                    RecommendationItem(
                        item=item,
                        score=score,
                        reason="AI predicted you'll like this",
                        algorithm_used="deep_learning",
                    )
                )

        return RecommendResponse(
            items=recommendations,
            user_id=request.user_id,
            algorithm_used="deep_learning",
            total=len(recommendations),
            context=request.context,
        )
    except Exception as e:
        logger.error(f"DL recommendations failed: {e}")
        return await enhanced_recommendations(request, BackgroundTasks(), db)


# ================================
# SIMILAR ITEMS ENDPOINTS
# ================================

@app.get("/ml/training-history/")
async def get_training_history(db: Session = Depends(get_db)):
    """Get ML model training history."""
    try:
        try:
            from .models import TrainingLog
        except ImportError:
            from models import TrainingLog

        history = db.query(TrainingLog).order_by(desc(TrainingLog.created_at)).limit(10).all()

        return [
            {
                "model": log.model_name,
                "timestamp": log.created_at.isoformat(),
                "status": log.status,
                "metrics": log.training_metrics or {},
            }
            for log in history
        ]
    except Exception:
        pass

    try:
        metadata_file = Path("ml_models/training_metadata.json")
        if metadata_file.exists():
            with open(metadata_file, "r") as f:
                metadata = json.load(f)

            return [
                {
                    "model": "all",
                    "timestamp": metadata.get("timestamp", datetime.now().isoformat()),
                    "status": metadata.get("status", "unknown"),
                    "metrics": metadata.get("data_stats", {}),
                }
            ]
    except Exception:
        pass

    return []


@app.get("/interactions/")
async def list_interactions(
    user_id: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List interactions with optional user filter."""
    query = db.query(Interaction)

    if user_id:
        query = query.filter(Interaction.user_id == user_id)

    interactions = query.order_by(desc(Interaction.created_at)).limit(limit).all()

    return [
        {
            "id": i.id,
            "user_id": i.user_id,
            "item_id": i.item_id,
            "interaction_type": i.interaction_type,
            "rating": i.rating,
            "created_at": i.created_at.isoformat() if getattr(i, "created_at", None) else None,
            "timestamp": i.created_at.isoformat() if getattr(i, "created_at", None) else None,
        }
        for i in interactions
    ]


@app.get("/analytics/stats/")
async def get_analytics_stats(db: Session = Depends(get_db)):
    """Get overall system statistics."""
    total_interactions = db.query(Interaction).count()
    avg_rating = db.query(func.avg(Interaction.rating)).scalar() or 0.0

    return {
        "total_interactions": total_interactions,
        "average_rating": float(avg_rating),
        "total_users": db.query(User).count(),
        "total_items": db.query(ItemModel).count(),
    }


@app.get("/analytics/user-activity/")
async def get_user_activity(db: Session = Depends(get_db)):
    """Get user activity statistics."""
    active_users = (
        db.query(
            User.id,
            User.username,
            func.count(Interaction.id).label("interaction_count"),
            func.max(Interaction.created_at).label("last_interaction"),
        )
        .join(Interaction, User.id == Interaction.user_id)
        .group_by(User.id, User.username)
        .order_by(desc("interaction_count"))
        .limit(10)
        .all()
    )

    return {
        "active_users": [
            {
                "user_id": user.id,
                "username": user.username,
                "interaction_count": user.interaction_count,
                "last_interaction": user.last_interaction.isoformat() if user.last_interaction else None,
            }
            for user in active_users
        ]
    }


@app.get("/analytics/category-performance/")
async def get_category_performance(db: Session = Depends(get_db)):
    """Get category performance statistics."""
    category_stats = (
        db.query(
            ItemModel.category,
            func.count(Interaction.id).label("interaction_count"),
            func.avg(Interaction.rating).label("avg_rating"),
        )
        .join(Interaction, ItemModel.id == Interaction.item_id)
        .group_by(ItemModel.category)
        .all()
    )

    return {
        "categories": [
            {
                "category": cat.category,
                "interaction_count": cat.interaction_count,
                "avg_rating": float(cat.avg_rating) if cat.avg_rating else 0.0,
            }
            for cat in category_stats
        ]
    }


@app.post("/cache/clear/")
async def clear_cache():
    """Clear the memory cache."""
    cache.clear()
    return {"message": "Cache cleared successfully"}


@app.get("/items/{item_id}/similar/enhanced/")
async def get_similar_items_enhanced(item_id: str, limit: int = 20, db: Session = Depends(get_db)):
    """Get similar items using advanced algorithms."""
    try:
        request = SimilarItemsRequest(item_id=item_id, algorithm="auto", limit=limit)
        results = await recommendation_engine.get_similar_items(db, request)
        return results
    except Exception as e:
        logger.error(f"Similar items failed: {e}")
        return get_similar_items(item_id, limit, db)


@app.get("/items/{item_id}/similar")
def get_similar_items(item_id: str, limit: int = 20, db: Session = Depends(get_db)):
    """Get items similar to the given item (basic version)."""
    target_item = db.query(ItemModel).filter(ItemModel.id == item_id).first()
    if not target_item:
        raise HTTPException(status_code=404, detail="Item not found")

    similar_items = []

    category_field = getattr(target_item, "category_name", target_item.category)
    category_items = (
        db.query(ItemModel)
        .filter(
            and_(
                ItemModel.category == category_field,
                ItemModel.id != item_id,
                ItemModel.in_stock == True,
            )
        )
        .limit(limit)
        .all()
    )

    similar_items.extend(category_items)

    return {
        "target_item": target_item,
        "similar_items": similar_items[:limit],
        "total_found": len(similar_items),
    }


# ================================
# ANALYTICS ENDPOINTS
# ================================

@app.get("/analytics/trending-items/")
async def get_trending_items(
    window_days: int = 7,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Get AI-predicted trending items."""
    if not ML_ENABLED or not predictive_engine:
        return get_popular(limit, db)

    try:
        cutoff_date = datetime.now() - timedelta(days=window_days)

        recent_interactions = (
            db.query(Interaction.item_id, func.count(Interaction.id).label("interaction_count"))
            .filter(Interaction.created_at >= cutoff_date)
            .group_by(Interaction.item_id)
            .all()
        )

        interaction_data = [(row.item_id, row.interaction_count) for row in recent_interactions]

        trending = predictive_engine.detect_trending_items(interaction_data, window_days)

        trending_items = []
        for trend in trending[:limit]:
            item = db.query(ItemModel).filter(ItemModel.id == trend["item_id"]).first()
            if item:
                trending_items.append(
                    {
                        "item": item,
                        "trend_score": trend["trend_score"],
                        "interaction_count": trend["interaction_count"],
                    }
                )

        return {"trending_items": trending_items}

    except Exception as e:
        logger.error(f"Trending analysis failed: {e}")
        return get_popular(limit, db)


@app.get("/popular/", response_model=List[ItemSchema])
def get_popular(limit: int = 20, db: Session = Depends(get_db)):
    """Get popular items."""
    items = (
        db.query(ItemModel)
        .filter(ItemModel.in_stock == True)
        .order_by(
            desc(ItemModel.popularity_score)
            if hasattr(ItemModel, "popularity_score")
            else desc(ItemModel.id)
        )
        .limit(limit)
        .all()
    )
    return items


# ================================
# USER ANALYTICS ENDPOINTS
# ================================

@app.get("/users/{user_id}/preferences")
def get_user_preferences(user_id: str, db: Session = Depends(get_db)):
    """Get user's category preferences and behavior insights."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    interactions = db.query(Interaction).filter(Interaction.user_id == user_id).all()

    if not interactions:
        return {"user_id": user_id, "total_interactions": 0, "favorite_categories": [], "average_rating": 0}

    categories = []
    ratings = []

    for interaction in interactions:
        if interaction.item:
            category = getattr(interaction.item, "category_name", interaction.item.category)
            if category:
                categories.append(category)
            if interaction.rating is not None:
                ratings.append(interaction.rating)

    category_counts = Counter(categories)
    avg_rating = sum(ratings) / len(ratings) if ratings else 0

    return {
        "user": {"id": user.id, "username": user.username, "email": user.email},
        "preferences": {
            "total_interactions": len(interactions),
            "favorite_categories": [{"category": cat, "count": count} for cat, count in category_counts.most_common(5)],
            "average_rating": round(avg_rating, 2),
        },
    }


# ================================
# ML MANAGEMENT ENDPOINTS
# ================================

@app.post("/ml/train-models/")
async def trigger_model_training(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Trigger ML model training."""
    
    # Check for existing training lock
    redis = get_redis_client()
    if redis:
        existing_lock = redis.get("training:lock")
        if existing_lock:
            return {
                "message": "Training already in progress",
                "log_id": existing_lock.decode() if isinstance(existing_lock, bytes) else str(existing_lock),
                "status": "already_running"
            }
    
    interaction_count = db.query(Interaction).count()
    user_count = db.query(User).count()
    item_count = db.query(ItemModel).count()

    log_id: Optional[str] = None
    try:
        from .models import TrainingLog
        # ... existing training log creation
    except ImportError:
        from models import TrainingLog
        # ...
    except Exception:
        pass

    # Set lock before starting task
    if redis and log_id:
        redis.setex("training:lock", 3600, log_id)  # 1 hour lock

    background_tasks.add_task(train_ml_models_task, log_id)

    return {
        "message": "Model training started in background",
        "current_stats": {"interactions": interaction_count, "users": user_count, "items": item_count},
        "log_id": log_id,
    }


@app.get("/ml/status/")
async def get_ml_status():
    """Get ML system status including embedding cache."""
    llm_info = {
        "enabled": LLM_QUERY_PROCESSOR_AVAILABLE and LLM_ENABLED,
        "available": LLM_QUERY_PROCESSOR_AVAILABLE,
    }
    
    if LLM_QUERY_PROCESSOR_AVAILABLE and LLM_ENABLED:
        try:
            cache_stats = get_llm_cache_stats()
            llm_info["cache"] = cache_stats
        except Exception:
            pass
    
    # Add embedding cache info
    embedding_cache_info = {}
    if ML_ENABLED and ml_engine:
        try:
            embedding_cache_info = ml_engine.get_embedding_cache_stats()
        except Exception:
            pass
    
    return {
        "ml_enabled": ML_ENABLED,
        "engines": {
            "ml_engine": ml_engine is not None,
            "nlp_engine": nlp_engine is not None,
            "image_engine": image_engine is not None,
            "predictive_engine": predictive_engine is not None,
        },
        "llm_query_processor": llm_info,
        "embedding_cache": embedding_cache_info,  # NEW
        "models_directory": os.path.exists("ml_models/"),
        "intent_search_config": {
            "strict_mode": INTENT_MATCH_STRICT,
            "min_token_match_ratio": INTENT_MIN_TOKEN_MATCH_RATIO,
            "fallback_to_faiss": INTENT_FALLBACK_TO_FAISS,
            "max_tier_threshold": INTENT_MAX_TIER_THRESHOLD,
        },
        "endpoints_available": [
            "/search/semantic/",
            "/search/shops/{seller_id}/items",
            "/search/nlp-enhanced/",
            "/recommend/deep-learning/",
            "/analytics/trending-items/",
            "/ml/train-models/",
            "/llm/process-query",
            "/cache/embedding-stats",  # NEW
            "/cache/embedding-clear",  # NEW
        ]
        if ML_ENABLED
        else ["/search/shops/{seller_id}/items"],
    }

# ================================
# HELPER FUNCTIONS
# ================================

def apply_nlp_filters_to_items(items: List[ItemSchema], nlp_filters: Dict[str, Any]) -> List[ItemSchema]:
    """Apply NLP-extracted filters as HARD constraints on the ranked item list."""
    if not items or not nlp_filters:
        return items

    def item_text(i: ItemSchema) -> str:
        return " ".join(
            [
                (i.title or "").lower(),
                (getattr(i, "category", None) or "").lower(),
                (getattr(i, "brand", None) or "").lower(),
            ]
        )

    filtered = items

    category = (nlp_filters.get("category") or "").strip().lower()
    if category:
        filtered = [i for i in filtered if category in (getattr(i, "category", "") or "").lower() or category in item_text(i)]

    style = (nlp_filters.get("style") or "").strip().lower()
    if style:
        filtered = [i for i in filtered if style in item_text(i)]

    include_terms = [t.lower() for t in nlp_filters.get("include_terms", []) if t]
    for term in include_terms:
        filtered = [i for i in filtered if term in item_text(i)]

    exclude_terms = [t.lower() for t in nlp_filters.get("exclude_terms", []) if t]
    if exclude_terms:
        tmp: List[ItemSchema] = []
        for i in filtered:
            text = item_text(i)
            if any(term in text for term in exclude_terms):
                continue
            tmp.append(i)
        filtered = tmp

    return filtered


async def log_recommendation(db: Session, user_id: str, algorithm: str, item_ids: List[str]):
    """Log recommendation for analytics."""
    try:
        logger.info(f"Recommendation logged: {user_id} - {algorithm} - {len(item_ids)} items")
    except Exception as e:
        logger.error(f"Failed to log recommendation: {e}")


async def train_ml_models_task(log_id: Optional[str] = None):
    """Background task for model training."""
    # Import SessionLocal directly to create a dedicated session
    # This avoids issues with request-scoped sessions in background tasks
    try:
        from .database import SessionLocal
    except ImportError:
        from database import SessionLocal
    
    db = SessionLocal()
    try:
        logger.info("Starting ML model training in background...")

        from run_training import train_ml_models
        success = await train_ml_models(db)

        if log_id:
            try:
                try:
                    from .models import TrainingLog
                except ImportError:
                    from models import TrainingLog

                log: Any = db.query(TrainingLog).filter(TrainingLog.id == log_id).first()
                if log:
                    log.end_time = datetime.now()
                    log.status = "completed" if success else "failed"

                    try:
                        with open("ml_models/training_metadata.json", "r") as f:
                            metadata = json.load(f)
                            log.training_metrics = metadata.get("data_stats", {})
                    except Exception as e:
                        logger.warning(f"Could not load training metadata: {e}")

                    db.commit()
            except Exception as e:
                logger.error(f"Error updating training log: {e}")

        logger.info(f"ML model training {'completed' if success else 'failed'}")

    except Exception as e:
        logger.error(f"Model training failed: {e}")

        if log_id:
            try:
                try:
                    from .models import TrainingLog
                except ImportError:
                    from models import TrainingLog

                log: Any = db.query(TrainingLog).filter(TrainingLog.id == log_id).first()
                if log:
                    log.end_time = datetime.now()
                    log.status = "failed"
                    log.error_message = str(e)
                    db.commit()
            except Exception as e2:
                logger.error(f"Error updating failed training log: {e2}")
    finally:
        # Release training lock
        try:
            redis = get_redis_client()
            if redis:
                redis.delete("training:lock")
                logger.info("Training lock released")
        except Exception as e:
            logger.warning(f"Failed to release training lock: {e}")
        
        db.close()

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Uvicorn server...")
    logger.info("Server will be available at http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")