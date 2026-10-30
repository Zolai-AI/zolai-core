"""POS Tagger Training — CRF-based using sklearn-crfsuite."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sklearn_crfsuite
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import train_test_split

from zolai.config import config
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


#: Default artifact location. This was ``Path("models/pos_tagger_crf.pkl")`` —
#: relative to the *working directory*, so `python -m zolai.pos_tagger.train`
#: from anywhere but the repo root trained a model, saved it somewhere else, and
#: then reported "Model not found" on the next line. `config.paths.root` is the
#: resolver for toolkit-owned paths, same rule as the database.
DEFAULT_MODEL_PATH = config.paths.root / "models" / "pos_tagger_crf.pkl"


def train_pos_tagger(
    model_path: Path = DEFAULT_MODEL_PATH,
    test_size: float = 0.2,
    random_state: int = 42,
    metrics_out: dict[str, Any] | None = None,
    X: list[list[dict]] | None = None,
    y: list[list[str]] | None = None,
) -> POSTaggerModel:
    """Train POS tagger using CRF.

    Args:
        model_path: Where the pickle is written (staging path for the learning
            cycle, canonical path for the standalone script).
        test_size / random_state: The train/eval split.
        metrics_out: Filled with split sizes plus held-out ``macro_f1`` and
            ``accuracy`` — in memory only, so callers can gate and record a run
            without parsing stdout.
        X / y: Pre-loaded features/labels. Defaults to the canonical
            ``pos_gold_v0`` set, which is *also* the training source — the
            held-out split is the only generalization number here.
    """
    print("Loading training data...")
    if X is None or y is None:
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

    if metrics_out is not None:
        metrics_out.update(
            {
                "split": "holdout",
                "train_sentences": len(X_train),
                "test_sentences": len(X_test),
                "train_tokens": sum(len(s) for s in y_train),
                "test_tokens": len(y_test_flat),
                "macro_f1": float(
                    f1_score(y_test_flat, y_pred_flat, average="macro", zero_division=0)
                ),
                "accuracy": float(accuracy_score(y_test_flat, y_pred_flat)),
                "labels": len(labels),
                "model_path": str(model_path),
            }
        )

    # Save model
    model = POSTaggerModel(crf=crf, tagset=labels)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    with open(model_path, 'wb') as f:
        pickle.dump(model, f)

    print(f"Model saved to {model_path}")
    return model


def evaluate_on_gold(model: POSTaggerModel, eval_set: str = 'pos_gold_v0'):
    """Score the model against a set — and say out loud whether it trained on it.

    The caller trains on ``pos_gold_v0`` and this reads ``pos_gold_v0`` back, so by
    default the number is a memorization score. It printed "Results on full gold
    set: 100% accuracy" into the planning docs that way, which is how a harness
    mistake becomes a product claim. Pass a genuinely unseen ``eval_set`` for a
    generalization number.
    """
    init_eval_db()
    items = get_eval_items(eval_set)

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

    in_sample = eval_set == 'pos_gold_v0'
    print(
        f"\n=== Evaluation on '{eval_set}' "
        + ("(IN-SAMPLE: this set was in the training data — memorization, not generalization)"
           if in_sample else "(held out)")
        + " ==="
    )
    if not all_gold:
        print("No items in that eval set — nothing measured.")
        return
    labels = sorted(set(all_gold + all_pred))
    print(classification_report(all_gold, all_pred, labels=labels, zero_division=0))


if __name__ == "__main__":
    import sys

    # Pickle records the class under whichever module it was imported from, and
    # running this file as a script makes that `__main__` — an artifact no library
    # reader can load. `zolai eval run pos --system crf` had to redirect that name
    # to read the model already on disk; rebinding here means new saves name the
    # importable module instead.
    POSTaggerModel.__module__ = "zolai.pos_tagger.train"

    if len(sys.argv) > 1 and sys.argv[1] == "evaluate":
        model_path = DEFAULT_MODEL_PATH
        if model_path.exists():
            with open(model_path, 'rb') as f:
                model = pickle.load(f)
            evaluate_on_gold(model)
        else:
            print(f"Model not found at {model_path}. Train first.")
    else:
        model = train_pos_tagger()
        evaluate_on_gold(model)
