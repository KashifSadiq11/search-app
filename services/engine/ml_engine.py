# services/engine/ml_engine.py
import os
import json
import logging
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, cast
import asyncio
import httpx
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import faiss
import joblib
import torch
import torch.nn as nn
from openai import OpenAI
import re
import hashlib

from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

try:
    from .memory_cache import get_redis_client, is_redis_connected
except ImportError:
    from memory_cache import get_redis_client, is_redis_connected

# Lazy metrics import to avoid circular imports
_metrics_cache = {}

def _get_metrics():
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
# EMBEDDING CACHE CONFIGURATION
# ============================================================
EMBEDDING_CACHE_ENABLED = os.getenv("EMBEDDING_CACHE_ENABLED", "true").lower() == "true"
EMBEDDING_CACHE_TTL = int(os.getenv("EMBEDDING_CACHE_TTL", "3600"))  # 1 hour default
EMBEDDING_CACHE_PREFIX = "emb:"

logger.info(f"Embedding Cache: enabled={EMBEDDING_CACHE_ENABLED}, TTL={EMBEDDING_CACHE_TTL}s")

# ==========================================
# GLOBAL WRITE-GUARD
# ==========================================
DISALLOW_INDEX_WRITE: bool = os.getenv("DISALLOW_INDEX_WRITE", "true").lower() == "true"
if DISALLOW_INDEX_WRITE:
    logger.warning("🔒 FAISS index write/mutation is DISABLED (DISALLOW_INDEX_WRITE=true)")
else:
    logger.warning("⚠️ FAISS index write/mutation is ENABLED (DISALLOW_INDEX_WRITE=false)")

# ==========================================
# THRESHOLDS
# ==========================================
def _read_threshold_env(name: str, default: str) -> float:
    raw = os.getenv(name, default)
    try:
        val = float(raw)
    except ValueError:
        logger.warning(f"Invalid {name}='{raw}', falling back to {default}")
        val = float(default)

    if val < -1.0 or val > 1.0:
        logger.warning(f"{name} out of bounds ({val}), clamping to [-1, 1]")
        val = max(-1.0, min(1.0, val))

    return val


SEMANTIC_MIN_SIMILARITY: float = _read_threshold_env("SEMANTIC_MIN_SIMILARITY", "0.30")
logger.warning(f"✅ ml_engine.py loaded from = {__file__}")
logger.warning(f"✅ SEMANTIC_MIN_SIMILARITY = {SEMANTIC_MIN_SIMILARITY}")


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


class MLRecommendationEngine:
    """
    Enterprise-safe ML engine.

    Single-index semantic search:
    - One FAISS index over item-level embedding text (+ numeric features)
    - threshold: SEMANTIC_MIN_SIMILARITY
    - deterministic order: FAISS order
    """

    _INDEX_FILE = "vector_index.faiss"
    _MAPPINGS_FILE = "index_mappings.json"
    _SCALER_FILE = "feature_scaler.pkl"

    def __init__(
        self,
        *,
        index_build_batch_size: int = 100,
        openai_embedding_batch_size: int = 100,
    ):
        """
        index_build_batch_size: controls how many items we process per loop chunk (memory/logging).
        openai_embedding_batch_size: controls how many texts we send per ONE OpenAI embeddings request.
        """
        logger.info("Initializing OpenAI embedding client...")
        self.client = OpenAI(
            http_client=httpx.Client(
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20
                )
            )
        )
        self.embedding_model = "text-embedding-3-large"
        self.embedding_dim = 3072

        # ✅ Numeric features (same 5 as your old version)
        self.numeric_feature_dim = 5
        self.hybrid_dim = self.embedding_dim + self.numeric_feature_dim

        # ✅ Scaler (same pattern as old version)
        self.scaler = StandardScaler()
        self.scaler_fitted = False

        # Batch controls (validated)
        self.index_build_batch_size = int(index_build_batch_size)
        self.openai_embedding_batch_size = int(openai_embedding_batch_size)
        if self.index_build_batch_size <= 0:
            raise ValueError("index_build_batch_size must be > 0")
        if self.openai_embedding_batch_size <= 0:
            raise ValueError("openai_embedding_batch_size must be > 0")

        # Neural collaborative filtering model
        self.ncf_model = None

        # FAISS index
        self.index = None

        # Mappings
        self.index_to_item_id: Dict[int, str] = {}
        self.item_id_to_index: Dict[str, int] = {}

        # Model directory
        self.model_dir = "ml_models/"
        self._executor = ThreadPoolExecutor(max_workers=10)
        os.makedirs(self.model_dir, exist_ok=True)

        self.load_all_models()

    # -------------------------------
    # NORMALIZATION
    # -------------------------------
    def _normalize_phrase(self, text: str) -> str:
        q = (text or "").strip().lower()
        q = re.sub(r"[^a-z0-9\s]+", " ", q)
        q = re.sub(r"\s+", " ", q).strip()
        return q

    def _normalize_forced_atomic(self, text: str) -> str:
        q = (text or "").strip().lower()
        q = re.sub(r"[^a-z0-9\s]+", " ", q)
        q = re.sub(r"\s+", "", q)
        return q

    _METADATA_RE = re.compile(r"\[Metadata:\s*(\{.*\})\s*\]\s*$", re.DOTALL)

    @staticmethod
    def _extract_metadata_from_description(description: str) -> Optional[Dict[str, Any]]:
        if not description:
            return None

        m = MLRecommendationEngine._METADATA_RE.search(description.strip())
        if not m:
            return None

        raw_json = m.group(1)

        try:
            meta = json.loads(raw_json)
            return cast(Dict[str, Any], meta) if isinstance(meta, dict) else None
        except json.JSONDecodeError:
            try:
                meta = json.loads(raw_json.replace("\n", " ").replace("\r", " "))
                return cast(Dict[str, Any], meta) if isinstance(meta, dict) else None
            except json.JSONDecodeError:
                return None

    @staticmethod
    def extract_category_path_from_description(description: str) -> Optional[str]:
        meta = MLRecommendationEngine._extract_metadata_from_description(description)
        if not meta:
            return None

        val = meta.get("category_path")
        val = str(val).strip() if val else ""
        return val or None

    @staticmethod
    def extract_seller_name_from_description(description: str) -> Optional[str]:
        meta = MLRecommendationEngine._extract_metadata_from_description(description)
        if not meta:
            return None

        seller_info = meta.get("seller_info")
        if not isinstance(seller_info, dict):
            return None

        name = seller_info.get("name")
        name = str(name).strip() if name else ""
        return name or None

    def _clean_description_for_embedding(self, text: str, max_chars: int = 50) -> str:
        clean = self._normalize_phrase(text or "")
        if not clean:
            return ""
        if len(clean) <= max_chars:
            return clean
        return clean[:max_chars].rsplit(" ", 1)[0].strip()

    # -------------------------------
    # ITEM EMBEDDING TEXT
    # -------------------------------
    _TITLE_BOOST = 1
    _CATEGORY_BOOST = 1

    def _build_item_text(self, item: Dict[str, Any]) -> str:
        title = (item.get("title") or "").strip()
        if not title:
            return ""

        category = (item.get("category") or "").strip()
        brand = (item.get("brand") or "").strip()
        description = (item.get("description") or "").strip()

        category_path = MLRecommendationEngine.extract_category_path_from_description(description) or ""
        seller_name = MLRecommendationEngine.extract_seller_name_from_description(description) or ""

        title_norm = self._normalize_phrase(title)
        if not title_norm:
            return ""

        category_norm = self._normalize_phrase(category) if category else ""
        seller_norm = self._normalize_phrase(seller_name) if seller_name else ""
        brand_norm = self._normalize_phrase(brand) if brand else ""
        category_path_norm = self._normalize_phrase(category_path) if category_path else ""

        parts: List[str] = []

        for _ in range(self._TITLE_BOOST):
            parts.append(f"Title: {title_norm}")

        if seller_norm:
            parts.append(f"Seller: {seller_norm}")

        if brand_norm:
            parts.append(f"Brand: {brand_norm}")

        if category_norm:
            for _ in range(self._CATEGORY_BOOST):
                parts.append(f"Category: {category_norm}")

        if category_path_norm:
            parts.append(f"CategoryPath: {category_path_norm}")

        desc_clean = self._clean_description_for_embedding(description, max_chars=150)
        if desc_clean:
            parts.append(f"Desc: {desc_clean}")

        return " | ".join(parts)

    def _build_query_text(self, query: str) -> str:
        q_normalize = self._normalize_phrase(query)
        if not q_normalize:
            return ""
        return f"{q_normalize}"
    
    # -------------------------------
    # EMBEDDING CACHE HELPERS
    # -------------------------------
    def _get_embedding_cache_key(self, text: str) -> str:
        """Generate cache key for embedding text."""
        normalized = self._normalize_phrase(text)
        return f"{EMBEDDING_CACHE_PREFIX}{hashlib.md5(normalized.encode()).hexdigest()}"
    
    def _get_cached_embedding(self, text: str) -> Optional[np.ndarray]:
        """Try to get embedding from Redis cache."""
        if not EMBEDDING_CACHE_ENABLED:
            logger.warning("[EMB_CACHE] Cache disabled")  # ADD THIS
            return None
        
        redis = get_redis_client()
        if not redis:
            logger.warning("[EMB_CACHE] Redis not connected")  # ADD THIS
            return None
        
        try:
            cache_key = self._get_embedding_cache_key(text)
            cached = redis.get(cache_key)
            if cached:
                embedding = np.frombuffer(cached, dtype=np.float32).copy()
                if embedding.shape[0] == self.embedding_dim:
                    logger.info(f"[EMB_CACHE] HIT: '{text[:40]}...'")  # CHANGE THIS LINE
                    return embedding
                else:
                    redis.delete(cache_key)
                    logger.warning(f"[EMB_CACHE] Dimension mismatch, deleted stale entry")
            else:
                logger.info(f"[EMB_CACHE] MISS: '{text[:40]}...'")  # ADD THIS
        except Exception as e:
            logger.warning(f"[EMB_CACHE] GET failed: {e}")
        
        return None
    
    def _cache_embedding(self, text: str, embedding: np.ndarray) -> None:
        """Store embedding in Redis cache with TTL."""
        if not EMBEDDING_CACHE_ENABLED:
            return
        
        redis = get_redis_client()
        if not redis:
            return
        
        try:
            cache_key = self._get_embedding_cache_key(text)
            embedding_bytes = embedding.astype(np.float32).tobytes()
            redis.setex(cache_key, EMBEDDING_CACHE_TTL, embedding_bytes)
            logger.info(f"[EMB_CACHE] SET: '{text[:40]}...' TTL={EMBEDDING_CACHE_TTL}s")  
        except Exception as e:
            logger.warning(f"[EMB_CACHE] SET failed: {e}")

    # -------------------------------
    # EMBEDDING GENERATION
    # -------------------------------
    def generate_embedding(self, text: str) -> np.ndarray:
        """Single-text embedding with Redis caching."""
        # Try cache first
        cached = self._get_cached_embedding(text)
        if cached is not None:
            return cached
        
        # Generate from OpenAI
        try:
            resp = self.client.embeddings.create(
                model=self.embedding_model,
                input=text,
            )
            embedding = np.array(resp.data[0].embedding, dtype="float32")
            
            # Cache the result
            self._cache_embedding(text, embedding)
            
            return embedding
        except Exception as e:
            logger.error(f"OpenAI embedding failed for text length={len(text)}: {e}")
            raise EmbeddingError("Failed to generate embedding") from e
        
    async def generate_embedding_async(self, text: str) -> np.ndarray:
        """Async embedding with Redis caching."""
        # Try cache first (sync but fast)
        cached = self._get_cached_embedding(text)
        if cached is not None:
            return cached
        
        # Generate from OpenAI in executor
        loop = asyncio.get_running_loop()
        
        def _generate():
            resp = self.client.embeddings.create(
                model=self.embedding_model,
                input=text,
            )
            return np.array(resp.data[0].embedding, dtype="float32")
        
        embedding = await loop.run_in_executor(self._executor, _generate)
        
        # Cache the result
        self._cache_embedding(text, embedding)
        
        return embedding

    def generate_embeddings_batch(self, texts: List[str]) -> np.ndarray:
        """
        Batch embeddings in ONE OpenAI API call.
        Returns: shape (len(texts), embedding_dim)
        """
        if not texts:
            return np.empty((0, self.embedding_dim), dtype=np.float32)

        try:
            resp = self.client.embeddings.create(
                model=self.embedding_model,
                input=texts,  # ✅ list => true API batching
            )
            out = np.array([d.embedding for d in resp.data], dtype="float32")

            if out.ndim != 2 or out.shape[1] != self.embedding_dim:
                raise ValueError(
                    f"Batch embedding shape mismatch got={out.shape} expected=(*, {self.embedding_dim})"
                )

            if out.shape[0] != len(texts):
                raise ValueError(
                    f"Batch embedding count mismatch got={out.shape[0]} expected={len(texts)}"
                )

            return out
        except Exception as e:
            logger.error(f"OpenAI batch embedding failed for batch_size={len(texts)}: {e}")
            raise EmbeddingError("Failed to generate batched embeddings") from e

    # -------------------------------
    # NUMERIC FEATURES (SINGLE SOURCE OF TRUTH)
    # -------------------------------
    def _item_numeric_features(self, item: Dict[str, Any]) -> np.ndarray:
        """
        price/1000, rating/5, popularity_score, discount/100, in_stock
        """
        def _f(x: Any) -> float:
            try:
                return float(x)
            except (TypeError, ValueError):
                return 0.0

        price = _f(item.get("price"))
        rating = _f(item.get("product_rating"))
        popularity = _f(item.get("popularity_score"))
        discount = _f(item.get("discount"))
        in_stock = item.get("in_stock", True)

        return np.array(
            [
                (price / 1000.0) if price else 0.0,
                (rating / 5.0) if rating else 0.0,
                popularity,
                (discount / 100.0) if discount else 0.0,
                1.0 if bool(in_stock) else 0.0,
            ],
            dtype=np.float32,
        )

    def _query_numeric_features(self, user_context: Optional[Dict[str, Any]]) -> np.ndarray:
        """
        Query-time numeric features.

        - If user_context is provided → build features and scale (if fitted)
        - If user_context is None → return a neutral vector in SCALED SPACE (zeros)
        """
        if not user_context:
            # ✅ Neutral in standardized space
            return np.zeros(self.numeric_feature_dim, dtype=np.float32)

        def _f(x: Any) -> float:
            try:
                return float(x)
            except (TypeError, ValueError):
                return 0.0

        feats = np.array(
            [
                _f(user_context.get("avg_price")) / 1000.0,
                _f(user_context.get("min_rating")) / 5.0,
                _f(user_context.get("popularity_score")),
                _f(user_context.get("discount_preference")) / 100.0,
                1.0 if bool(user_context.get("in_stock_preference")) else 0.0,
            ],
            dtype=np.float32,
        )

        if self.scaler_fitted:
            feats = self.scaler.transform([feats])[0].astype(np.float32)

        return feats
    # -------------------------------
    # HYBRID ITEM VECTOR (TEXT + NUMERIC)
    # -------------------------------
    def generate_item_embeddings(self, item: Dict[str, Any]) -> np.ndarray:
        """
        Legacy per-item path (kept for compatibility / debugging).
        build_vector_index() uses true batch API calls instead.
        """
        text = self._build_item_text(item)
        if not text:
            return np.array([], dtype=np.float32)

        text_embedding = self.generate_embedding(text).astype("float32")
        if text_embedding.shape[0] != self.embedding_dim:
            raise ValueError(
                f"Text embedding dim mismatch got={text_embedding.shape[0]} expected={self.embedding_dim}"
            )

        numeric_features = self._item_numeric_features(item)
        if self.scaler_fitted:
            numeric_features = self.scaler.transform([numeric_features])[0].astype(np.float32)

        combined = np.concatenate([text_embedding, numeric_features]).astype("float32")
        if combined.shape[0] != self.hybrid_dim:
            raise ValueError(f"Hybrid vector dim mismatch got={combined.shape[0]} expected={self.hybrid_dim}")

        return combined

    # -------------------------------
    # INTERNAL: SCORE NORMALIZATION
    # -------------------------------
    def _score_to_cosine_from_index(self, index_obj: Any, score: float) -> float:
        if index_obj is None:
            return -1.0

        metric = getattr(index_obj, "metric_type", None)

        if metric == faiss.METRIC_INNER_PRODUCT:
            return float(score)

        if metric == faiss.METRIC_L2:
            return 1.0 - (float(score) / 2.0)

        logger.warning(f"Unknown FAISS metric_type={metric}; treating similarity as very low")
        return -1.0

    def _set_hnsw_efsearch(self, index_obj: Any, ef_search: int = 128) -> None:
        if index_obj is None:
            return
        hnsw = getattr(index_obj, "hnsw", None)
        if hnsw is None:
            return
        cast(Any, hnsw).efSearch = ef_search

    # -------------------------------
    # INDEX BUILDING
    # -------------------------------
    def build_vector_index(self, items: List[Dict], save_after_build: bool = True) -> None:
        """
        Builds ONE:
          - self.index (semantic item text + numeric features)

        ✅ TRUE batching:
          - builds texts in chunks
          - calls OpenAI embeddings with input=[...] per chunk
        """
        if DISALLOW_INDEX_WRITE:
            logger.error("❌ Attempted to build FAISS index while DISALLOW_INDEX_WRITE=true")
            raise RuntimeError("FAISS index build is disabled in this environment")

        if not items:
            logger.warning("No items to index")
            return

        logger.info(f"Building FAISS vector index with {len(items)} items...")

        # Reset mappings
        self.index_to_item_id = {}
        self.item_id_to_index = {}

        # ---- Fit scaler on all items (same pattern as old) ----
        numeric_features_list: List[List[float]] = []
        for item in items:
            numeric_features_list.append(self._item_numeric_features(item).tolist())

        if numeric_features_list:
            self.scaler.fit(numeric_features_list)
            self.scaler_fitted = True
            logger.info("✅ Feature scaler fitted")
        else:
            self.scaler_fitted = False
            logger.warning("⚠️ No numeric features found; scaler not fitted")

        embeddings: List[np.ndarray] = []

        batch_size = self.index_build_batch_size
        total_batches = (len(items) - 1) // batch_size + 1

        for batch_start in range(0, len(items), batch_size):
            batch = items[batch_start: batch_start + batch_size]
            batch_idx = batch_start // batch_size + 1
            logger.info(f"Processing batch {batch_idx}/{total_batches} (size={len(batch)})")

            # Build texts for valid items (do not call OpenAI yet)
            valid_items: List[Dict[str, Any]] = []
            texts: List[str] = []

            for item in batch:
                item_id = str(item.get("id") or "")
                if not item_id:
                    continue

                text = self._build_item_text(item)
                if not text:
                    continue

                valid_items.append(item)
                texts.append(text)

            if not valid_items:
                continue

            # OpenAI batching can be smaller than loop-batch to control payload size
            sub_bs = self.openai_embedding_batch_size
            for i in range(0, len(valid_items), sub_bs):
                sub_items = valid_items[i: i + sub_bs]
                sub_texts = texts[i: i + sub_bs]

                try:
                    text_embs = self.generate_embeddings_batch(sub_texts)  # (n, 3072)
                except Exception as e:
                    logger.warning(f"Failed OpenAI batch embeddings for sub-batch size={len(sub_texts)}: {e}")
                    continue

                for item, text_embedding in zip(sub_items, text_embs):
                    try:
                        numeric_features = self._item_numeric_features(item)
                        if self.scaler_fitted:
                            numeric_features = self.scaler.transform([numeric_features])[0].astype(np.float32)

                        combined = np.concatenate([text_embedding, numeric_features]).astype("float32")
                        if combined.shape[0] != self.hybrid_dim:
                            raise ValueError(
                                f"Hybrid vector dim mismatch got={combined.shape[0]} expected={self.hybrid_dim}"
                            )

                        embeddings.append(combined)

                        idx = len(embeddings) - 1  # ✅ always aligned to FAISS row id
                        iid = str(item["id"])

                        self.index_to_item_id[idx] = iid
                        self.item_id_to_index[iid] = idx

                    except Exception as e:
                        logger.warning(f"Failed to build hybrid vector for item {item.get('id')}: {e}")

        if not embeddings:
            logger.error("No embeddings created – index not built")
            return

        arr = np.array(embeddings, dtype="float32")
        faiss.normalize_L2(arr)

        # Build single index (dimension = hybrid_dim)
        if len(embeddings) > 10000:
            self.index = faiss.IndexHNSWFlat(self.hybrid_dim, 32, faiss.METRIC_INNER_PRODUCT)
            self.index.hnsw.efConstruction = 200
            self._set_hnsw_efsearch(self.index, 128)
        else:
            self.index = faiss.IndexFlatIP(self.hybrid_dim)

        self.index.add(arr)  # type: ignore

        logger.info(f"✅ Built vector index items={int(self.index.ntotal) if self.index else 0}")

        if save_after_build:
            self.save_vector_index()
            self.save_scaler()

    # -------------------------------
    # SEMANTIC SEARCH (SINGLE INDEX)
    # -------------------------------
    async def semantic_search_async(
        self,
        query: str,
        k: int = 20,
        offset: int = 0,
        candidate_pool: int = 500,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[str], int]:
        """
        Async version of semantic_search.
        Non-blocking for use in async endpoints.
        """
        if not self.index:
            logger.warning("Vector index not built")
            return [], 0

        try:
            offset = max(0, int(offset))
            k = max(1, int(k))

            query_text = self._build_query_text(query)
            if not query_text:
                return [], 0

            # Use async embedding generation
            q_text = await self.generate_embedding_async(query_text)
            q_text = q_text.astype("float32")
            
            if q_text.shape[0] != self.embedding_dim:
                logger.error(f"Query embedding dim mismatch got={q_text.shape[0]} expected={self.embedding_dim}")
                return [], 0

            q_num = self._query_numeric_features(user_context)
            qvec = np.concatenate([q_text, q_num]).astype("float32")
            if qvec.shape[0] != self.hybrid_dim:
                logger.error(f"Query hybrid dim mismatch got={qvec.shape[0]} expected={self.hybrid_dim}")
                return [], 0

            qvec2d = np.array([qvec], dtype="float32")
            faiss.normalize_L2(qvec2d)

            self._set_hnsw_efsearch(self.index, 128)

            total = int(self.index.ntotal)
            if total <= 0:
                return [], 0

            fetch = min(max(candidate_pool, k + offset), total)

            # FAISS search with metrics tracking
            faiss_start = time.perf_counter()
            faiss_error = None
            try:
                D, I = self.index.search(qvec2d, fetch)  # type: ignore
            except Exception as e:
                faiss_error = type(e).__name__
                raise
            finally:
                faiss_duration_ms = (time.perf_counter() - faiss_start) * 1000
                m = _get_metrics()
                if m:
                    m.record_dependency("faiss", faiss_duration_ms, faiss_error)

            results: List[str] = []
            for raw_score, idx in zip(D[0], I[0]):
                i = int(idx)
                if i < 0:
                    continue

                sim = self._score_to_cosine_from_index(self.index, float(raw_score))
                if sim < SEMANTIC_MIN_SIMILARITY:
                    continue

                item_id = self.index_to_item_id.get(i)
                if item_id:
                    results.append(item_id)

            total_after = len(results)
            if total_after == 0:
                return [], 0

            if offset >= total_after:
                return [], total_after

            return results[offset: offset + k], total_after

        except Exception as e:
            logger.error(f"Async semantic search failed: {e}")
            return [], 0
        
    # -------------------------------
    # SIMILAR ITEMS
    # -------------------------------
    def find_similar_items(self, item_id: str, k: int = 10) -> List[Tuple[str, float]]:
        if not self.index or item_id not in self.item_id_to_index:
            return []

        try:
            item_idx = self.item_id_to_index[item_id]
            item_vector = self.index.reconstruct(item_idx).reshape(1, -1)  # type: ignore

            self._set_hnsw_efsearch(self.index, 128)

            distances, indices = self.index.search(item_vector, k + 1)  # type: ignore

            similar_items: List[Tuple[str, float]] = []
            for raw_score, idx in zip(distances[0], indices[0]):
                i = int(idx)
                if i < 0 or i == int(item_idx):
                    continue

                other_id = self.index_to_item_id.get(i)
                if not other_id:
                    continue

                sim = self._score_to_cosine_from_index(self.index, float(raw_score))
                similar_items.append((other_id, sim))

            return similar_items[:k]

        except Exception as e:
            logger.error(f"Find similar items failed: {e}")
            return []

    # -------------------------------
    # PERSISTENCE
    # -------------------------------
    def save_vector_index(self) -> None:
        if DISALLOW_INDEX_WRITE:
            logger.error("❌ Attempted to save FAISS index while DISALLOW_INDEX_WRITE=true")
            raise RuntimeError("Cannot save FAISS index in this environment")

        if not self.index:
            logger.warning("No vector index to save")
            return

        try:
            faiss.write_index(self.index, os.path.join(self.model_dir, self._INDEX_FILE))

            mappings = {
                "index_to_item_id": self.index_to_item_id,
                "item_id_to_index": self.item_id_to_index,
                "timestamp": datetime.now().isoformat(),
                "total_items": int(self.index.ntotal),
                "metric_type": getattr(self.index, "metric_type", None),
                "dimension": int(getattr(self.index, "d", 0) or 0),
            }

            mappings_path = os.path.join(self.model_dir, self._MAPPINGS_FILE)
            with open(mappings_path, "w", encoding="utf-8") as f:
                json.dump(mappings, f, indent=2)

            logger.info(f"Saved vector index items={int(self.index.ntotal)}")

        except Exception as e:
            logger.error(f"Failed to save FAISS index: {e}")

    def save_scaler(self) -> None:
        if DISALLOW_INDEX_WRITE:
            logger.error("❌ Attempted to save scaler while DISALLOW_INDEX_WRITE=true")
            raise RuntimeError("Cannot save scaler in this environment")

        if not self.scaler_fitted:
            logger.warning("Scaler not fitted; not saving")
            return

        try:
            scaler_path = os.path.join(self.model_dir, self._SCALER_FILE)
            joblib.dump(self.scaler, scaler_path)
            logger.info("✅ Saved feature scaler")
        except Exception as e:
            logger.error(f"Failed to save feature scaler: {e}")

    def load_scaler(self) -> bool:
        scaler_path = os.path.join(self.model_dir, self._SCALER_FILE)
        if not os.path.exists(scaler_path):
            return False

        try:
            self.scaler = joblib.load(scaler_path)
            self.scaler_fitted = True
            logger.info("✅ Loaded feature scaler")
            return True
        except Exception as e:
            logger.error(f"Failed to load feature scaler: {e}")
            self.scaler_fitted = False
            return False

    def load_vector_index(self) -> bool:
        index_path = os.path.join(self.model_dir, self._INDEX_FILE)
        mappings_path = os.path.join(self.model_dir, self._MAPPINGS_FILE)

        if not os.path.exists(index_path) or not os.path.exists(mappings_path):
            logger.info("No saved indexes found")
            return False

        try:
            self.index = faiss.read_index(index_path)
            self._set_hnsw_efsearch(self.index, 128)

            # ✅ dimension check (prevents silently loading old 3072 index)
            d = int(getattr(self.index, "d", 0) or 0)
            if d != self.hybrid_dim:
                logger.error(
                    f"❌ FAISS index dimension mismatch: index.d={d} expected={self.hybrid_dim}. "
                    "Rebuild the index after enabling numeric features."
                )
                self.index = None
                return False

            with open(mappings_path, "r", encoding="utf-8") as f:
                mappings = json.load(f)

            index_to_item_id_raw = mappings.get("index_to_item_id")
            item_id_to_index_raw = mappings.get("item_id_to_index")

            if isinstance(index_to_item_id_raw, dict) and isinstance(item_id_to_index_raw, dict):
                self.index_to_item_id = {int(k): v for k, v in index_to_item_id_raw.items()}
                self.item_id_to_index = dict(item_id_to_index_raw)
                schema = "single-index"
            else:
                title_map_raw = mappings.get("title_index_to_item_id") or {}
                title_rev_raw = mappings.get("title_item_id_to_index") or {}

                if not isinstance(title_map_raw, dict) or not isinstance(title_rev_raw, dict):
                    logger.error(
                        "Mappings file schema is unknown. Expected either "
                        "(index_to_item_id/item_id_to_index) or "
                        "(title_index_to_item_id/title_item_id_to_index)."
                    )
                    return False

                self.index_to_item_id = {int(k): v for k, v in title_map_raw.items()}
                self.item_id_to_index = dict(title_rev_raw)
                schema = "cascade-title-fallback"

                logger.warning(
                    "⚠️ Loaded legacy cascade mappings (title_*). "
                    "Semantic search will work, but consider rebuilding single-index later."
                )

            logger.warning(
                f"✅ Loaded FAISS index ntotal={int(getattr(self.index, 'ntotal', 0) or 0)} "
                f"metric_type={getattr(self.index, 'metric_type', None)} "
                f"dimension={d} mappings={len(self.index_to_item_id)} schema={schema}"
            )

            if not self.index_to_item_id:
                logger.error("❌ Mappings are empty after load; cannot map FAISS ids -> item ids.")
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to load FAISS index: {e}")
            return False

    # -------------------------------
    # MODEL LOADING (NCF)
    # -------------------------------
    def load_models(self) -> None:
        try:
            ncf_path = os.path.join(self.model_dir, "ncf_final.pth")
            if os.path.exists(ncf_path):
                checkpoint = torch.load(ncf_path, map_location=torch.device("cpu"))
                self.ncf_model = NeuralCollaborativeFiltering(
                    checkpoint["num_users"],
                    checkpoint["num_items"],
                    checkpoint.get("embedding_dim", 50),
                )
                self.ncf_model.load_state_dict(checkpoint["model_state_dict"])
                self.ncf_model.eval()
                logger.info("Loaded NCF model")
        except Exception as e:
            logger.error(f"Error loading NCF model: {e}")

    def load_all_models(self) -> None:
        index_loaded = self.load_vector_index()
        scaler_loaded = self.load_scaler()
        self.load_models()

        if index_loaded and not scaler_loaded:
            logger.error("CRITICAL: Index loaded but scaler missing - numeric features will be incorrect")
            raise RuntimeError("Scaler required when index present")

        if index_loaded:
            logger.info("All models loaded successfully")
        else:
            logger.info("No pre-built index found. Build offline to enable semantic search.")

    # -------------------------------
    # EMBEDDING CACHE MANAGEMENT
    # -------------------------------
    def get_embedding_cache_stats(self) -> Dict[str, Any]:
        """Get embedding cache statistics."""
        redis = get_redis_client()
        if not redis:
            return {
                "enabled": EMBEDDING_CACHE_ENABLED,
                "connected": False,
                "ttl_seconds": EMBEDDING_CACHE_TTL,
            }
        
        try:
            cursor = 0
            count = 0
            while True:
                cursor, keys = redis.scan(cursor, match=f"{EMBEDDING_CACHE_PREFIX}*", count=100)
                count += len(keys)
                if cursor == 0:
                    break
            
            return {
                "enabled": EMBEDDING_CACHE_ENABLED,
                "connected": True,
                "cached_embeddings": count,
                "ttl_seconds": EMBEDDING_CACHE_TTL,
                "embedding_dim": self.embedding_dim,
                "bytes_per_embedding": self.embedding_dim * 4,
                "estimated_cache_mb": round(count * self.embedding_dim * 4 / (1024 * 1024), 2),
            }
        except Exception as e:
            return {"enabled": EMBEDDING_CACHE_ENABLED, "connected": False, "error": str(e)}
    
    def clear_embedding_cache(self) -> Dict[str, Any]:
        """Clear all cached embeddings."""
        redis = get_redis_client()
        if not redis:
            return {"cleared": False, "error": "Redis not connected"}
        
        try:
            cursor = 0
            deleted = 0
            while True:
                cursor, keys = redis.scan(cursor, match=f"{EMBEDDING_CACHE_PREFIX}*", count=100)
                if keys:
                    deleted += redis.delete(*keys)
                if cursor == 0:
                    break
            
            return {"cleared": True, "entries_deleted": deleted}
        except Exception as e:
            return {"cleared": False, "error": str(e)}


class NeuralCollaborativeFiltering(nn.Module):
    """Deep learning model for collaborative filtering."""

    def __init__(self, num_users, num_items, embedding_dim=50):
        super().__init__()

        self.user_embedding = nn.Embedding(num_users, embedding_dim)
        self.item_embedding = nn.Embedding(num_items, embedding_dim)

        self.fc_layers = nn.Sequential(
            nn.Linear(embedding_dim * 2, 128),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, user_ids, item_ids):
        user_embeds = self.user_embedding(user_ids)
        item_embeds = self.item_embedding(item_ids)
        concat = torch.cat([user_embeds, item_embeds], dim=1)
        output = self.fc_layers(concat)
        return output * 5


class DeepLearningRecommender:
    """Advanced DL-based recommendation system."""

    def __init__(self):
        self.model = None
        self.user_to_idx: Dict[str, int] = {}
        self.item_to_idx: Dict[str, int] = {}
        self.model_dir = "ml_models/"

        self.load_encoders()
        self.load_model()

    def load_encoders(self):
        try:
            user_encoder_path = os.path.join(self.model_dir, "user_encoder.pkl")
            item_encoder_path = os.path.join(self.model_dir, "item_encoder.pkl")

            if os.path.exists(user_encoder_path):
                user_encoder = joblib.load(user_encoder_path)
                self.user_to_idx = {user: idx for idx, user in enumerate(user_encoder.classes_)}
                logger.info("Loaded user encoder")

            if os.path.exists(item_encoder_path):
                item_encoder = joblib.load(item_encoder_path)
                self.item_to_idx = {item: idx for idx, item in enumerate(item_encoder.classes_)}
                logger.info("Loaded item encoder")
        except Exception as e:
            logger.error(f"Error loading encoders: {e}")

    def load_model(self):
        try:
            model_path = os.path.join(self.model_dir, "ncf_final.pth")
            if os.path.exists(model_path):
                checkpoint = torch.load(model_path, map_location=torch.device("cpu"))
                self.model = NeuralCollaborativeFiltering(
                    checkpoint["num_users"],
                    checkpoint["num_items"],
                    checkpoint.get("embedding_dim", 50),
                )
                self.model.load_state_dict(checkpoint["model_state_dict"])
                self.model.eval()
                logger.info("Loaded deep learning model")
        except Exception as e:
            logger.error(f"Error loading model: {e}")

    def predict(self, user_id: str, item_ids: List[str]) -> Dict[str, float]:
        if not self.model:
            return {}

        predictions: Dict[str, float] = {}
        user_idx = self.user_to_idx.get(user_id)
        if user_idx is None:
            return {}

        for item_id in item_ids:
            item_idx = self.item_to_idx.get(item_id)
            if item_idx is None:
                continue

            with torch.no_grad():
                user_tensor = torch.tensor([user_idx])
                item_tensor = torch.tensor([item_idx])
                pred = self.model(user_tensor, item_tensor)
                predictions[item_id] = float(pred.item())

        return predictions
