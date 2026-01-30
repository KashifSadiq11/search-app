# data_pipeline.py
class ProductionDataPipeline:
    def __init__(self):
        self.min_interactions_per_user = 10
        self.min_users_per_item = 5
        
    def validate_data_quality(self, db):
        """Production systems need data quality checks."""
        issues = []
        
        # Check interaction velocity
        recent_interactions = db.query(Interaction).filter(
            Interaction.created_at > datetime.now() - timedelta(days=1)
        ).count()
        
        if recent_interactions < 100:
            issues.append("Low interaction velocity")
        
        # Check data distribution
        user_dist = db.query(
            Interaction.user_id, 
            func.count(Interaction.id)
        ).group_by(Interaction.user_id).all()
        
        if np.std([count for _, count in user_dist]) > 100:
            issues.append("Highly skewed user distribution")
        
        return issues