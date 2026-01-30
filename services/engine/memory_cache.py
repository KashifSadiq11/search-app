# memory_cache.py
"""
Centralized Redis Connection Manager

SINGLE Redis connection for the entire application.
All modules should import from here.
"""

from typing import Any, Optional, Dict
import time
import json
import pickle
import logging
import os

logger = logging.getLogger(__name__)

# ============================================================
# METRICS IMPORT (lazy to avoid circular imports)
# ============================================================
_metrics_cache = {}

def _get_metrics():
    """Lazy import of metrics to avoid circular imports."""
    if 'metrics' not in _metrics_cache:
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
        _metrics_cache['metrics'] = m
    return _metrics_cache.get('metrics')

# ============================================================
# REDIS AVAILABILITY CHECK
# ============================================================
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not installed. Using in-memory cache.")

# ============================================================
# CONFIGURATION
# ============================================================
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD') or None
REDIS_DB = int(os.getenv('REDIS_DB', '0'))
REDIS_URL = os.getenv('REDIS_URL') or None
REDIS_CONNECT_TIMEOUT = int(os.getenv('REDIS_CONNECT_TIMEOUT', '5'))
REDIS_SOCKET_TIMEOUT = int(os.getenv('REDIS_SOCKET_TIMEOUT', '5'))


# ============================================================
# SINGLETON REDIS CLIENT
# ============================================================
class RedisConnectionManager:
    """Singleton - ensures only ONE Redis connection exists."""
    
    _instance: Optional['RedisConnectionManager'] = None
    _redis_client: Any = None
    _initialized: bool = False
    _connection_error: Optional[str] = None
    
    def __new__(cls) -> 'RedisConnectionManager':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        if not REDIS_AVAILABLE:
            self._connection_error = "Redis package not installed"
            logger.warning("[REDIS] Package not installed - using in-memory fallback")
            return
        
        self._connect()
    
    def _connect(self) -> None:
        """Establish Redis connection."""
        try:
            if REDIS_URL:
                self._redis_client = redis.from_url(
                    REDIS_URL,
                    decode_responses=False,
                    socket_connect_timeout=REDIS_CONNECT_TIMEOUT,
                    socket_timeout=REDIS_SOCKET_TIMEOUT,
                )
            else:
                self._redis_client = redis.Redis(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    db=REDIS_DB,
                    password=REDIS_PASSWORD,
                    decode_responses=False,
                    socket_connect_timeout=REDIS_CONNECT_TIMEOUT,
                    socket_timeout=REDIS_SOCKET_TIMEOUT,
                )
            
            # Test connection
            self._redis_client.ping()
            logger.info(f"[REDIS] ✅ Connected: {REDIS_HOST}:{REDIS_PORT} DB={REDIS_DB}")
            
        except Exception as e:
            self._connection_error = str(e)
            self._redis_client = None
            logger.warning(f"[REDIS] ❌ Connection failed: {e}. Using in-memory fallback.")
    
    @property
    def client(self) -> Optional[Any]:
        return self._redis_client
    
    @property
    def is_connected(self) -> bool:
        if not self._redis_client:
            return False
        try:
            self._redis_client.ping()
            return True
        except Exception:
            return False


# Global singleton
_redis_manager: Optional[RedisConnectionManager] = None


def get_redis_manager() -> RedisConnectionManager:
    """Get the singleton Redis manager."""
    global _redis_manager
    if _redis_manager is None:
        _redis_manager = RedisConnectionManager()
    return _redis_manager


# ============================================================
# EAGER INITIALIZATION AT IMPORT TIME
# ============================================================
# This ensures Redis connects when the module is first imported
# and logs the connection status immediately
_redis_manager = RedisConnectionManager()


def get_redis_client() -> Optional[Any]:
    """Get the shared Redis client. Returns None if not connected."""
    return get_redis_manager().client


def is_redis_connected() -> bool:
    """Check if Redis is connected."""
    return get_redis_manager().is_connected


# ============================================================
# HYBRID CACHE
# ============================================================
class HybridCache:
    """Cache using shared Redis connection, falls back to memory."""
    
    def __init__(self, key_prefix: str = "", default_ttl: int = 300):
        self.memory_cache: Dict[str, Dict[str, Any]] = {}
        self.key_prefix = key_prefix
        self.default_ttl = default_ttl
    
    @property
    def redis_client(self) -> Optional[Any]:
        return get_redis_client()
    
    def _make_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"
    
    def _serialize(self, value: Any) -> bytes:
        try:
            return json.dumps(value).encode('utf-8')
        except (TypeError, ValueError):
            return pickle.dumps(value)
    
    def _deserialize(self, data: bytes) -> Any:
        if data is None:
            return None
        try:
            return json.loads(data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return pickle.loads(data)
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        full_key = self._make_key(key)
        ttl = ttl or self.default_ttl

        if self.redis_client:
            start_time = time.perf_counter()
            error = None
            try:
                self.redis_client.setex(full_key, ttl, self._serialize(value))
                return True
            except Exception as e:
                error = type(e).__name__
                logger.warning(f"[CACHE] Redis SET failed: {e}")
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                m = _get_metrics()
                if m:
                    m.record_dependency("redis", duration_ms, error)

        # Memory fallback
        self.memory_cache[full_key] = {
            'value': value,
            'expires_at': time.time() + ttl
        }
        return False

    def get(self, key: str) -> Optional[Any]:
        full_key = self._make_key(key)

        if self.redis_client:
            start_time = time.perf_counter()
            error = None
            try:
                data = self.redis_client.get(full_key)
                hit = data is not None
                m = _get_metrics()
                if m:
                    m.record_cache("get", hit)
                if data:
                    return self._deserialize(data)
                return None
            except Exception as e:
                error = type(e).__name__
                logger.warning(f"[CACHE] Redis GET failed: {e}")
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                m = _get_metrics()
                if m:
                    m.record_dependency("redis", duration_ms, error)

        # Memory fallback
        if full_key not in self.memory_cache:
            m = _get_metrics()
            if m:
                m.record_cache("get", False)
            return None

        item = self.memory_cache[full_key]
        if time.time() > item['expires_at']:
            del self.memory_cache[full_key]
            m = _get_metrics()
            if m:
                m.record_cache("get", False)
            return None

        m = _get_metrics()
        if m:
            m.record_cache("get", True)
        return item['value']
    
    def delete(self, key: str) -> bool:
        full_key = self._make_key(key)
        deleted = False
        
        if self.redis_client:
            try:
                deleted = bool(self.redis_client.delete(full_key))
            except Exception:
                pass
        
        if full_key in self.memory_cache:
            del self.memory_cache[full_key]
            deleted = True
        
        return deleted
    
    def exists(self, key: str) -> bool:
        full_key = self._make_key(key)
        
        if self.redis_client:
            try:
                return bool(self.redis_client.exists(full_key))
            except Exception:
                pass
        
        if full_key in self.memory_cache:
            if time.time() <= self.memory_cache[full_key]['expires_at']:
                return True
            del self.memory_cache[full_key]
        return False
    
    def clear(self, pattern: Optional[str] = None) -> int:
        deleted = 0
        search_pattern = pattern or f"{self.key_prefix}*"
        
        if self.redis_client:
            try:
                cursor = 0
                while True:
                    cursor, keys = self.redis_client.scan(cursor, match=search_pattern, count=100)
                    if keys:
                        deleted += self.redis_client.delete(*keys)
                    if cursor == 0:
                        break
            except Exception as e:
                logger.warning(f"[CACHE] Redis CLEAR failed: {e}")
        
        # Clear memory
        keys_to_del = [k for k in self.memory_cache if k.startswith(self.key_prefix)]
        for k in keys_to_del:
            del self.memory_cache[k]
            deleted += 1
        
        return deleted
    
    def get_ttl(self, key: str) -> int:
        full_key = self._make_key(key)
        
        if self.redis_client:
            try:
                ttl = self.redis_client.ttl(full_key)
                return ttl if ttl >= 0 else -1
            except Exception:
                pass
        
        if full_key in self.memory_cache:
            remaining = self.memory_cache[full_key]['expires_at'] - time.time()
            return max(0, int(remaining))
        
        return -1
    
    def get_many(self, keys: list) -> Dict[str, Any]:
        result = {}
        
        if self.redis_client and keys:
            try:
                pipe = self.redis_client.pipeline()
                full_keys = [self._make_key(k) for k in keys]
                for fk in full_keys:
                    pipe.get(fk)
                values = pipe.execute()
                
                for key, fk, val in zip(keys, full_keys, values):
                    if val:
                        result[key] = self._deserialize(val)
                return result
            except Exception:
                pass
        
        # Memory fallback
        for key in keys:
            val = self.get(key)
            if val is not None:
                result[key] = val
        
        return result
    
    def info(self) -> Dict[str, Any]:
        return {
            'backend': 'redis' if self.redis_client else 'memory',
            'prefix': self.key_prefix,
            'default_ttl': self.default_ttl,
            'memory_items': len(self.memory_cache),
            'redis_connected': is_redis_connected(),
        }


# ============================================================
# BACKWARD COMPATIBLE GLOBAL INSTANCE
# ============================================================
cache = HybridCache(key_prefix="", default_ttl=300)
MemoryCache = HybridCache