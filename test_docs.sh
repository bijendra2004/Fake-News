#!/bin/bash
source /Users/bijendrayadav/Desktop/Fake\ News\ /.venv/bin/activate
cd /Users/bijendrayadav/Desktop/Fake\ News\ 

echo "Testing DEVELOPMENT mode..."
export APP_ENV=development
lsof -ti:8000 | xargs kill -9 2>/dev/null
uvicorn backend.main:app --port 8000 &
PID=$!
sleep 3
DOCS_CODE=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/docs)
echo "Development /docs HTTP code: $DOCS_CODE"
kill -9 $PID

echo "Testing PRODUCTION mode..."
export APP_ENV=production
lsof -ti:8000 | xargs kill -9 2>/dev/null
uvicorn backend.main:app --port 8000 &
PID=$!
sleep 3
DOCS_CODE_PROD=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/docs)
echo "Production /docs HTTP code: $DOCS_CODE_PROD"

# Test an actual API endpoint to ensure it still works
API_CODE=$(curl -X POST -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/predict)
# We expect 401 Unauthorized or 400 Bad Request, NOT 404
echo "Production /api/predict HTTP code: $API_CODE"

kill -9 $PID
