#!/usr/bin/env python3
# run_training.py - Fully Dynamic Adaptive Training Pipeline

import asyncio
import sys
import os
import logging
import json
import warnings
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
import pandas as pd
import numpy as np
from pathlib import Path
import joblib

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore')

# ML Libraries
from sklearn.model_selection import train_test_split, KFold
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.decomposition import TruncatedSVD
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.feature_extraction.text import TfidfVectorizer
from scipy.sparse import csr_matrix, hstack
from scipy.sparse.linalg import svds

# Collaborative Filtering
try:
    from implicit.als import AlternatingLeastSquares
    from implicit.bpr import BayesianPersonalizedRanking
    from implicit.nearest_neighbours import CosineRecommender
    IMPLICIT_AVAILABLE = True
except ImportError:
    IMPLICIT_AVAILABLE = False
    warnings.warn("Implicit library not available. Some collaborative filtering methods will be skipped.")

# Deep Learning (optional)
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset
    DEEP_LEARNING_AVAILABLE = True
except ImportError:
    DEEP_LEARNING_AVAILABLE = False

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Item, User, Interaction
from database import get_db
from sqlalchemy import func

"""Logging configured without Unicode symbols for Windows compatibility."""

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Model directory
MODEL_DIR = Path("./ml_models")
MODEL_DIR.mkdir(exist_ok=True)


class DynamicConfig:
    """Dynamic configuration based on available data."""
    
    @staticmethod
    def get_config(n_users: int, n_items: int, n_interactions: int) -> Dict[str, Any]:
        """Generate optimal configuration based on data size."""
        
        config = {
            "random_state": 42,
            "content_weight": 0.3,
            "collab_weight": 0.7,
        }
        
        # Determine data size category
        data_size = DynamicConfig._categorize_data_size(n_users, n_items, n_interactions)
        
        if data_size == "minimal":
            # Very small data (< 10 users or < 20 items)
            config.update({
                "test_size": 0.1,  # Small test set to preserve training data
                "n_splits": min(2, n_users),  # Minimal cross-validation
                "svd_components": min(3, n_users - 1, n_items - 1),
                "als_factors": min(5, n_users, n_items),
                "als_iterations": 5,
                "als_regularization": 0.5,  # High regularization to prevent overfitting
                "tfidf_features": min(50, n_items),
                "min_interactions_per_user": 1,
                "min_interactions_per_item": 1,
            })
            
        elif data_size == "small":
            # Small data (10-50 users or 20-100 items)
            config.update({
                "test_size": 0.15,
                "n_splits": min(3, n_users),
                "svd_components": min(10, n_users - 1, n_items - 1),
                "als_factors": min(20, n_users, n_items),
                "als_iterations": 10,
                "als_regularization": 0.1,
                "tfidf_features": min(100, n_items * 2),
                "min_interactions_per_user": 2,
                "min_interactions_per_item": 2,
            })
            
        elif data_size == "medium":
            # Medium data (50-500 users or 100-1000 items)
            config.update({
                "test_size": 0.2,
                "n_splits": 5,
                "svd_components": min(50, n_users - 1, n_items - 1),
                "als_factors": min(50, n_users, n_items),
                "als_iterations": 15,
                "als_regularization": 0.05,
                "tfidf_features": 500,
                "min_interactions_per_user": 3,
                "min_interactions_per_item": 3,
            })
            
        else:  # large
            # Large data (500+ users and 1000+ items)
            config.update({
                "test_size": 0.2,
                "n_splits": 10,
                "svd_components": 100,
                "als_factors": 100,
                "als_iterations": 20,
                "als_regularization": 0.01,
                "tfidf_features": 1000,
                "min_interactions_per_user": 5,
                "min_interactions_per_item": 5,
            })
        
        return config
    
    @staticmethod
    def _categorize_data_size(n_users: int, n_items: int, n_interactions: int) -> str:
        """Categorize data size."""
        if n_users < 10 or n_items < 20 or n_interactions < 50:
            return "minimal"
        elif n_users < 50 or n_items < 100 or n_interactions < 500:
            return "small"
        elif n_users < 500 or n_items < 1000 or n_interactions < 5000:
            return "medium"
        else:
            return "large"
    
    @staticmethod
    def get_viable_algorithms(n_users: int, n_items: int, n_interactions: int) -> Dict[str, bool]:
        """Determine which algorithms can work with the data."""
        return {
            "svd": n_users >= 2 and n_items >= 2 and n_interactions >= 5,
            "als": IMPLICIT_AVAILABLE and n_users >= 3 and n_items >= 3 and n_interactions >= 10,
            "bpr": IMPLICIT_AVAILABLE and n_users >= 3 and n_items >= 3 and n_interactions >= 10,
            "cosine": IMPLICIT_AVAILABLE and n_items >= 3,
            "content": n_items >= 3,
            "popularity": n_interactions >= 1,
            "deep_learning": DEEP_LEARNING_AVAILABLE and n_users >= 10 and n_items >= 10 and n_interactions >= 100,
        }


class AdaptiveDataset:
    """Dataset that adapts to any amount of data."""
    
    def __init__(self, db):
        self.db = db
        self.users = pd.DataFrame()
        self.items = pd.DataFrame()
        self.interactions = pd.DataFrame()
        self.config = {}
        self.viable_algorithms = {}
        
    def load_and_validate(self) -> bool:
        """Load data and validate if training is possible."""
        logger.info("Loading data from database...")
        
        # Load users
        users_data = self.db.query(User).all()
        if users_data:
            self.users = pd.DataFrame([
                {"user_id": u.id, "username": u.username, "email": u.email}
                for u in users_data
            ])
        else:
            logger.error("No users found in database!")
            return False
        
        # Load items
        items_data = self.db.query(Item).all()
        if items_data:
            self.items = pd.DataFrame([
                {
                    "item_id": i.id,
                    "title": i.title or f"Item {i.id}",
                    "category": i.category or "unknown",
                    "price": i.price or 0.0,
                    "in_stock": i.in_stock,
                    "product_rating": getattr(i, 'product_rating', 0.0) or 0.0
                }
                for i in items_data
            ])
        else:
            logger.error("No items found in database!")
            return False
        
        # Load interactions
        interactions_data = self.db.query(Interaction).all()
        if interactions_data:
            self.interactions = pd.DataFrame([
                {
                    "user_id": i.user_id,
                    "item_id": i.item_id,
                    "interaction_type": i.interaction_type,
                    "rating": i.rating or 1.0,
                    "timestamp": i.created_at
                }
                for i in interactions_data
            ])
        else:
            logger.warning("No interactions found. Creating synthetic interactions...")
            self._create_synthetic_interactions()
        
        # Get statistics
        n_users = len(self.users)
        n_items = len(self.items)
        n_interactions = len(self.interactions)
        
        logger.info(f"Data loaded successfully:")
        logger.info(f"  Users: {n_users}")
        logger.info(f"  Items: {n_items}")
        logger.info(f"  Interactions: {n_interactions}")
        
        if n_interactions > 0:
            logger.info(f"  Avg interactions/user: {n_interactions/n_users:.2f}")
            logger.info(f"  Avg interactions/item: {n_interactions/n_items:.2f}")
            logger.info(f"  Sparsity: {1 - n_interactions/(n_users * n_items):.2%}")
        
        # Get dynamic configuration
        self.config = DynamicConfig.get_config(n_users, n_items, n_interactions)
        self.viable_algorithms = DynamicConfig.get_viable_algorithms(n_users, n_items, n_interactions)
        
        # Log viable algorithms
        logger.info("Viable algorithms for this data:")
        for algo, viable in self.viable_algorithms.items():
            logger.info(f"  {algo}: {'OK' if viable else 'FAIL'}")
        
        # Check if any algorithm can work
        if not any(self.viable_algorithms.values()):
            logger.error("Data is insufficient for any algorithm!")
            return False
        
        return True
    
    def _create_synthetic_interactions(self):
        """Create minimal synthetic interactions for cold start."""
        logger.info("Creating synthetic interactions for cold start...")
        
        synthetic_interactions = []
        
        # Create at least one interaction per user
        for user_id in self.users['user_id'].head(min(5, len(self.users))):
            # Random item for each user
            item_id = np.random.choice(self.items['item_id'])
            synthetic_interactions.append({
                'user_id': user_id,
                'item_id': item_id,
                'interaction_type': 'view',
                'rating': 3.0,
                'timestamp': datetime.now()
            })
        
        self.interactions = pd.DataFrame(synthetic_interactions)
        logger.info(f"Created {len(synthetic_interactions)} synthetic interactions")
    
    def create_features(self) -> Tuple[Optional[csr_matrix], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """Create feature matrices adaptively."""
        if len(self.interactions) == 0:
            return None, self.users, self.items
        
        # User features
        user_features = self.interactions.groupby('user_id').agg({
            'rating': ['mean', 'count'],
            'interaction_type': lambda x: x.mode()[0] if len(x) > 0 else 'view'
        }).reset_index()
        user_features.columns = ['user_id', 'avg_rating', 'n_interactions', 'preferred_type']
        
        self.user_features = self.users.merge(user_features, on='user_id', how='left')
        self.user_features.fillna({'avg_rating': 0, 'n_interactions': 0, 'preferred_type': 'none'}, inplace=True)
        
        # Item features
        item_features = self.interactions.groupby('item_id').agg({
            'rating': ['mean', 'count'],
            'user_id': 'nunique'
        }).reset_index()
        item_features.columns = ['item_id', 'avg_user_rating', 'n_ratings', 'n_unique_users']
        
        self.item_features = self.items.merge(item_features, on='item_id', how='left')
        self.item_features.fillna(0, inplace=True)
        
        # Create interaction matrix
        interaction_matrix = self._create_interaction_matrix()
        
        return interaction_matrix, self.user_features, self.item_features
    
    def _create_interaction_matrix(self) -> Optional[csr_matrix]:
        """Create sparse interaction matrix."""
        if len(self.interactions) == 0:
            return None
        
        # Create mappings
        self.user_to_idx = {uid: idx for idx, uid in enumerate(self.users['user_id'].unique())}
        self.item_to_idx = {iid: idx for idx, iid in enumerate(self.items['item_id'].unique())}
        self.idx_to_user = {v: k for k, v in self.user_to_idx.items()}
        self.idx_to_item = {v: k for k, v in self.item_to_idx.items()}
        
        # Create sparse matrix
        rows = []
        cols = []
        data = []
        
        for _, interaction in self.interactions.iterrows():
            if interaction['user_id'] in self.user_to_idx and interaction['item_id'] in self.item_to_idx:
                rows.append(self.user_to_idx[interaction['user_id']])
                cols.append(self.item_to_idx[interaction['item_id']])
                data.append(interaction['rating'])
        
        if not rows:
            return None
        
        matrix = csr_matrix(
            (data, (rows, cols)),
            shape=(len(self.user_to_idx), len(self.item_to_idx))
        )
        
        return matrix


class AdaptiveCollaborativeModel:
    """Collaborative filtering that adapts to data size."""
    
    def __init__(self, config: Dict[str, Any], viable_algorithms: Dict[str, bool]):
        self.config = config
        self.viable = viable_algorithms
        self.models = {}
        
    def train(self, interaction_matrix: Optional[csr_matrix], n_users: int, n_items: int):
        """Train only viable collaborative models."""
        if interaction_matrix is None:
            logger.warning("No interaction matrix available for collaborative filtering")
            return
        
        # Simple SVD (always try if matrix exists)
        if self.viable.get('svd', False) and interaction_matrix.nnz > 0:
            try:
                logger.info("Training SVD model...")
                k = min(self.config['svd_components'], 
                       min(interaction_matrix.shape) - 1, 
                       interaction_matrix.nnz // 2)
                
                if k > 0:
                    U, sigma, Vt = svds(interaction_matrix.asfptype(), k=k)
                    self.models['svd'] = {
                        'U': U,
                        'sigma': sigma,
                        'Vt': Vt,
                        'predicted_ratings': U @ np.diag(sigma) @ Vt
                    }
                    logger.info(f"  SVD trained with {k} components")
            except Exception as e:
                logger.warning(f"  SVD training failed: {e}")
        
        # ALS (if available and viable)
        if self.viable.get('als', False) and IMPLICIT_AVAILABLE:
            try:
                logger.info("Training ALS model...")
                self.models['als'] = AlternatingLeastSquares(
                    factors=self.config['als_factors'],
                    iterations=self.config['als_iterations'],
                    regularization=self.config['als_regularization'],
                    random_state=self.config['random_state']
                )
                # Convert to implicit format (positive values only)
                implicit_matrix = interaction_matrix.copy()
                implicit_matrix.data = np.ones_like(implicit_matrix.data)
                self.models['als'].fit(implicit_matrix.T)
                logger.info("  ALS trained successfully")
            except Exception as e:
                logger.warning(f"  ALS training failed: {e}")
        
        # Cosine similarity (simple, works with small data)
        if n_items >= 2:
            try:
                logger.info("Training cosine similarity model...")
                # Simple item-item similarity
                item_matrix = interaction_matrix.T
                norms = np.sqrt((item_matrix.multiply(item_matrix)).sum(axis=1))
                norms[norms == 0] = 1  # Avoid division by zero
                self.models['cosine'] = {
                    'item_matrix': item_matrix,
                    'norms': norms
                }
                logger.info("  Cosine similarity trained")
            except Exception as e:
                logger.warning(f"  Cosine training failed: {e}")
    
    def save(self, path: Path):
        """Save trained models."""
        if self.models:
            joblib.dump(self.models, path / 'collaborative_models.pkl')
            logger.info(f"Saved {len(self.models)} collaborative models")


class AdaptiveContentModel:
    """Content-based model that adapts to data."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.models = {}
        
    def train(self, item_features: pd.DataFrame):
        """Train content-based model."""
        if len(item_features) < 2:
            logger.warning("Not enough items for content-based filtering")
            return
        
        try:
            logger.info("Training content-based model...")
            
            # TF-IDF on titles (with adaptive features)
            max_features = min(self.config.get('tfidf_features', 100), len(item_features) * 2)
            self.models['tfidf'] = TfidfVectorizer(
                max_features=max_features,
                stop_words='english',
                ngram_range=(1, 2)
            )
            title_features = self.models['tfidf'].fit_transform(item_features['title'].fillna(''))
            
            # Category encoding
            self.models['category_encoder'] = LabelEncoder()
            categories = item_features['category'].fillna('unknown')
            category_encoded = self.models['category_encoder'].fit_transform(categories)
            
            # Numerical features (normalized)
            numerical_cols = ['price', 'product_rating', 'avg_user_rating']
            available_cols = [col for col in numerical_cols if col in item_features.columns]
            
            if available_cols:
                scaler = StandardScaler()
                numerical_features = scaler.fit_transform(item_features[available_cols].fillna(0))
                self.models['scaler'] = scaler
            else:
                numerical_features = np.zeros((len(item_features), 1))
            
            # Combine all features
            self.models['item_profiles'] = hstack([
                title_features,
                csr_matrix(category_encoded.reshape(-1, 1)),
                csr_matrix(numerical_features)
            ])
            
            logger.info(f"  Content model trained with {self.models['item_profiles'].shape[1]} features")
            
        except Exception as e:
            logger.warning(f"Content model training failed: {e}")
    
    def save(self, path: Path):
        """Save model."""
        if self.models:
            joblib.dump(self.models, path / 'content_model.pkl')
            logger.info("Saved content-based model")


class PopularityModel:
    """Simple popularity-based fallback model."""
    
    def __init__(self):
        self.popular_items = []
        
    def train(self, interactions: pd.DataFrame, items: pd.DataFrame):
        """Train popularity model."""
        logger.info("Training popularity model...")
        
        if len(interactions) > 0:
            # Based on interactions
            item_popularity = interactions.groupby('item_id').agg({
                'rating': ['mean', 'count']
            }).reset_index()
            item_popularity.columns = ['item_id', 'avg_rating', 'interaction_count']
            
            # Calculate popularity score
            item_popularity['popularity_score'] = (
                item_popularity['avg_rating'] * 0.3 + 
                np.log1p(item_popularity['interaction_count']) * 0.7
            )
            
            # Merge with items
            popular = items.merge(item_popularity[['item_id', 'popularity_score']], 
                                 on='item_id', how='left')
            popular['popularity_score'] = popular['popularity_score'].fillna(0)
            
            # Sort by popularity
            self.popular_items = popular.sort_values('popularity_score', ascending=False)['item_id'].tolist()
        else:
            # Random popularity for cold start
            self.popular_items = items['item_id'].tolist()
            np.random.shuffle(self.popular_items)
        
        logger.info(f"  Popularity model trained with {len(self.popular_items)} items")
    
    def save(self, path: Path):
        """Save model."""
        joblib.dump({'popular_items': self.popular_items}, path / 'popularity_model.pkl')
        logger.info("Saved popularity model")


async def train_ml_models(db):
    """Main adaptive training pipeline."""
    
    logger.info("="*60)
    logger.info("ADAPTIVE ML TRAINING PIPELINE")
    logger.info("="*60)
    
    # Initialize dataset
    dataset = AdaptiveDataset(db)
    
    # Load and validate data
    if not dataset.load_and_validate():
        logger.error("Data validation failed. Cannot proceed with training.")
        return False
    
    # Create features
    interaction_matrix, user_features, item_features = dataset.create_features()
    
    # Track what we're training
    models_trained = []
    training_warnings = []
    
    # Always train popularity model (works with any data)
    popularity_model = PopularityModel()
    popularity_model.train(dataset.interactions, dataset.items)
    popularity_model.save(MODEL_DIR)
    models_trained.append("popularity")
    
    # Train content-based if viable
    if dataset.viable_algorithms.get('content', False) and item_features is not None:
        content_model = AdaptiveContentModel(dataset.config)
        content_model.train(item_features)
        content_model.save(MODEL_DIR)
        models_trained.append("content")
    
    # Train collaborative if viable
    if any([dataset.viable_algorithms.get(algo, False) 
            for algo in ['svd', 'als', 'bpr', 'cosine']]):
        collab_model = AdaptiveCollaborativeModel(dataset.config, dataset.viable_algorithms)
        collab_model.train(interaction_matrix, len(dataset.users), len(dataset.items))
        collab_model.save(MODEL_DIR)
        if collab_model.models:
            models_trained.extend(collab_model.models.keys())
    
    # Generate warnings based on data quality
    n_users = len(dataset.users)
    n_items = len(dataset.items)
    n_interactions = len(dataset.interactions)
    
    if n_users < 10:
        training_warnings.append(f"Only {n_users} users. Recommendations may not be diverse.")
    if n_items < 50:
        training_warnings.append(f"Only {n_items} items. Consider adding more products.")
    if n_interactions < 100:
        training_warnings.append(f"Only {n_interactions} interactions. Models need more data for accuracy.")
    
    avg_interactions_per_user = n_interactions / n_users if n_users > 0 else 0
    if avg_interactions_per_user < 5:
        training_warnings.append(f"Low user engagement ({avg_interactions_per_user:.1f} interactions/user).")
    
    # Save metadata
    metadata = {
        'timestamp': datetime.now().isoformat(),
        'data_stats': {
            'n_users': n_users,
            'n_items': n_items,
            'n_interactions': n_interactions,
            'avg_interactions_per_user': avg_interactions_per_user,
            'avg_interactions_per_item': n_interactions / n_items if n_items > 0 else 0,
            'sparsity': 1 - n_interactions / (n_users * n_items) if n_users * n_items > 0 else 1.0
        },
        'models_trained': models_trained,
        'config_used': dataset.config,
        'viable_algorithms': dataset.viable_algorithms,
        'warnings': training_warnings,
        'status': 'success' if models_trained else 'failed'
    }
    
    with open(MODEL_DIR / 'training_metadata.json', 'w') as f:
        json.dump(metadata, f, indent=2)
    
    # Save mappings
    if hasattr(dataset, 'user_to_idx'):
        mappings = {
            'user_to_idx': dataset.user_to_idx,
            'idx_to_user': dataset.idx_to_user,
            'item_to_idx': dataset.item_to_idx,
            'idx_to_item': dataset.idx_to_item
        }
        joblib.dump(mappings, MODEL_DIR / 'mappings.pkl')
    
    # Log summary
    logger.info("="*60)
    logger.info("TRAINING SUMMARY")
    logger.info("="*60)
    logger.info(f"Models trained: {', '.join(models_trained) if models_trained else 'None'}")
    
    if training_warnings:
        logger.warning("Data quality warnings:")
        for warning in training_warnings:
                logger.warning(f"  WARNING: {warning}")

    if models_trained:
        logger.info(f"Training successful! Models saved to {MODEL_DIR}")
        logger.info("The system will work with your current data and improve as more data is collected.")
    else:
        logger.error("No models could be trained.")
    
    logger.info("="*60)
    
    return len(models_trained) > 0


async def run_training():
    """Run the training pipeline."""
    
    # Setup logging to file
    log_file = MODEL_DIR / f"training_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    
    # Get database session
    db = next(get_db())
    
    try:
        # Run training
        success = await train_ml_models(db)
        
        if success:
            logger.info("Training pipeline completed successfully!")

            # Validate saved models
            try:
                metadata = json.load(open(MODEL_DIR / 'training_metadata.json'))
                logger.info(f"Validated metadata: {len(metadata['models_trained'])} models trained")

                if Path(MODEL_DIR / 'popularity_model.pkl').exists():
                    logger.info("Popularity model exists")
                if Path(MODEL_DIR / 'content_model.pkl').exists():
                    logger.info("Content model exists")
                if Path(MODEL_DIR / 'collaborative_models.pkl').exists():
                    logger.info("Collaborative models exist")

            except Exception as e:
                logger.error(f"Model validation failed: {e}")
        else:
            logger.error("Training pipeline failed!")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Training failed with error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run_training())