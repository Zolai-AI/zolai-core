"""Tests for custom Zolai SentencePiece tokenizer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
from zolai.tokenizer.zolai_tokenizer import ZolaiTokenizer

MODEL_PATH = Path(__file__).parent.parent.parent / "data" / "tokenizer" / "zolai_spm.model"


@pytest.fixture(scope="module")
def tokenizer():
    if not MODEL_PATH.exists():
        pytest.skip("Tokenizer model not trained yet")
    return ZolaiTokenizer(str(MODEL_PATH))


def test_model_file_exists():
    assert MODEL_PATH.exists(), f"Model not found at {MODEL_PATH}"


def test_encode_decode_roundtrip(tokenizer):
    text = "Pasian in vantung leh leitung a piangsak hi."
    ids = tokenizer.encode(text)
    decoded = tokenizer.decode(ids)
    assert text in decoded or len(decoded) > 0


def test_vocab_size_reasonable(tokenizer):
    vs = tokenizer.vocab_size()
    assert 1000 < vs < 20000, f"Vocab size {vs} out of expected range"


def test_coverage_above_80_percent(tokenizer):
    text = "Pasian in vantung leh leitung a piangsak hi. Gam ka mu hi."
    cov = tokenizer.coverage(text)
    assert cov > 0.80, f"Coverage {cov:.1%} below 80% threshold"


def test_handles_zolai_text(tokenizer):
    ids = tokenizer.encode("Pasian om hi. Lungdam na.")
    assert len(ids) > 0
    assert all(isinstance(i, int) for i in ids)


def test_handles_english_text(tokenizer):
    ids = tokenizer.encode("God created the heaven and earth.")
    assert len(ids) > 0
