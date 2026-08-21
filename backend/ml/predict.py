from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import os
import secrets

import joblib
import jwt
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from ..secrets_config import get_runtime_secret

# User-supplied text is untrusted inference input only — never merge into system
# instructions or prompt templates if LLM integration is added later.

JWT_SECRET = get_runtime_secret("JWT_SECRET", "APP_EPHEMERAL_JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
MODEL_PATH = Path(__file__).with_name("model.joblib")


@dataclass
class PredictionService:
    model: Pipeline | None = None

    def load(self) -> None:
        if MODEL_PATH.exists():
            self.model = joblib.load(MODEL_PATH)
        else:
            self.model = _fallback_pipeline()

    def predict(self, text: str) -> dict[str, Any]:
        if self.model is None:
            self.load()
        assert self.model is not None

        vectorizer: TfidfVectorizer = self.model.named_steps["tfidf"]
        classifier: LogisticRegression = self.model.named_steps["clf"]
        vector = vectorizer.transform([text])
        probabilities = classifier.predict_proba(vector)[0]
        predicted_index = int(np.argmax(probabilities))
        label = "real" if classifier.classes_[predicted_index] == 1 else "fake"
        confidence = float(probabilities[predicted_index])
        top_keywords = extract_top_keywords(vectorizer, classifier, vector, predicted_index)
        return {
            "label": label,
            "confidence": round(confidence, 4),
            "top_keywords": top_keywords,
        }

    def verify_access_token(self, token: str) -> bool:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return payload.get("type") == "access"
        except jwt.PyJWTError:
            return False


def extract_top_keywords(
    vectorizer: TfidfVectorizer,
    classifier: LogisticRegression,
    vector,
    predicted_index: int,
) -> list[str]:
    feature_names = np.array(vectorizer.get_feature_names_out())
    coefficients = classifier.coef_[0]
    if predicted_index == 0:
        coefficients = -coefficients

    weighted_terms = vector.toarray()[0] * coefficients
    top_indices = np.argsort(weighted_terms)[::-1]
    top_terms: list[str] = []
    for index in top_indices:
        if weighted_terms[index] <= 0:
            continue
        term = feature_names[index]
        if term not in top_terms:
            top_terms.append(term)
        if len(top_terms) == 5:
            break
    if not top_terms:
        top_terms = feature_names[top_indices[:5]].tolist()
    return top_terms[:5]


def _fallback_pipeline() -> Pipeline:
    vectorizer = TfidfVectorizer(stop_words="english")
    classifier = LogisticRegression(max_iter=1000)
    training_texts = [
        "official statement confirms report",
        "breaking fake expose scandal hoax",
    ]
    training_labels = [1, 0]
    vectorizer.fit(training_texts)
    classifier.fit(vectorizer.transform(training_texts), training_labels)
    return Pipeline([("tfidf", vectorizer), ("clf", classifier)])
