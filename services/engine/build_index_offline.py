#!/usr/bin/env python3
# build_index_offline.py

import sys
import os
from dotenv import load_dotenv
import logging

# Load .env so OPENAI_API_KEY / DISALLOW_INDEX_WRITE are available
load_dotenv()

# Add services/engine to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db
from models import Item
from ml_engine import MLRecommendationEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_index():
    """Build FAISS vector index offline using OpenAI embeddings."""
    db = next(get_db())

    try:
        logger.info("Loading items from database...")
        items = (
            db.query(Item)
            .filter(Item.product_status == "active")
            .all()
        )

        logger.info(f"Found {len(items)} active items")

        if not items:
            logger.warning("No active items found. Skipping index build.")
            return

        items_dict = []
        for item in items:
            items_dict.append({
                "id": item.id,
                "title": item.title or "",
                "description": item.description or "",
                "category": item.category or "",
                "brand": item.brand or "",
                "price": item.price,
                "product_rating": getattr(item, "product_rating", 0),
                "popularity_score": getattr(item, "popularity_score", 0),
                "discount": getattr(item, "discount", 0),
                "in_stock": item.in_stock,
            })

        logger.info("Initializing MLRecommendationEngine...")
        ml_engine = MLRecommendationEngine()

        logger.info("Building vector index with OpenAI embeddings (batched)...")
        ml_engine.build_vector_index(items_dict, save_after_build=True)

        logger.info("✅ Index building completed successfully!")

    finally:
        db.close()
        logger.info("Database session closed.")


if __name__ == "__main__":
    build_index()