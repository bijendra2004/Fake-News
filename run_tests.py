import os
from unittest.mock import patch

def mock_validate_csrf(*args, **kwargs):
    return True

patch('backend.main.validate_csrf', mock_validate_csrf).start()

from fastapi.testclient import TestClient
from backend.main import app
from backend.auth import create_access_token
from backend.models import get_or_create_user
from backend.main import SessionLocal
from backend.main import gemini_explainer
from backend.media import extract_text_from_url

# Create DB and user to get token
db = SessionLocal()
email = "test@example.com"
user = get_or_create_user(db, email)
token = create_access_token(email)
headers = {"Authorization": f"Bearer {token}"}

client = TestClient(app)

print("--- 1. IMAGE WITH REAL TEXT ---")
with open("sample_claim.png", "rb") as f:
    response = client.post("/api/predict-image", files={"file": ("sample_claim.png", f, "image/png")}, headers=headers)
print("Status:", response.status_code)
print("Response:", response.json())
print()

print("--- 2. VOICE ---")
with open("sample_claim.wav", "rb") as f:
    response = client.post("/api/predict-voice", files={"file": ("sample_claim.wav", f, "audio/wav")}, headers=headers)
print("Status:", response.status_code)
print("Response:", response.json())
print()

print("--- 3. LINK ---")
print("Valid Link:")
response = client.post("/api/predict-link", json={"url": "https://en.wikipedia.org/wiki/Earth"}, headers=headers)
print("Status:", response.status_code)
print("Response:", response.json())
print()
print("Invalid Link:")
response = client.post("/api/predict-link", json={"url": "https://this-is-an-invalid-url-that-does-not-exist.com"}, headers=headers)
print("Status:", response.status_code)
print("Response:", response.json())
print()

print("--- 4. SANITY-CHECK THE PERCENTAGE SCALE ---")
response = client.post("/api/predict", json={"text": "The Earth orbits the Sun."}, headers=headers)
print("Status:", response.status_code)
print("Response:", response.json())
print()

print("--- 5. ERROR HANDLING (Simulating Gemini failure) ---")
# To simulate Gemini failure, let's pass a bad API key directly to the explainer's client
if hasattr(gemini_explainer, 'client'):
    # Usually it's initialized with genai.Client
    pass
# Wait, let's just make it raise an exception by replacing the method
def mock_explain(*args, **kwargs):
    from backend.gemini_explainer import GeminiExplanationError
    raise GeminiExplanationError("Simulated Gemini API error")

with patch.object(gemini_explainer, 'explain', side_effect=mock_explain):
    response = client.post("/api/predict", json={"text": "This should fail because of Gemini API key."}, headers=headers)
    print("Status:", response.status_code)
    print("Response:", response.json())
print()
