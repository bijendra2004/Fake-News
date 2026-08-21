import requests

s = requests.Session()
email = "testpredict@example.com"
found_otp = "726647"

# 2. OTP Verify
resp_verify = s.post(
    "http://127.0.0.1:8000/api/auth/otp-verify",
    json={"email": email, "otp": found_otp},
    headers={"X-CSRF-Token": "fake-token", "X-Device-Fingerprint": "test-device"},
    cookies={"csrf_token": "fake-token"}
)
print("OTP Verify:", resp_verify.status_code, resp_verify.text)
if resp_verify.status_code == 200:
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
