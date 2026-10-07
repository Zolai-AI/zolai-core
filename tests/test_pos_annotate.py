"""Tests for the POS Annotation Tool CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zolai.pos_tagger.annotate import (
    _load_annotations,
    _save_annotation,
    _write_all_annotations,
    cmd_export,
    cmd_list,
    cmd_stats,
)


class TestAnnotationStorage:
    """Test annotation load/save operations."""

    def test_save_and_load_annotation(self, tmp_path: Path) -> None:
        """Test saving and loading a single annotation."""
        # Use temp file
        gold_file = tmp_path / "test_pos_gold.jsonl"

        # Patch the global path
        import zolai.pos_tagger.annotate as annotate_module
        original_gold = annotate_module._GOLD_FILE
        annotate_module._GOLD_FILE = gold_file

        try:
            # Save annotation
            annotation = {
                "sentence": "Pasian in vantung leh leitung a piangsak hi.",
                "tokens": ["Pasian", "in", "vantung", "leh", "leitung", "a", "piangsak", "hi"],
                "tags": ["N.PROPER", "PART.ERG", "N.COMPOUND", "CONJ", "N.COMPOUND", "PRON", "V.TRANS", "PART"],
                "source": "bible_verses",
                "book": "GEN",
                "chapter": 1,
                "verse": 1,
                "confidence": [0.8, 1.0, 0.9, 0.95, 0.9, 1.0, 0.9, 1.0],
                "annotator": "test",
                "created_at": "2026-10-07T00:00:00Z",
            }
            _save_annotation(annotation)

            # Load and verify
            annotations = _load_annotations()
            assert len(annotations) == 1
            key = "Pasian in vantung leh leitung a piangsak hi."
            assert key in annotations
            loaded = annotations[key]
            assert loaded["tokens"] == annotation["tokens"]
            assert loaded["tags"] == annotation["tags"]
            assert loaded["source"] == "bible_verses"

            # Save another
            annotation2 = {
                "sentence": "Gam ka mu hi.",
                "tokens": ["Gam", "ka", "mu", "hi"],
                "tags": ["NOUN", "PRON", "VERB", "PART"],
                "source": "bible_verses",
                "confidence": [0.9, 1.0, 0.9, 1.0],
                "annotator": "test",
                "created_at": "2026-10-07T00:00:00Z",
            }
            _save_annotation(annotation2)

            annotations = _load_annotations()
            assert len(annotations) == 2

        finally:
            annotate_module._GOLD_FILE = original_gold

    def test_write_all_annotations(self, tmp_path: Path) -> None:
        """Test writing all annotations at once."""
        gold_file = tmp_path / "test_pos_gold.jsonl"

        import zolai.pos_tagger.annotate as annotate_module
        original_gold = annotate_module._GOLD_FILE
        annotate_module._GOLD_FILE = gold_file

        try:
            annotations = {
                "Sentence 1.": {"sentence": "Sentence 1.", "tags": ["NOUN", "VERB"]},
                "Sentence 2.": {"sentence": "Sentence 2.", "tags": ["PRON", "VERB"]},
            }
            _write_all_annotations(annotations)

            # Verify file contents
            with open(gold_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 2
            for line in lines:
                data = json.loads(line)
                assert "sentence" in data
                assert "tags" in data

        finally:
            annotate_module._GOLD_FILE = original_gold


class TestCLICommands:
    """Test CLI command functions."""

    def test_cmd_list_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test list command with no annotations."""
        gold_file = tmp_path / "test_pos_gold.jsonl"

        import zolai.pos_tagger.annotate as annotate_module
        original_gold = annotate_module._GOLD_FILE
        annotate_module._GOLD_FILE = gold_file

        # Mock _get_all_sentences to return empty
        monkeypatch.setattr(annotate_module, "_get_all_sentences", lambda: [])

        try:
            class Args:
                show_all = False
                show_remaining = False

            result = cmd_list(Args())
            assert result == 0
        finally:
            annotate_module._GOLD_FILE = original_gold

    def test_cmd_export(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test export command."""
        gold_file = tmp_path / "test_pos_gold.jsonl"
        output_file = tmp_path / "export.jsonl"

        import zolai.pos_tagger.annotate as annotate_module
        original_gold = annotate_module._GOLD_FILE
        annotate_module._GOLD_FILE = gold_file

        try:
            # Add some annotations
            _write_all_annotations({
                "Sentence 1.": {"sentence": "Sentence 1.", "tags": ["NOUN", "VERB"], "source": "bible_verses"},
                "Sentence 2.": {"sentence": "Sentence 2.", "tags": ["PRON", "VERB"], "source": "dictionary"},
            })

            class Args:
                output = str(output_file)
                source = None

            result = cmd_export(Args())
            assert result == 0

            # Verify export file
            with open(output_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 2

        finally:
            annotate_module._GOLD_FILE = original_gold

    def test_cmd_export_with_source_filter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test export command with source filter."""
        gold_file = tmp_path / "test_pos_gold.jsonl"
        output_file = tmp_path / "export.jsonl"

        import zolai.pos_tagger.annotate as annotate_module
        original_gold = annotate_module._GOLD_FILE
        annotate_module._GOLD_FILE = gold_file

        try:
            _write_all_annotations({
                "Sentence 1.": {"sentence": "Sentence 1.", "tags": ["NOUN", "VERB"], "source": "bible_verses"},
                "Sentence 2.": {"sentence": "Sentence 2.", "tags": ["PRON", "VERB"], "source": "dictionary"},
            })

            class Args:
                output = str(output_file)
                source = "bible_verses"

            result = cmd_export(Args())
            assert result == 0

            with open(output_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["source"] == "bible_verses"

        finally:
            annotate_module._GOLD_FILE = original_gold

    def test_cmd_stats(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test stats command."""
        gold_file = tmp_path / "test_pos_gold.jsonl"

        import zolai.pos_tagger.annotate as annotate_module
        original_gold = annotate_module._GOLD_FILE
        annotate_module._GOLD_FILE = gold_file

        try:
            _write_all_annotations({
                "Sentence 1.": {
                    "sentence": "Sentence 1.",
                    "tokens": ["Token1", "Token2"],
                    "tags": ["NOUN", "VERB"],
                    "source": "bible_verses",
                    "annotator": "test1",
                    "confidence": [0.9, 0.9],
                },
                "Sentence 2.": {
                    "sentence": "Sentence 2.",
                    "tokens": ["Token3"],
                    "tags": ["PRON"],
                    "source": "dictionary",
                    "annotator": "test2",
                    "confidence": [0.8],
                },
            })

            class Args:
                pass

            result = cmd_stats(Args())
            assert result == 0

        finally:
            annotate_module._GOLD_FILE = original_gold


class TestAutoTagging:
    """Test auto-tagging functionality."""

    def test_auto_tag_basic(self) -> None:
        """Test that auto-tagging returns expected format."""
        from zolai.pos_tagger.annotate import _auto_tag

        result = _auto_tag("Pasian in vantung a piangsak hi.")

        assert isinstance(result, list)
        assert len(result) > 0
        for token, tag, conf in result:
            assert isinstance(token, str)
            assert isinstance(tag, str)
            assert isinstance(conf, float)
            assert 0.0 <= conf <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

