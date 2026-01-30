# run_training_optimized.py
import pandas as pd
from sqlalchemy import create_engine
import numpy as np
from pathlib import Path
import joblib

class ScalableTraining:
    def __init__(self, db_url, batch_size=10000):
        self.engine = create_engine(db_url)
        self.batch_size = batch_size
        self.model_dir = Path("ml_models")
        
    def train_with_batching(self):
        """Train models with batch processing for 100k+ items."""
        
        # Process users in batches
        user_query = "SELECT * FROM users"
        user_chunks = []
        for chunk in pd.read_sql_query(user_query, self.engine, chunksize=1000):
            user_chunks.append(chunk)
        users_df = pd.concat(user_chunks, ignore_index=True)
        
        # Process items in batches
        item_features = []
        item_query = "SELECT * FROM items"
        
        for chunk in pd.read_sql_query(item_query, self.engine, chunksize=self.batch_size):
            # Process each batch
            features = self.extract_item_features(chunk)
            item_features.append(features)
        
        # Process interactions in batches
        interaction_matrix = self.build_interaction_matrix_chunked()
        
        # Train models
        self.train_models(interaction_matrix, item_features)
    
    def build_interaction_matrix_chunked(self):
        """Build sparse matrix in chunks to handle millions of interactions."""
        from scipy.sparse import csr_matrix, vstack
        
        query = """
            SELECT user_id, item_id, rating 
            FROM interactions 
            ORDER BY created_at
        """
        
        matrices = []
        for chunk in pd.read_sql_query(query, self.engine, chunksize=50000):
            # Convert to sparse matrix
            chunk_matrix = self.chunk_to_sparse(chunk)
            matrices.append(chunk_matrix)
        
        # Combine all chunks
        return vstack(matrices) if matrices else None
    
    def extract_item_features(self, items_chunk):
        """Extract features from item batch."""
        # TF-IDF on text, but in batches
        from sklearn.feature_extraction.text import TfidfVectorizer
        
        tfidf = TfidfVectorizer(max_features=1000, max_df=0.95, min_df=2)
        text_features = tfidf.fit_transform(items_chunk['title'].fillna(''))
        
        return {
            'text_features': text_features,
            'categories': items_chunk['category'].values,
            'prices': items_chunk['price'].values
        }