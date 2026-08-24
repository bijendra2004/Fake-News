import requests
import time

print("Requesting OTP from Render...")
resp = requests.post("https://fake-news-cvzg.onrender.com/api/auth/otp-request", json={"email": "sachlenstest@mailinator.com"})
print(resp.status_code, resp.text)
