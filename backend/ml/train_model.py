from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report

STOPWORDS = 'english'


def load_dataset(fake_path: Path, true_path: Path) -> pd.DataFrame:
    fake_df = pd.read_csv(fake_path)
    true_df = pd.read_csv(true_path)
    fake_df = normalize_columns(fake_df, label_value=0)
    true_df = normalize_columns(true_df, label_value=1)
    return pd.concat([fake_df, true_df], ignore_index=True)


def normalize_columns(df: pd.DataFrame, label_value: int) -> pd.DataFrame:
    text_columns = [column for column in ["text", "title", "subject"] if column in df.columns]
    if not text_columns:
        first_column = df.columns[0]
        df = df.rename(columns={first_column: "text"})
        text_columns = ["text"]

    merged_text = df[text_columns].fillna("").astype(str).agg(" ".join, axis=1)
    result = pd.DataFrame({"text": merged_text.str.replace(r"\s+", " ", regex=True).str.strip(), "label": label_value})
    result = result[result["text"].str.len() > 0]
    return result


def build_model() -> Pipeline:
    return Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    stop_words=STOPWORDS,
                    lowercase=True,
                    ngram_range=(1, 2),
                    max_df=0.95,
                    min_df=2,
                    token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z']+\b",
                ),
            ),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train SachLens news classifier")
    parser.add_argument("--fake", required=True, type=Path)
    parser.add_argument("--true", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    data = load_dataset(args.fake, args.true)
    train_texts, test_texts, train_labels, test_labels = train_test_split(
        data["text"], data["label"], test_size=0.2, random_state=42, stratify=data["label"], shuffle=True
    )

    model = build_model()
    model.fit(train_texts, train_labels)
    predictions = model.predict(test_texts)
    print(classification_report(test_labels, predictions))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.output)
    print(f"Saved model to {args.output}")


if __name__ == "__main__":
    main()
