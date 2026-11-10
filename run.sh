#!/usr/bin/env bash
set -e

echo "======================================================="
echo "🗓️ Initializing Timetable Manager Full-Stack Platform..."
echo "======================================================="

# Ensure virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating local Python virtual environment (venv)..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install backend package dependencies
echo "Installing/verifying backend API dependencies..."
pip install -r backend/requirements.txt

# Export local python path to resolve modules
export PYTHONPATH=.

# Rebuild the school configuration from the Excel export whenever the Excel or quotas changed
if [ config/SHIKHA_CLASSWISE.xlsx -nt config/my_school_config.json ] || [ config/period_quotas.json -nt config/my_school_config.json ]; then
    echo "Importing school data from config/SHIKHA_CLASSWISE.xlsx..."
    python3 -m backend.ingest.excel_loader
fi

# Start FastAPI application server in the background
echo "Starting FastAPI backend service on http://localhost:8000..."
python3 -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000 &
SERVER_PID=$!

# Wait briefly to let server initialize
sleep 4

# Open stunning web frontend interface in default web browser
echo "Launching browser interface..."
if which open >/dev/null; then
    open http://localhost:8000
elif which xdg-open >/dev/null; then
    xdg-open http://localhost:8000
else
    echo "Open http://localhost:8000 in your web browser."
fi

echo "======================================================="
echo "✨ System running! Press Ctrl+C to stop backend service."
echo "======================================================="

# Keep process alive capturing termination
trap "echo 'Stopping FastAPI backend server...'; kill $SERVER_PID; exit" INT TERM
wait $SERVER_PID
