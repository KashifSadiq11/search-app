# ab_testing.py
class ABTestingFramework:
    def __init__(self):
        self.experiments = {}
        
    def create_experiment(self, name, variants, allocation):
        """Create A/B test for models."""
        experiment = {
            'name': name,
            'variants': variants,
            'allocation': allocation,
            'metrics': ['ctr', 'cvr', 'revenue_per_user'],
            'min_sample_size': self.calculate_sample_size()
        }
        self.experiments[name] = experiment
        
    def assign_variant(self, user_id, experiment_name):
        """Deterministic variant assignment."""
        hash_value = hashlib.md5(f"{user_id}:{experiment_name}".encode()).hexdigest()
        return self.get_variant_from_hash(hash_value, self.experiments[experiment_name])