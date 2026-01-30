# predictive_analytics.py - Working implementation
import numpy as np
from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta
from collections import Counter, defaultdict
import logging

logger = logging.getLogger(__name__)

class PredictiveAnalytics:
    def __init__(self):
        logger.info("PredictiveAnalytics initialized")
    
    def predict_item_demand(self, 
                           interaction_history: List[Dict],
                           item_id: str,
                           days_ahead: int = 7) -> float:
        """Predict future demand using simple time series."""
        
        # Count interactions per day for this item
        daily_counts = defaultdict(int)
        for interaction in interaction_history:
            if interaction.get('item_id') == item_id:
                date = interaction.get('date', datetime.now()).date()
                daily_counts[date] += 1
        
        if not daily_counts:
            return 0.0
        
        # Simple moving average
        recent_days = sorted(daily_counts.keys())[-7:]
        avg_daily_demand = sum(daily_counts[d] for d in recent_days) / len(recent_days)
        
        # Add trend (increasing or decreasing)
        if len(recent_days) >= 3:
            first_half = sum(daily_counts[d] for d in recent_days[:3]) / 3
            second_half = sum(daily_counts[d] for d in recent_days[-3:]) / 3
            trend = (second_half - first_half) / max(first_half, 1)
        else:
            trend = 0
        
        # Predict with trend
        predicted_demand = avg_daily_demand * (1 + trend) * days_ahead
        return max(0, predicted_demand)
    
    def predict_user_lifetime_value(self, user_history: Dict) -> float:
        """Calculate CLV based on actual user data."""
        
        total_purchases = user_history.get('total_purchases', 0)
        avg_order_value = user_history.get('avg_order_value', 50)
        days_since_first = user_history.get('days_since_first_interaction', 30)
        days_since_last = user_history.get('days_since_last_interaction', 0)
        
        # Calculate purchase frequency
        if days_since_first > 0:
            purchase_frequency = total_purchases / (days_since_first / 30)  # per month
        else:
            purchase_frequency = 0
        
        # Churn probability (simple: based on recency)
        if days_since_last < 30:
            retention_rate = 0.8
        elif days_since_last < 60:
            retention_rate = 0.5
        else:
            retention_rate = 0.2
        
        # Simple CLV: AOV * Purchase Frequency * Customer Lifespan
        expected_lifespan_months = 12 * retention_rate
        clv = avg_order_value * purchase_frequency * expected_lifespan_months
        
        return max(0, clv)
    
    def detect_trending_items(self, 
                            interaction_data: List[Tuple],
                            window_days: int = 7) -> List[Dict[str, Any]]:
        """Detect items with increasing popularity."""
        
        trending = []
        
        for item_id, interaction_count in interaction_data:
            # Calculate trend score (interactions per day)
            daily_rate = interaction_count / max(window_days, 1)
            
            # Boost score for velocity (more recent = higher score)
            recency_boost = 1.0 if window_days <= 7 else 0.5
            trend_score = daily_rate * recency_boost
            
            if trend_score > 0.5:  # Threshold
                trending.append({
                    'item_id': item_id,
                    'trend_score': trend_score,
                    'interaction_count': interaction_count,
                    'daily_rate': daily_rate
                })
        
        # Sort by trend score
        trending.sort(key=lambda x: x['trend_score'], reverse=True)
        return trending[:20]  # Top 20 trending
    
    def predict_churn_probability(self, user_features: Dict) -> float:
        """Predict user churn with simple heuristics."""
        
        days_inactive = user_features.get('days_inactive', 0)
        total_interactions = user_features.get('total_interactions', 0)
        avg_rating = user_features.get('avg_rating', 3.0)
        
        # Churn score based on multiple factors
        churn_score = 0.0
        
        # Inactivity factor
        if days_inactive > 60:
            churn_score += 0.5
        elif days_inactive > 30:
            churn_score += 0.3
        elif days_inactive > 14:
            churn_score += 0.1
        
        # Engagement factor
        if total_interactions < 3:
            churn_score += 0.3
        elif total_interactions < 10:
            churn_score += 0.1
        
        # Satisfaction factor
        if avg_rating < 2.5:
            churn_score += 0.2
        elif avg_rating < 3.5:
            churn_score += 0.1
        
        return min(1.0, churn_score)
    
    def calculate_price_elasticity(self, 
                                  price_points: List[float],
                                  sales_data: List[int]) -> float:
        """Calculate how price changes affect demand."""
        
        if len(price_points) < 2 or len(sales_data) < 2:
            return -1.0  # Default elasticity
        
        # Calculate percentage changes
        price_changes = []
        quantity_changes = []
        
        for i in range(1, len(price_points)):
            if price_points[i-1] > 0 and sales_data[i-1] > 0:
                price_change = (price_points[i] - price_points[i-1]) / price_points[i-1]
                quantity_change = (sales_data[i] - sales_data[i-1]) / sales_data[i-1]
                
                if price_change != 0:
                    price_changes.append(price_change)
                    quantity_changes.append(quantity_change)
        
        if not price_changes:
            return -1.0
        
        # Average elasticity
        elasticities = [q/p for q, p in zip(quantity_changes, price_changes) if p != 0]
        return np.mean(elasticities) if elasticities else -1.0