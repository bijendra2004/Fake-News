#!/bin/bash
source /Users/bijendrayadav/Desktop/Fake\ News\ /.venv/bin/activate
cd /Users/bijendrayadav/Desktop/Fake\ News\ 

export APP_ENV=production
# Provide JWT_SECRET so it starts up
export JWT_SECRET=test_jwt_secret_which_is_long_enough_32bytes_min

# DELIBERATELY DO NOT EXPORT DATA_ENCRYPTION_KEY
unset DATA_ENCRYPTION_KEY

lsof -ti:8000 | xargs kill -9 2>/dev/null
uvicorn backend.main:app --port 8000 &
PID=$!
sleep 3
echo "Sending OTP request..."
curl -X POST -s -v -H "Content-Type: application/json" -d '{"email": "test@example.com"}' http://localhost:8000/api/auth/otp-request
kill -9 $PID
