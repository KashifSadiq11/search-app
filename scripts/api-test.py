# test_api_endpoints.py
import requests

API_URL = "http://localhost:8016"

print(f"Testing API at {API_URL}...")

# Test 1: Check if API is running
try:
    health_response = requests.get(f"{API_URL}/health", timeout=5)
    print(f"✅ Health check: {health_response.status_code} - {health_response.text[:100]}")
except:
    print("❌ Health endpoint not available (this might be OK)")

# Test 2: Try to get users
try:
    users_response = requests.get(f"{API_URL}/users/", timeout=10)
    print(f"\n📡 Users endpoint: {users_response.status_code}")
    
    if users_response.status_code == 200:
        data = users_response.json()
        print(f"Response type: {type(data)}")
        
        if isinstance(data, dict):
            print(f"Keys in response: {list(data.keys())}")
            if "items" in data:
                print(f"Number of users: {len(data['items'])}")
        elif isinstance(data, list):
            print(f"Number of users: {len(data)}")
            
        # Print first user if available
        if isinstance(data, dict) and "items" in data and data["items"]:
            first_user = data["items"][0]
            print(f"\nFirst user example: {first_user}")
        elif isinstance(data, list) and data:
            print(f"\nFirst user example: {data[0]}")
            
    else:
        print(f"Response: {users_response.text[:200]}")
        
except requests.RequestException as e:
    print(f"❌ Error connecting to users endpoint: {e}")

# Test 3: Try alternative endpoints
endpoints_to_test = ["/users", "/api/users", "/api/v1/users", "/user"]
for endpoint in endpoints_to_test:
    try:
        resp = requests.get(f"{API_URL}{endpoint}", timeout=5)
        if resp.status_code == 200:
            print(f"\n✅ Found alternative endpoint: {endpoint}")
            break
    except:
        pass

print("\n✅ API test complete!")