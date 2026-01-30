"""
OpenAI-Based Query Processor for E-commerce Search

Uses SHARED Redis connection from memory_cache.py
TTL-based cache expiration via LLM_CACHE_TTL env variable
"""

from __future__ import annotations

import os
import json
import logging
import hashlib
import time
import threading
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from dataclasses import dataclass
from cachetools import TTLCache
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
from collections import deque
import statistics


# Metrics tracking (thread-safe with deque's atomic operations)
_latency_samples: deque = deque(maxlen=1000)  # Last 1000 OpenAI calls
_cache_hits: int = 0
_cache_misses: int = 0
_metrics_lock = threading.Lock()

logger = logging.getLogger(__name__)

# Lazy Prometheus metrics import to avoid circular imports
_prometheus_metrics_cache = {}

def _get_prometheus_metrics():
    if 'metrics' not in _prometheus_metrics_cache:
        m = None
        try:
            from observability.metrics import metrics
            m = metrics
        except ImportError:
            try:
                from .observability.metrics import metrics
                m = metrics
            except ImportError:
                pass
        _prometheus_metrics_cache['metrics'] = m
    return _prometheus_metrics_cache.get('metrics')

# ============================================================
# IMPORT SHARED REDIS FROM memory_cache
# ============================================================
try:
    from .memory_cache import HybridCache, get_redis_client, is_redis_connected, REDIS_AVAILABLE
except ImportError:
    from memory_cache import HybridCache, get_redis_client, is_redis_connected, REDIS_AVAILABLE

if TYPE_CHECKING:
    from openai import OpenAI as OpenAIClient, AsyncOpenAI as AsyncOpenAIClient

OPENAI_AVAILABLE = False
openai_module: Any = None
RateLimitError: Any = Exception  # Fallback type
APITimeoutError: Any = Exception  # Fallback type
APIConnectionError: Any = Exception  # Fallback type

try:
    import openai as openai_module
    from openai import RateLimitError, APITimeoutError, APIConnectionError
    OPENAI_AVAILABLE = True
except ImportError:
    logger.warning("OpenAI package not installed.")

# ============================================================
# CONFIGURATION
# ============================================================
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "5.0"))
LLM_ENABLED = os.getenv("LLM_QUERY_PROCESSING", "true").lower() == "true" and OPENAI_AVAILABLE
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "150"))

LLM_CACHE_ENABLED = os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true"
LLM_CACHE_PREFIX = "llm_query:"
LLM_CACHE_TTL = int(os.getenv("LLM_CACHE_TTL", "86400"))  # Default: 24 hours (86400 seconds)

LLM_RETRY_ATTEMPTS = int(os.getenv("LLM_RETRY_ATTEMPTS", "3"))
LLM_RETRY_MIN_WAIT = float(os.getenv("LLM_RETRY_MIN_WAIT", "1.0"))
LLM_RETRY_MAX_WAIT = float(os.getenv("LLM_RETRY_MAX_WAIT", "10.0"))

logger.info(f"LLM Retry: attempts={LLM_RETRY_ATTEMPTS}, wait={LLM_RETRY_MIN_WAIT}-{LLM_RETRY_MAX_WAIT}s")

logger.info(f"LLM Query Processor: model={LLM_MODEL}, enabled={LLM_ENABLED}")
logger.info(f"LLM Cache: TTL={LLM_CACHE_TTL}s ({LLM_CACHE_TTL // 3600}h), prefix={LLM_CACHE_PREFIX}")


# ============================================================
# DATA STRUCTURES
# ============================================================
@dataclass
class QueryProcessingResult:
    original_query: str
    corrected_query: str
    was_corrected: bool
    intent: str
    product_type: Optional[str]
    modifier: Optional[str]
    related_terms: List[str]
    confidence: float
    processing_time_ms: float
    from_cache: bool = False
    error: Optional[str] = None
    openai_request_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_query": self.original_query,
            "corrected_query": self.corrected_query,
            "was_corrected": self.was_corrected,
            "intent": self.intent,
            "product_type": self.product_type,
            "modifier": self.modifier,
            "related_terms": self.related_terms,
            "confidence": self.confidence,
            "processing_time_ms": self.processing_time_ms,
            "from_cache": self.from_cache,
            "openai_request_id": self.openai_request_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], from_cache: bool = False, 
                  original_query_override: Optional[str] = None) -> QueryProcessingResult:
        return cls(
            original_query=original_query_override or data.get("original_query", ""),
            corrected_query=data.get("corrected_query", ""),
            was_corrected=data.get("was_corrected", False),
            intent=data.get("intent", "product_search"),
            product_type=data.get("product_type"),
            modifier=data.get("modifier"),
            related_terms=data.get("related_terms", []),
            confidence=data.get("confidence", 0.0),
            processing_time_ms=0.0 if from_cache else data.get("processing_time_ms", 0.0),
            from_cache=from_cache,
            openai_request_id=data.get("openai_request_id"),
        )

# ============================================================
# LLM CACHE - TTL-based expiration (THREAD-SAFE)
# ============================================================
class LLMQueryCache:
    """LLM cache using shared Redis - TTL-based expiration"""
    
    def __init__(self):
        # Memory fallback cache (for when Redis is unavailable)
        # Using TTLCache with max size to prevent unbounded growth
        self._memory_cache: TTLCache = TTLCache(maxsize=1000, ttl=LLM_CACHE_TTL)
        self._memory_lock = threading.RLock()  # Lock for thread-safe memory cache access
        
    def _hash_key(self, query: str) -> str:
        return hashlib.md5(query.lower().strip().encode()).hexdigest()
    
    def _make_full_key(self, key: str) -> str:
        return f"{LLM_CACHE_PREFIX}{key}"
    
    def _get_redis(self) -> Optional[Any]:
        """Get shared Redis client."""
        return get_redis_client()
    
    def _serialize(self, data: Dict[str, Any]) -> bytes:
        return json.dumps(data).encode('utf-8')
    
    def _deserialize(self, data: bytes) -> Dict[str, Any]:
        return json.loads(data.decode('utf-8'))
    
    def get(self, query: str) -> Optional[QueryProcessingResult]:
        global _cache_hits, _cache_misses
        key = self._hash_key(query)
        full_key = self._make_full_key(key)

        # Try Redis first
        redis = self._get_redis()
        if redis:
            try:
                data = redis.get(full_key)
                if data:
                    with _metrics_lock:
                        _cache_hits += 1
                    # Record cache hit in Prometheus
                    m = _get_prometheus_metrics()
                    if m:
                        m.record_cache("llm_get", True)
                    parsed = self._deserialize(data)
                    result = QueryProcessingResult.from_dict(parsed, from_cache=True, original_query_override=query)
                    logger.info(f"[LLM_CACHE] HIT: '{query}' -> '{result.corrected_query}'")
                    return result
            except Exception as e:
                logger.warning(f"[LLM_CACHE] Redis GET failed: {e}")

        # Fallback to memory (check expiry) - with lock
        with self._memory_lock:
            data = self._memory_cache.get(full_key)
            if data:
                with _metrics_lock:
                    _cache_hits += 1
                # Record cache hit in Prometheus
                m = _get_prometheus_metrics()
                if m:
                    m.record_cache("llm_get", True)
                result = QueryProcessingResult.from_dict(data, from_cache=True, original_query_override=query)
                logger.info(f"[LLM_CACHE] HIT (memory): '{query}' -> '{result.corrected_query}'")
                return result

        with _metrics_lock:
            _cache_misses += 1
        # Record cache miss in Prometheus
        m = _get_prometheus_metrics()
        if m:
            m.record_cache("llm_get", False)
        return None
            
    def set(self, query: str, result: QueryProcessingResult) -> None:
        """Store with TTL expiration."""
        key = self._hash_key(query)
        full_key = self._make_full_key(key)
        data = result.to_dict()
        
        redis = self._get_redis()
        if redis:
            try:
                # SETEX with TTL
                redis.setex(full_key, LLM_CACHE_TTL, self._serialize(data))
                logger.debug(f"[LLM_CACHE] SET (TTL={LLM_CACHE_TTL}s): '{query}'")
                return
            except Exception as e:
                logger.warning(f"[LLM_CACHE] Redis SET failed: {e}")
        
        # Fallback to memory with expiry tracking - with lock
        with self._memory_lock:
            self._memory_cache[full_key] = data
            
    def set_both(self, original_query: str, result: QueryProcessingResult) -> None:
        """Store by both original and corrected query (with TTL)."""
        self.set(original_query, result)
        
        corrected_norm = result.corrected_query.lower().strip()
        original_norm = original_query.lower().strip()
        
        if corrected_norm and corrected_norm != original_norm:
            key = self._hash_key(corrected_norm)
            full_key = self._make_full_key(key)
            data = result.to_dict()
            
            redis = self._get_redis()
            if redis:
                try:
                    redis.setex(full_key, LLM_CACHE_TTL, self._serialize(data))
                    logger.debug(f"[LLM_CACHE] SET (corrected, TTL={LLM_CACHE_TTL}s): '{corrected_norm}'")
                except Exception:
                    with self._memory_lock:
                        self._memory_cache[full_key] = data
            else:
                with self._memory_lock:
                    self._memory_cache[full_key] = data
                    
    def delete(self, query: str) -> bool:
        key = self._hash_key(query)
        full_key = self._make_full_key(key)
        deleted = False
        
        redis = self._get_redis()
        if redis:
            try:
                deleted = bool(redis.delete(full_key))
            except Exception:
                pass
        
        with self._memory_lock:
            if full_key in self._memory_cache:
                del self._memory_cache[full_key]
                deleted = True
        
        return deleted
    
    def clear(self) -> Dict[str, Any]:
        """Clear all LLM cache entries."""
        deleted = 0
        
        redis = self._get_redis()
        if redis:
            try:
                cursor = 0
                while True:
                    cursor, keys = redis.scan(cursor, match=f"{LLM_CACHE_PREFIX}*", count=100)
                    if keys:
                        deleted += redis.delete(*keys)
                    if cursor == 0:
                        break
            except Exception as e:
                logger.warning(f"[LLM_CACHE] Redis CLEAR failed: {e}")
        
        # Clear memory - with lock
        with self._memory_lock:
            mem_count = len(self._memory_cache)
            self._memory_cache.clear()
        
        return {"cleared": True, "entries_deleted": deleted}
    
    def get_stats(self) -> Dict[str, Any]:
        count = 0
        redis = self._get_redis()
        if redis:
            try:
                cursor = 0
                while True:
                    cursor, keys = redis.scan(cursor, match=f"{LLM_CACHE_PREFIX}*", count=100)
                    count += len(keys)
                    if cursor == 0:
                        break
            except Exception:
                pass
        
        # Clean up expired memory entries before counting - with lock
        with self._memory_lock:
            memory_entries = len(self._memory_cache)
        
        return {
            "enabled": LLM_CACHE_ENABLED,
            "backend": "redis" if is_redis_connected() else "memory",
            "redis_connected": is_redis_connected(),
            "total_cached_queries": count,
            "ttl_seconds": LLM_CACHE_TTL,
            "ttl_hours": round(LLM_CACHE_TTL / 3600, 2),
            "persistent": False,
            "key_prefix": LLM_CACHE_PREFIX,
            "memory_fallback_entries": memory_entries,
        }
    
    def get_by_query(self, query: str) -> Dict[str, Any]:
        key = self._hash_key(query)
        full_key = self._make_full_key(key)
        
        # Try Redis
        redis = self._get_redis()
        if redis:
            try:
                data = redis.get(full_key)
                if data:
                    ttl = redis.ttl(full_key)
                    return {
                        "found": True,
                        "query": query,
                        "redis_key": full_key,
                        "ttl_remaining_seconds": ttl,
                        "ttl_configured_seconds": LLM_CACHE_TTL,
                        "cached_data": self._deserialize(data),
                    }
            except Exception:
                pass
        
        # Try memory - with lock
        with self._memory_lock:
            data = self._memory_cache.get(full_key)
            if data:
                return {
                    "found": True,
                    "query": query,
                    "redis_key": full_key,
                    "ttl_remaining_seconds": LLM_CACHE_TTL,  # TTLCache doesn't expose per-item TTL
                    "ttl_configured_seconds": LLM_CACHE_TTL,
                    "storage": "memory_fallback",
                    "cached_data": data.copy() if isinstance(data, dict) else data,
                }
            
        return {"found": False, "query": query, "redis_key": full_key}
    
    def update_entry(self, query: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        ALLOWED = {"corrected_query", "was_corrected", "intent", 
                "product_type", "modifier", "related_terms", "confidence"}
        
        invalid = set(updates.keys()) - ALLOWED
        if invalid:
            return {"updated": False, "error": f"Invalid fields: {invalid}"}
        
        key = self._hash_key(query)
        full_key = self._make_full_key(key)
        
        # Get existing data and TTL
        data = None
        remaining_ttl = LLM_CACHE_TTL
        
        redis = self._get_redis()
        if redis:
            try:
                raw = redis.get(full_key)
                if raw:
                    data = self._deserialize(raw)
                    ttl = redis.ttl(full_key)
                    if ttl > 0:
                        remaining_ttl = ttl
            except Exception:
                pass
        
        if data is None:
            with self._memory_lock:
                cached_data = self._memory_cache.get(full_key)
                if cached_data:
                    data = cached_data.copy() if isinstance(cached_data, dict) else cached_data
                    remaining_ttl = LLM_CACHE_TTL  # TTLCache doesn't expose per-item TTL
                
        if not data:
            return {"updated": False, "error": "Query not found in cache"}
        
        changes = {}
        for field, new_val in updates.items():
            if data.get(field) != new_val:
                changes[field] = {"old": data.get(field), "new": new_val}
                data[field] = new_val
        
        if not changes:
            return {"updated": False, "message": "No changes"}
        
        data["manually_edited"] = True
        data["edit_timestamp"] = int(time.time())
        
        # Save back with remaining TTL
        if redis:
            try:
                redis.setex(full_key, max(1, remaining_ttl), self._serialize(data))
            except Exception:
                with self._memory_lock:
                    self._memory_cache[full_key] = data
        else:
            with self._memory_lock:
                self._memory_cache[full_key] = data
        
        return {
            "updated": True, 
            "changes": changes, 
            "updated_data": data, 
            "ttl_remaining_seconds": remaining_ttl
        }

# Global cache
_llm_cache: Optional[LLMQueryCache] = None


def _get_cache() -> LLMQueryCache:
    global _llm_cache
    if _llm_cache is None:
        _llm_cache = LLMQueryCache()
    return _llm_cache


# ============================================================
# SYSTEM PROMPT
# ============================================================
SYSTEM_PROMPT = """You are an e-commerce search query processor. Your job is to:

1. DETECT INTENT: Determine if user is searching for a PRODUCT or a SHOP/SELLER
2. FIX SPELLING/TYPOS: Correct any spelling mistakes, typos, or phonetic errors
3. UNDERSTAND QUERY: Identify what the user is looking for
4. IDENTIFY RELATED TERMS: List semantically related search terms

INTENT DETECTION RULES:
- "shop_search" intent: Query looks like a business/store/seller name (proper nouns, contains "store", "shop", "mart", "traders", company-like names)
- "product_search" intent: Query looks like a product (common nouns, descriptive terms like "blue", "large", product categories)

EXAMPLES:

SHOP SEARCH (intent: "shop_search"):
- "Al Madina" -> shop name, corrected: "Al Madina", intent: "shop_search"
- "almadina store" -> shop name, corrected: "Al Madina Store", intent: "shop_search"
- "khan traders" -> shop name, corrected: "Khan Traders", intent: "shop_search"

PRODUCT SEARCH (intent: "product_search"):
- "office beg" -> product, corrected: "office bag", product_type: "bag", modifier: "office"
- "fry pen" -> product, corrected: "fry pan", product_type: "pan", modifier: "fry"
- "hodie" -> product, corrected: "hoodie", product_type: "hoodie"
- "lether jacket" -> product, corrected: "leather jacket", product_type: "jacket", modifier: "leather"
- "air pods pro" -> product, corrected: "AirPods Pro", product_type: "earbuds", modifier: "Pro"
- "airpods pro" -> product, corrected: "AirPods Pro", product_type: "earbuds", modifier: "Pro"

IMPORTANT RULES:
- Always correct clear spelling errors.
- For shop searches, normalize capitalization (title case)
- For product searches, provide related_terms for query expansion
- product_type and modifier are only for product searches (null for shop searches)
- BE CONSISTENT: Similar queries should produce identical product_type values

Respond ONLY with valid JSON in this exact format:
{
  "corrected_query": "the corrected search query",
  "was_corrected": true/false,
  "intent": "product_search" or "shop_search",
  "product_type": "main product type or null",
  "modifier": "product modifier or null", 
  "related_terms": ["term1", "term2", "term3"],
  "confidence": 0.0-1.0
}"""


# ============================================================
# OPENAI PROCESSOR
# ============================================================
class OpenAIQueryProcessor:
    client: OpenAIClient
    async_client: AsyncOpenAIClient
    
    def __init__(self) -> None:
        if not OPENAI_AVAILABLE:
            raise RuntimeError("OpenAI package not installed")
        self.client = openai_module.OpenAI()
        self.async_client = openai_module.AsyncOpenAI()
        self.model = LLM_MODEL
        self.timeout = LLM_TIMEOUT
        self.temperature = LLM_TEMPERATURE
        self.max_tokens = LLM_MAX_TOKENS
    
    def _parse_response(self, content: str, original_query: str) -> Dict[str, Any]:
        try:
            content = content.strip()
            for prefix in ["```json", "```"]:
                if content.startswith(prefix):
                    content = content[len(prefix):]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            data = json.loads(content)
            corrected = data.get("corrected_query", original_query)
            was_corrected = data.get("was_corrected", False)
            
            if corrected.lower().strip() != original_query.lower().strip():
                was_corrected = True
            
            return {
                "corrected_query": corrected,
                "was_corrected": was_corrected,
                "intent": data.get("intent", "product_search"),
                "product_type": data.get("product_type"),
                "modifier": data.get("modifier"),
                "related_terms": data.get("related_terms", [])[:10],
                "confidence": min(1.0, max(0.0, float(data.get("confidence", 0.8)))),
            }
        except Exception as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            return {
                "corrected_query": original_query,
                "was_corrected": False,
                "intent": "product_search",
                "product_type": None,
                "modifier": None,
                "related_terms": [],
                "confidence": 0.5,
            }
    
    def _extract_request_id(self, response: Any) -> Optional[str]:
        try:
            rid = getattr(response, '_request_id', None)
            if rid and isinstance(rid, str):
                return rid[4:] if rid.startswith('req_') else rid
        except Exception:
            pass
        return None
    
    # NEW: Retry-enabled sync OpenAI call
    @retry(
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=LLM_RETRY_MIN_WAIT, max=LLM_RETRY_MAX_WAIT),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call_openai(self, messages: List[Dict[str, str]]) -> Any:
        """Make OpenAI API call with retry logic for transient errors."""
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )
    
    # NEW: Retry-enabled async OpenAI call
    @retry(
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=LLM_RETRY_MIN_WAIT, max=LLM_RETRY_MAX_WAIT),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _call_openai_async(self, messages: List[Dict[str, str]]) -> Any:
        """Make async OpenAI API call with retry logic for transient errors."""
        return await self.async_client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )
    
    def process_query(self, query: str) -> QueryProcessingResult:
        query = (query or "").strip()
        if not query:
            return QueryProcessingResult(
                original_query="", corrected_query="", was_corrected=False,
                intent="empty", product_type=None, modifier=None,
                related_terms=[], confidence=1.0, processing_time_ms=0.0,
            )
        
        if LLM_CACHE_ENABLED:
            cached = _get_cache().get(query)
            if cached:
                return cached
        
        start = time.time()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Process this search query: {query}"}
        ]
        
        try:
            # Use retry-enabled method
            response = self._call_openai(messages)
            
            request_id = self._extract_request_id(response)
            parsed = self._parse_response(response.choices[0].message.content or "", query)
            elapsed = (time.time() - start) * 1000
            _latency_samples.append(elapsed)

            # Record LLM latency in Prometheus metrics
            m = _get_prometheus_metrics()
            if m:
                m.record_dependency("llm", elapsed, None)

            result = QueryProcessingResult(
                original_query=query,
                corrected_query=parsed["corrected_query"],
                was_corrected=parsed["was_corrected"],
                intent=parsed["intent"],
                product_type=parsed["product_type"],
                modifier=parsed["modifier"],
                related_terms=parsed["related_terms"],
                confidence=parsed["confidence"],
                processing_time_ms=elapsed,
                openai_request_id=request_id,
            )
            
            if LLM_CACHE_ENABLED:
                _get_cache().set_both(query, result)
            
            logger.info(f"[LLM] '{query}' -> '{result.corrected_query}' ({elapsed:.0f}ms)")
            return result
            
        except Exception as e:
            logger.error(f"[LLM] Failed after retries: {e}")
            elapsed = (time.time() - start) * 1000
            # Record LLM error in Prometheus metrics
            m = _get_prometheus_metrics()
            if m:
                m.record_dependency("llm", elapsed, type(e).__name__)
            return QueryProcessingResult(
                original_query=query, corrected_query=query, was_corrected=False,
                intent="product_search", product_type=None, modifier=None,
                related_terms=[], confidence=0.0,
                processing_time_ms=elapsed, error=str(e),
            )

    async def process_query_async(self, query: str) -> QueryProcessingResult:
        query = (query or "").strip()
        if not query:
            return QueryProcessingResult(
                original_query="", corrected_query="", was_corrected=False,
                intent="empty", product_type=None, modifier=None,
                related_terms=[], confidence=1.0, processing_time_ms=0.0,
            )
        
        if LLM_CACHE_ENABLED:
            cached = _get_cache().get(query)
            if cached:
                return cached
        
        start = time.time()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Process this search query: {query}"}
        ]
        
        try:
            # Use retry-enabled async method
            response = await self._call_openai_async(messages)
            
            request_id = self._extract_request_id(response)
            parsed = self._parse_response(response.choices[0].message.content or "", query)
            elapsed = (time.time() - start) * 1000
            _latency_samples.append(elapsed)

            # Record LLM latency in Prometheus metrics
            m = _get_prometheus_metrics()
            if m:
                m.record_dependency("openai", elapsed, None)

            result = QueryProcessingResult(
                original_query=query,
                corrected_query=parsed["corrected_query"],
                was_corrected=parsed["was_corrected"],
                intent=parsed["intent"],
                product_type=parsed["product_type"],
                modifier=parsed["modifier"],
                related_terms=parsed["related_terms"],
                confidence=parsed["confidence"],
                processing_time_ms=elapsed,
                openai_request_id=request_id,
            )

            if LLM_CACHE_ENABLED:
                _get_cache().set_both(query, result)

            logger.info(f"[LLM] '{query}' -> '{result.corrected_query}' ({elapsed:.0f}ms)")
            return result

        except Exception as e:
            logger.error(f"[LLM] Failed after retries: {e}")
            elapsed = (time.time() - start) * 1000
            # Record LLM error in Prometheus metrics
            m = _get_prometheus_metrics()
            if m:
                m.record_dependency("openai", elapsed, type(e).__name__)
            return QueryProcessingResult(
                original_query=query, corrected_query=query, was_corrected=False,
                intent="product_search", product_type=None, modifier=None,
                related_terms=[], confidence=0.0,
                processing_time_ms=elapsed, error=str(e),
            )

# ============================================================
# GLOBAL FUNCTIONS
# ============================================================
_query_processor: Optional[OpenAIQueryProcessor] = None
_processor_lock = threading.Lock()


def get_query_processor() -> OpenAIQueryProcessor:
    global _query_processor
    if not OPENAI_AVAILABLE:
        raise RuntimeError("OpenAI not installed")
    if _query_processor is None:
        with _processor_lock:
            if _query_processor is None:
                _query_processor = OpenAIQueryProcessor()
    return _query_processor


def _disabled_result(query: str) -> QueryProcessingResult:
    return QueryProcessingResult(
        original_query=query, corrected_query=query, was_corrected=False,
        intent="product_search", product_type=None, modifier=None,
        related_terms=[], confidence=1.0, processing_time_ms=0.0,
    )


def process_query(query: str) -> QueryProcessingResult:
    if not LLM_ENABLED:
        return _disabled_result(query)
    return get_query_processor().process_query(query)


async def process_query_async(query: str) -> QueryProcessingResult:
    if not LLM_ENABLED:
        return _disabled_result(query)
    return await get_query_processor().process_query_async(query)


# ============================================================
# CACHE API FUNCTIONS (for endpoints in main.py)
# ============================================================

def clear_cache() -> Dict[str, Any]:
    return _get_cache().clear()


def get_cache_stats() -> Dict[str, Any]:
    return _get_cache().get_stats()

def get_llm_metrics() -> Dict[str, Any]:
    """Get LLM performance metrics including latency percentiles and cache hit rate."""
    with _metrics_lock:
        hits = _cache_hits
        misses = _cache_misses
    
    total_requests = hits + misses
    hit_rate = (hits / total_requests * 100) if total_requests > 0 else 0.0
    
    # Calculate latency percentiles
    latencies = list(_latency_samples)
    latency_stats = {}
    
    if latencies:
        sorted_latencies = sorted(latencies)
        latency_stats = {
            "count": len(latencies),
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "mean_ms": round(statistics.mean(latencies), 1),
            "median_ms": round(statistics.median(latencies), 1),
            "p90_ms": round(sorted_latencies[int(len(sorted_latencies) * 0.90)] if len(sorted_latencies) >= 10 else max(latencies), 1),
            "p95_ms": round(sorted_latencies[int(len(sorted_latencies) * 0.95)] if len(sorted_latencies) >= 20 else max(latencies), 1),
            "p99_ms": round(sorted_latencies[int(len(sorted_latencies) * 0.99)] if len(sorted_latencies) >= 100 else max(latencies), 1),
        }
    else:
        latency_stats = {"count": 0, "message": "No samples yet"}
    
    return {
        "cache": {
            "hits": hits,
            "misses": misses,
            "total_requests": total_requests,
            "hit_rate_percent": round(hit_rate, 2),
        },
        "openai_latency": latency_stats,
        "sample_window": 1000,
    }


def reset_llm_metrics() -> Dict[str, Any]:
    """Reset all LLM metrics counters."""
    global _cache_hits, _cache_misses
    with _metrics_lock:
        old_hits, old_misses = _cache_hits, _cache_misses
        _cache_hits = 0
        _cache_misses = 0
    _latency_samples.clear()
    return {"reset": True, "previous_hits": old_hits, "previous_misses": old_misses}

def get_cache_entry(query: str) -> Dict[str, Any]:
    return _get_cache().get_by_query(query)


def delete_cache_entry(query: str) -> Dict[str, Any]:
    deleted = _get_cache().delete(query)
    return {"deleted": deleted, "query": query}


def update_cache_entry(query: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    return _get_cache().update_entry(query, updates)
