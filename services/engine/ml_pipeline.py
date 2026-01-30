# ml_pipeline.py
class ProductionMLPipeline:
    def __init__(self):
        self.models = {}
        self.feature_store = {}
        self.experiment_tracker = MLFlowTracker()
        
    def train_with_validation(self, data):
        """Production training with proper validation."""
        # Time-based split for recommendations
        train, val, test = self.temporal_split(data)
        
        # Train multiple models
        models_performance = {}
        
        # 1. Two-Tower DNN
        two_tower = self.train_two_tower(train, val)
        models_performance['two_tower'] = self.evaluate(two_tower, test)
        
        # 2. Wide & Deep
        wide_deep = self.train_wide_deep(train, val)
        models_performance['wide_deep'] = self.evaluate(wide_deep, test)
        
        # 3. Transformer-based
        transformer = self.train_transformer(train, val)
        models_performance['transformer'] = self.evaluate(transformer, test)
        
        # Select best model
        best_model = max(models_performance, key=lambda k: models_performance[k]['ndcg'])
        
        # A/B test configuration
        self.deploy_with_ab_test(best_model, challenger_ratio=0.1)
        
        return models_performance