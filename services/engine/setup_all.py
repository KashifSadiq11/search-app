#!/usr/bin/env python3
"""One command to set up everything."""
import subprocess
import time
import requests

def run_setup():
    print("Starting complete setup...")
    
    # 1. Start API in background
    print("Starting API...")
    api_process = subprocess.Popen(['python', 'main.py'])
    time.sleep(5)  # Wait for API to start
    
    # 2. Generate test items
    print("Generating test items...")
    response = requests.post("http://localhost:8000/generate-test-items/?count=500")
    print(f"Items created: {response.json()}")
    
    # 3. Generate users and interactions
    print("Generating users and interactions...")
    subprocess.run(['python', 'generate_test_data.py'])
    
    # 4. Train models
    print("Training models...")
    subprocess.run(['python', 'run_training.py'])
    
    # 5. Build index
    print("Building embeddings index...")
    subprocess.run(['python', 'build_index_offline.py'])
    
    print("\n✓ Setup complete!")
    print("API is running at http://localhost:8000")
    print("View docs at http://localhost:8000/docs")
    
    # Keep API running
    try:
        api_process.wait()
    except KeyboardInterrupt:
        api_process.terminate()

if __name__ == "__main__":
    run_setup()