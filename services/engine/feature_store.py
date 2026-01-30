# feature_store.py
class FeatureStore:
    def __init__(self):
        self.redis_client = redis.Redis()
        self.feature_definitions = {}
        
    def compute_user_features(self, user_id):
        """Real-time user features."""
        return {
            # Behavioral
            'avg_session_duration': self.get_avg_session_duration(user_id),
            'browse_to_purchase_rate': self.get_conversion_rate(user_id),
            'category_preferences': self.get_category_distribution(user_id),
            'price_sensitivity': self.get_price_elasticity(user_id),
            
            # Temporal
            'hour_of_day_pattern': self.get_temporal_pattern(user_id),
            'days_since_last_purchase': self.get_recency(user_id),
            
            # Social
            'user_cluster': self.get_user_segment(user_id),
            'influence_score': self.get_influence_score(user_id)
        }
    
    def compute_item_features(self, item_id):
        """Real-time item features."""
        return {
            # Performance
            'ctr': self.get_click_through_rate(item_id),
            'cvr': self.get_conversion_rate(item_id),
            'return_rate': self.get_return_rate(item_id),
            
            # Trending
            'momentum_score': self.get_momentum(item_id),
            'seasonality_factor': self.get_seasonality(item_id),
            
            # Quality
            'quality_score': self.get_quality_score(item_id),
            'freshness': self.get_freshness(item_id)
        }