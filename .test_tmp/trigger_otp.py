import requests

resp = requests.post(
    "http://127.0.0.1:8000/api/auth/otp-request",
    json={"email": "sachlens_test123@web-library.net"},
    headers={
        "X-CSRF-Token": "fake-token",
        "X-Device-Fingerprint": "test-device"
    },
    cookies={
        "csrf_token": "fake-token"
    }
)

print(resp.status_code)
print(resp.text)
