# test_cache.py
from memory_cache import cache

# Test basic operations
cache.set('test_key', {'data': 'test_value'}, ttl=60)
print(cache.get('test_key'))  # Should print: {'data': 'test_value'}

# Check cache info
print(cache.info())  # Shows whether using Redis or memory

# Test with complex objects
cache.set('user_prefs', {'categories': ['Electronics', 'Books'], 'budget': 100})
print(cache.get('user_prefs'))