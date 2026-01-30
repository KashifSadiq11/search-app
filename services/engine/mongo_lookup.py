from __future__ import annotations

import os
import logging
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Mapping

from pymongo import MongoClient
from pymongo.errors import PyMongoError, OperationFailure
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_DB_NAME = "BuyPASS"
DEFAULT_BUSINESS_COLLECTION = "business"

# ============================================================
# INCREASED TIMEOUTS FOR BETTER RELIABILITY
# ============================================================
# Previous: 3000ms - too aggressive for cloud MongoDB
# New: More realistic timeouts for Atlas clusters
DEFAULT_SERVER_SELECTION_TIMEOUT_MS = 10000  # 10 seconds (was 3s)
DEFAULT_CONNECT_TIMEOUT_MS = 10000           # 10 seconds (was 3s)
DEFAULT_SOCKET_TIMEOUT_MS = 30000            # 30 seconds (was 3s)

# Global client reference for connection reuse
_mongo_client: Optional[MongoClient] = None
_client_initialized: bool = False
_connection_logged: bool = False


class BusinessIdentity(BaseModel):
    """Minimal business identity."""
    mongo_id: str = Field(..., description="Mongo ObjectId as string")
    id: Optional[str] = Field(None, description="Mongo business id as string")
    seller_id: Optional[str] = Field(None, description="Mongo sellerId as string")
    shop_logo_url: Optional[str] = Field(None, description="shopLogo.url (image)")


def _create_mongo_client() -> MongoClient:
    """Create a new MongoDB client with optimized settings."""
    uri = (os.getenv("MONGO_URI") or "").strip()
    
    if not uri:
        raise RuntimeError("MONGO_URI not set")
    
    # Read timeout settings from environment with sensible defaults
    server_selection_timeout = int(os.getenv(
        "MONGO_SERVER_SELECTION_TIMEOUT_MS", 
        str(DEFAULT_SERVER_SELECTION_TIMEOUT_MS)
    ))
    connect_timeout = int(os.getenv(
        "MONGO_CONNECT_TIMEOUT_MS", 
        str(DEFAULT_CONNECT_TIMEOUT_MS)
    ))
    socket_timeout = int(os.getenv(
        "MONGO_SOCKET_TIMEOUT_MS", 
        str(DEFAULT_SOCKET_TIMEOUT_MS)
    ))
    
    logger.info(
        f"[MONGO] Creating client with timeouts: "
        f"serverSelection={server_selection_timeout}ms, "
        f"connect={connect_timeout}ms, "
        f"socket={socket_timeout}ms"
    )
    
    client = MongoClient(
        uri,
        # Timeouts - more generous for cloud Atlas
        serverSelectionTimeoutMS=server_selection_timeout,
        connectTimeoutMS=connect_timeout,
        socketTimeoutMS=socket_timeout,
        
        # Connection pool settings
        retryWrites=True,
        retryReads=True,  # Added: retry read operations too
        maxPoolSize=10,
        minPoolSize=1,    # Reduced from 2 to 1 to avoid unnecessary connections
        maxIdleTimeMS=60000,  # Increased from 30s to 60s
        waitQueueTimeoutMS=10000,  # Increased from 5s to 10s
        
        # Compression (optional - comment out if causing issues)
        # compressors=["zstd", "snappy", "zlib"],
        
        # Heartbeat frequency - how often to check server status
        heartbeatFrequencyMS=30000,  # 30 seconds (default is 10s, reducing load)
        
        # Direct connection for single node (if not using replica set)
        # directConnection=False,  # Set to True if connecting to single node
    )
    
    return client


def init_mongo_connection() -> bool:
    """
    Initialize MongoDB connection at application startup.
    Call this during FastAPI startup event to pre-warm the connection.
    
    Returns:
        True if connection successful, False otherwise
    """
    global _mongo_client, _client_initialized, _connection_logged
    
    if _client_initialized and _mongo_client is not None:
        return True
    
    try:
        _mongo_client = _create_mongo_client()
        db_name = os.getenv("MONGO_DB", DEFAULT_DB_NAME)
        
        # Force connection by pinging the database
        _mongo_client[db_name].command("ping")
        
        # Pre-warm by listing collections (optional, can be slow)
        # _mongo_client[db_name].list_collection_names()
        
        _client_initialized = True
        
        if not _connection_logged:
            logger.info("[MONGO] Connection established ✓")
            _connection_logged = True
        
        return True
        
    except OperationFailure as e:
        if not _connection_logged:
            logger.error(f"[MONGO] Authentication failed (code={getattr(e, 'code', None)})")
            _connection_logged = True
        _client_initialized = False
        return False
    except Exception as e:
        if not _connection_logged:
            logger.error(f"[MONGO] Connection failed: {e}")
            _connection_logged = True
        _client_initialized = False
        return False


def get_mongo_client() -> MongoClient:
    """
    Get the MongoDB client. Uses pre-initialized client if available.
    Falls back to lazy initialization if not pre-initialized.
    """
    global _mongo_client, _client_initialized, _connection_logged
    
    if _client_initialized and _mongo_client is not None:
        return _mongo_client
    
    if _mongo_client is None:
        _mongo_client = _create_mongo_client()
        
        db_name = os.getenv("MONGO_DB", DEFAULT_DB_NAME)
        _mongo_client[db_name].command("ping")
        
        _client_initialized = True
        
        if not _connection_logged:
            logger.info("[MONGO] Connection established (lazy) ✓")
            _connection_logged = True
    
    return _mongo_client


def close_mongo_connection():
    """
    Close the MongoDB connection. Call this during application shutdown.
    """
    global _mongo_client, _client_initialized, _connection_logged
    
    if _mongo_client is not None:
        try:
            _mongo_client.close()
            logger.info("[MONGO] Connection closed")
        except Exception:
            pass
        finally:
            _mongo_client = None
            _client_initialized = False
            _connection_logged = False


def get_business_collection():
    """Get the business collection."""
    client = get_mongo_client()
    db = os.getenv("MONGO_DB", DEFAULT_DB_NAME)
    coll = os.getenv("MONGO_BUSINESS_COLLECTION", DEFAULT_BUSINESS_COLLECTION)
    return client[db][coll]


# ============================================================
# HELPERS (unchanged)
# ============================================================
def _to_int_ids(values: Iterable[Any]) -> List[int]:
    out: List[int] = []
    for v in values:
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, int):
            out.append(v)
            continue
        s = str(v).strip()
        if s.isdigit():
            out.append(int(s))
    return out


def _to_digit_str_ids(values: Iterable[Any]) -> List[str]:
    """Normalize IDs to digit-only strings."""
    out: List[str] = []
    for v in values:
        if isinstance(v, bool) or v is None:
            continue
        s = str(v).strip()
        if s.isdigit():
            out.append(s)
    return out


def _extract_mongo_number(val: Any) -> Optional[str]:
    """Extract numeric value from MongoDB Extended JSON formats."""
    if val is None:
        return None
    
    if isinstance(val, dict):
        if "$numberLong" in val:
            return str(val["$numberLong"])
        if "$numberInt" in val:
            return str(val["$numberInt"])
        if "$numberDouble" in val:
            try:
                return str(int(float(val["$numberDouble"])))
            except (ValueError, TypeError):
                return None
        return None
    
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return str(int(val))
    
    if isinstance(val, str):
        s = val.strip()
        if s.isdigit():
            return s
        try:
            return str(int(float(s)))
        except (ValueError, TypeError):
            return None
    
    return None


def _get_shop_logo_url(raw: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Extract shopLogo.url from Mongo-style dict."""
    if not raw:
        return None
    shop_logo = raw.get("shopLogo")
    if isinstance(shop_logo, dict):
        url = shop_logo.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


# ============================================================
# PUBLIC LOOKUP (BY MONGO "id" FIELD)
# ============================================================
def fetch_business_identities(business_ids: List[Any]) -> Dict[str, BusinessIdentity]:
    """Batch lookup business identities by Mongo field: 'id' (numeric)."""
    ids = sorted(set(_to_int_ids(business_ids)))
    if not ids:
        return {}

    try:
        coll = get_business_collection()
        cursor = coll.find(
            {"id": {"$in": ids}},
            {"_id": 1, "id": 1, "sellerId": 1, "shopLogo.url": 1},
        )

        out: Dict[str, BusinessIdentity] = {}
        for doc in cursor:
            if "_id" not in doc:
                continue
            
            raw_bid = doc.get("id")
            extracted_bid = _extract_mongo_number(raw_bid)
            
            if extracted_bid is None:
                continue
            
            raw_seller_id = doc.get("sellerId")
            extracted_seller_id = _extract_mongo_number(raw_seller_id)
            
            identity = BusinessIdentity(
                mongo_id=str(doc["_id"]),
                id=extracted_bid,
                seller_id=extracted_seller_id,
                shop_logo_url=_get_shop_logo_url(doc),
            )
            out[str(identity.id)] = identity
        return out

    except PyMongoError as e:
        logger.warning(f"[MONGO] Business identity lookup (by id) failed: {e}")
        return {}
    except Exception:
        return {}


# ============================================================
# PUBLIC LOOKUP (BY MONGO "sellerId" FIELD)
# ============================================================
def fetch_business_identities_by_seller_ids(seller_ids: List[Any]) -> Dict[str, BusinessIdentity]:
    """Batch lookup business identities by Mongo field: 'sellerId' (string)."""
    ids = sorted(set(_to_digit_str_ids(seller_ids)))
    if not ids:
        return {}

    try:
        coll = get_business_collection()
        cursor = coll.find(
            {"sellerId": {"$in": ids}},
            {"_id": 1, "id": 1, "sellerId": 1, "shopLogo.url": 1},
        )

        out: Dict[str, BusinessIdentity] = {}
        for doc in cursor:
            if "_id" not in doc:
                continue
            
            raw_sid = doc.get("sellerId")
            extracted_sid = _extract_mongo_number(raw_sid)
            
            if extracted_sid is None:
                continue
            
            raw_id = doc.get("id")
            extracted_id = _extract_mongo_number(raw_id)
            
            identity = BusinessIdentity(
                mongo_id=str(doc["_id"]),
                id=extracted_id,
                seller_id=extracted_sid,
                shop_logo_url=_get_shop_logo_url(doc),
            )
            out[str(identity.seller_id)] = identity
        return out

    except PyMongoError as e:
        logger.warning(f"[MONGO] Business identity lookup (by sellerId) failed: {e}")
        return {}
    except Exception:
        return {}