import os
from dotenv import load_dotenv
from backend.gemini_explainer import GeminiExplainer

load_dotenv()

explainer = GeminiExplainer()
res = explainer.explain("who is the winner of ipl 2026? i guess it is mumbai indians.", {})
print(res)