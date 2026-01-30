# deploy.py
class ProductionDeployment:
    def deploy_new_model(self, model_path, canary_percentage=5):
        """Safe production deployment."""
        
        # 1. Validate model
        if not self.validate_model(model_path):
            raise ValueError("Model validation failed")
        
        # 2. Shadow mode first
        self.deploy_shadow_mode(model_path, duration_hours=24)
        
        # 3. Check shadow metrics
        if not self.check_shadow_metrics():
            raise ValueError("Shadow metrics below threshold")
        
        # 4. Canary deployment
        self.deploy_canary(model_path, canary_percentage)
        
        # 5. Monitor and auto-rollback
        self.monitor_with_rollback(threshold_error_rate=0.01)
        
        # 6. Gradual rollout
        for percentage in [10, 25, 50, 100]:
            self.increase_traffic(percentage)
            time.sleep(3600)  # Wait 1 hour between increases
            if not self.check_metrics():
                self.rollback()
                break