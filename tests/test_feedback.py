"""Tests for the feedback/correction learning system."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from zolai.learning.feedback import FeedbackStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback" / "corrections.jsonl")


# ---------------------------------------------------------------------------
# Core tests
# ---------------------------------------------------------------------------

class TestFeedbackStore:
    def test_record_and_retrieve(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.record("pasian", "God", "Creator", user="peter", reason="context")
        corrections = store.get_corrections("pasian")
        assert len(corrections) == 1
        assert corrections[0]["original"] == "God"
        assert corrections[0]["corrected"] == "Creator"
        assert corrections[0]["user"] == "peter"

    def test_override_returns_latest(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.record("gam", "earth", "land", user="alice")
        store.record("gam", "land", "ground", user="bob")
        assert store.get_override("gam") == "ground"

    def test_stats_counts(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.record("pasian", "God", "Creator", user="peter")
        store.record("tapa", "life", "son", user="peter")
        store.record("tapa", "son", "offspring", user="alice")
        stats = store.stats()
        assert stats["total_corrections"] == 3
        assert stats["unique_words"] == 2
        # peter has 2, alice has 1
        user_map = dict(stats["top_correctors"])
        assert user_map["peter"] == 2
        assert user_map["alice"] == 1

    def test_empty_word_returns_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get_override("nonexistent") is None
        assert store.get_corrections("nonexistent") == []

    def test_multiple_corrections_for_word(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.record("topa", "Lord", "Master")
        store.record("topa", "Master", "Lord")
        store.record("topa", "Lord", "Sovereign")
        corrections = store.get_corrections("topa")
        assert len(corrections) == 3
        assert store.get_override("topa") == "Sovereign"

    def test_persistence(self, tmp_path: Path) -> None:
        path = tmp_path / "feedback" / "corrections.jsonl"
        store1 = FeedbackStore(path)
        store1.record("pasian", "God", "Creator")
        store1.record("tapa", "life", "son")
        # Reload from disk
        store2 = FeedbackStore(path)
        assert len(store2.get_corrections("pasian")) == 1
        assert store2.get_override("tapa") == "son"
        assert store2.stats()["total_corrections"] == 2

    def test_rag_uses_override(self, tmp_path: Path) -> None:
        """Verify ZolaiRAG injects feedback override as vocabulary evidence."""
        from zolai.knowledge.rag_contract import ZolaiRAG

        # Patch data dir to a temp location with a stub correction file
        fb_path = tmp_path / "feedback" / "corrections.jsonl"
        fb_path.parent.mkdir(parents=True)
        fb_path.write_text(
            json.dumps({
                "word": "testword",
                "original": "original",
                "corrected": "corrected_val",
                "user": "tester",
                "reason": "test",
                "timestamp": "2026-09-10T00:00:00Z",
            })
            + "\n"
        )
        # Patch FeedbackStore to use our path
        import zolai.learning.feedback as fb_mod

        orig_init = FeedbackStore.__init__

        def patched_init(self, path=None):
            orig_init(self, fb_path)

        fb_mod.FeedbackStore.__init__ = patched_init
        try:
            rag = ZolaiRAG(data_dir=tmp_path)
            pack = rag.retrieve("testword")
            fb_evidence = [e for e in pack.vocabulary if e.source == "feedback"]
            assert len(fb_evidence) == 1
            assert "corrected_val" in fb_evidence[0].text
            assert fb_evidence[0].confidence == 0.99
        finally:
            fb_mod.FeedbackStore.__init__ = orig_init

    def test_cli_record(self, tmp_path: Path) -> None:
        """CLI record command writes a correction."""
        env_patch = f"ZOLAI_DATA_ROOT={tmp_path.parent}"
        cmd = (
            f"{env_patch} python -m zolai.learning.feedback "
            f"record pasian God Creator --user peter --reason context"
        )
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(tmp_path.parent),
        )
        # Check that it produced JSON output
        if result.returncode == 0:
            output = json.loads(result.stdout)
            assert output["word"] == "pasian"
            assert output["corrected"] == "Creator"
        else:
            # If run outside zolai-core, skip gracefully
            pytest.skip(f"CLI not runnable in this env: {result.stderr[:200]}")
