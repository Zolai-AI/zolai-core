"""Tests for scripts/data_audit.py"""
import json
import tempfile
from pathlib import Path

import scripts.data_audit as data_audit_mod


def test_detect_schema():
    """Test filename-to-schema detection."""
    assert data_audit_mod.detect_schema(Path("dict_unified_v1.jsonl")) == "dictionary_unified"
    assert data_audit_mod.detect_schema(Path("dict_enriched_v1.jsonl")) == "dictionary_enriched"
    assert data_audit_mod.detect_schema(Path("dict_semantic_v1.jsonl")) == "dictionary_semantic"
    assert data_audit_mod.detect_schema(Path("bible_parallel_tdb77_kjv.jsonl")) == "bible_parallel"
    assert data_audit_mod.detect_schema(Path("zo_en_pairs_combined_v1.jsonl")) == "parallel_zo_en"
    assert data_audit_mod.detect_schema(Path("corpus_unified_v1.jsonl")) == "corpus"
    assert data_audit_mod.detect_schema(Path("random_file.jsonl")) is None


def test_stream_jsonl_valid():
    """Test streaming valid JSONL."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"text": "hello"}\n')
        f.write('{"text": "world"}\n')
        f.write('\n')  # empty line (skipped)
        f.write('{"text": "end"}\n')
        path = Path(f.name)

    results = list(data_audit_mod.stream_jsonl(path))
    assert len(results) == 3
    assert results[0] == (1, {"text": "hello"}, None)
    assert results[1] == (2, {"text": "world"}, None)
    assert results[2] == (4, {"text": "end"}, None)
    path.unlink()


def test_stream_jsonl_invalid():
    """Test streaming with invalid JSON."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"text": "valid"}\n')
        f.write('not json at all\n')
        f.write('{"text": "also valid"}\n')
        path = Path(f.name)

    results = list(data_audit_mod.stream_jsonl(path))
    assert len(results) == 3
    assert results[0][2] is None  # valid
    assert results[1][2] is not None  # error
    assert results[2][2] is None  # valid
    path.unlink()


def test_check_empty_fields():
    """Test empty field detection."""
    obj = {"zolai": "hello", "english": "", "pos": []}
    issues = data_audit_mod.check_empty_fields(obj, ["zolai", "english", "pos"])
    assert "empty:english" in issues
    assert "empty_list:pos" in issues
    assert not any("zolai" in i for i in issues)


def test_check_encoding():
    """Test encoding issue detection."""
    assert data_audit_mod.check_encoding("hello world") == []
    assert len(data_audit_mod.check_encoding("hello \ufffd world")) > 0
    assert len(data_audit_mod.check_encoding("hello <html>world</html>")) > 0


def test_audit_file_pass(tmp_path):
    """Test auditing a valid JSONL file."""
    # Create file under a temp dir and point DATA_ROOT there
    jsonl_path = tmp_path / "corpus_unified_v1.jsonl"
    with open(jsonl_path, "w") as f:
        json.dump({"text": "hello", "source": "test"}, f)
        f.write("\n")
        json.dump({"text": "world", "source": "test"}, f)
        f.write("\n")

    original_root = data_audit_mod.DATA_ROOT
    data_audit_mod.DATA_ROOT = tmp_path
    try:
        result = data_audit_mod.audit_file(jsonl_path, check_duplicates=True)
        assert result["status"] == "PASS"
        assert result["line_count"] == 2
        assert result["error_lines"] == 0
    finally:
        data_audit_mod.DATA_ROOT = original_root
