import requests
import sqlite3
import hmac
import hashlib
import os

# 1. OTP Request
s = requests.Session()
email = "testpredict@example.com"
resp_otp = s.post(
    "http://127.0.0.1:8000/api/auth/otp-request",
    json={"email": email},
    headers={"X-CSRF-Token": "fake-token", "X-Device-Fingerprint": "test-device"},
    cookies={"csrf_token": "fake-token"}
)
print("OTP Request:", resp_otp.status_code)

conn = sqlite3.connect("sachlens.db")
cursor = conn.cursor()
cursor.execute("SELECT otp_hash FROM otp_challenges WHERE email=?", (email,))
row = cursor.fetchone()

# Parse .env
with open(".env", "r") as f:
    for line in f:
        if line.strip() and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ[k] = v.strip('"')

secret = os.environ.get("JWT_SECRET", "APP_EPHEMERAL_JWT_SECRET")

target_hash = row[0]
found_otp = None
for i in range(1000000):
    otp = f"{i:06d}"
    h = hashlib.sha256(f"{email}:{otp}:{secret}".encode('utf-8')).hexdigest()
    if h == target_hash:
        found_otp = otp
        break

print("Found OTP:", found_otp)

# 2. OTP Verify
resp_verify = s.post(
    "http://127.0.0.1:8000/api/auth/otp-verify",
    json={"email": email, "otp": found_otp},
    headers={"X-CSRF-Token": "fake-token", "X-Device-Fingerprint": "test-device"},
    cookies={"csrf_token": "fake-token"}
)
print("OTP Verify:", resp_verify.status_code, resp_verify.text)
access_token = resp_verify.json().get("access_token")

# 3. Predict
resp_predict = s.post(
    "http://127.0.0.1:8000/api/predict",
    json={"text": "breaking fake expose scandal hoax"},
    headers={
        "X-CSRF-Token": "fake-token", 
        "X-Device-Fingerprint": "test-device",
        "Authorization": f"Bearer {access_token}"
    },
    cookies={"csrf_token": "fake-token"}
)
print("Predict:", resp_predict.status_code, resp_predict.text)
