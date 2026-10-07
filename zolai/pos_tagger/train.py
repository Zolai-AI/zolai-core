"""POS Tagger Training — CRF-based using sklearn-crfsuite."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sklearn_crfsuite
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from zolai.eval.store import get_eval_items, init_eval_db
from zolai.pos_tagger.annotate import tokenize_zolai


@dataclass
class POSTaggerModel:
    """Wrapper for CRF POS tagger."""
    crf: sklearn_crfsuite.CRF
    tagset: list[str]

    def predict(self, text: str) -> list[str]:
        """Predict POS tags for a sentence."""
        tokens = tokenize_zolai(text)
        features = [self._token_features(tokens, i) for i in range(len(tokens))]
        tags = self.crf.predict_single(features)
        return tags

    def _token_features(self, tokens: list[str], i: int) -> dict[str, Any]:
        """Extract features for a token at position i."""
        token = tokens[i]

        features = {
            'bias': 1.0,
            'token.lower()': token.lower(),
            'token[-3:]': token[-3:] if len(token) >= 3 else token,
            'token[-2:]': token[-2:] if len(token) >= 2 else token,
            'token.isupper()': token.isupper(),
            'token.istitle()': token.istitle(),
            'token.isdigit()': token.isdigit(),
            'token.isalpha()': token.isalpha(),
        }

        # Previous token
        if i > 0:
            prev = tokens[i-1]
            features.update({
                'prev.token.lower()': prev.lower(),
                'prev.token.istitle()': prev.istitle(),
                'prev.token.isupper()': prev.isupper(),
            })
        else:
            features['BOS'] = True

        # Next token
        if i < len(tokens) - 1:
            nxt = tokens[i+1]
            features.update({
                'next.token.lower()': nxt.lower(),
                'next.token.istitle()': nxt.istitle(),
                'next.token.isupper()': nxt.isupper(),
            })
        else:
            features['EOS'] = True

        return features


def load_training_data() -> tuple[list[list[dict]], list[list[str]]]:
    """Load training data from evaluation database."""
    init_eval_db()
    items = get_eval_items('pos_gold_v0')

    X = []  # features
    y = []  # labels

    for item in items:
        ann = json.loads(item.gold_annotation)
        tokens = [t['text'] for t in ann.get('tokens', [])]
        tags = [t.get('pos', 'X') for t in ann.get('tokens', [])]

        if len(tokens) == len(tags) and tokens:
            features = [POSTaggerModel(None, [])._token_features(tokens, i) for i in range(len(tokens))]
            X.append(features)
            y.append(tags)

    return X, y


def train_pos_tagger(
    model_path: Path = Path("models/pos_tagger_crf.pkl"),
    test_size: float = 0.2,
    random_state: int = 42,
) -> POSTaggerModel:
    """Train POS tagger using CRF."""
    print("Loading training data...")
    X, y = load_training_data()
    print(f"Loaded {len(X)} sentences, {sum(len(s) for s in X)} tokens")

    if not X:
        raise ValueError("No training data available")

    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state
    )
    print(f"Train: {len(X_train)} sentences, Test: {len(X_test)} sentences")

    # Train CRF
    print("Training CRF...")
    crf = sklearn_crfsuite.CRF(
        algorithm='lbfgs',
        c1=0.1,
        c2=0.1,
        max_iterations=100,
        all_possible_transitions=True,
        verbose=True,
    )

    crf.fit(X_train, y_train)

    # Evaluate
    print("Evaluating...")
    y_pred = crf.predict(X_test)

    # Flatten for classification report
    y_test_flat = [tag for sent in y_test for tag in sent]
    y_pred_flat = [tag for sent in y_pred for tag in sent]

    labels = sorted(set(y_test_flat + y_pred_flat))
    print(classification_report(y_test_flat, y_pred_flat, labels=labels, zero_division=0))

    # Save model
    model = POSTaggerModel(crf=crf, tagset=labels)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    with open(model_path, 'wb') as f:
        pickle.dump(model, f)

    print(f"Model saved to {model_path}")
    return model


def evaluate_on_gold(model: POSTaggerModel):
    """Evaluate trained model on gold set."""
    init_eval_db()
    items = get_eval_items('pos_gold_v0')

    all_pred = []
    all_gold = []

    for item in items:
        ann = json.loads(item.gold_annotation)
        text = ann.get('text', '')
        gold_tags = [t.get('pos', 'X') for t in ann.get('tokens', [])]
        pred_tags = model.predict(text)

        min_len = min(len(gold_tags), len(pred_tags))
        all_pred.extend(pred_tags[:min_len])
        all_gold.extend(gold_tags[:min_len])

    from sklearn.metrics import classification_report
    labels = sorted(set(all_gold + all_pred))
    print(classification_report(all_gold, all_pred, labels=labels, zero_division=0))


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "evaluate":
        model_path = Path("models/pos_tagger_crf.pkl")
        if model_path.exists():
            with open(model_path, 'rb') as f:
                model = pickle.load(f)
            evaluate_on_gold(model)
        else:
            print("Model not found. Train first.")
    else:
        model = train_pos_tagger()
        evaluate_on_gold(model)
