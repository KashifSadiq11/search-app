#!/bin/bash
echo "Starting Rec Engine..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt
python -c "import sys; sys.path.append('..'); from services.engine.database import init_db; init_db(); print('Database initialized!')"
cd ..
echo "Starting on http://localhost:8000"
uvicorn services.engine.main:app --reload --host 0.0.0.0 --port 8000
