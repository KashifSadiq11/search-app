# simple/run.bat (WINDOWS BATCH FILE)
@echo off
echo Starting Rec Engine...

cd /d F:\sistemsai\recomendations\rec-engine\simple





echo Initializing database...
python -c "import sys; sys.path.append('..'); from services.engine.database import init_db; init_db(); print('Database initialized!')"

echo Starting application on http://localhost:8000
echo API docs available at http://localhost:8000/docs
cd ..
uvicorn services.engine.main:app --reload --host 0.0.0.0 --port 8000

pause
