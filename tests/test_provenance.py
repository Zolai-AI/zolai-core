"""Tests for scripts/data_provenance.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import scripts.data_provenance as provenance_mod


def test_manifest_generates():
    """Test that manifest generation works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "data" / "bible"
        data_dir.mkdir(parents=True)
        test_file = data_dir / "test_data.jsonl"
        with open(test_file, "w") as f:
            f.write('{"id": 1, "text": "hello"}\n')
            f.write('{"id": 2, "text": "world"}\n')
        (Path(tmpdir) / "AGENTS.md").touch()
        manifest = provenance_mod.generate_manifest(str(Path(tmpdir)))
        assert "version" in manifest
        assert "generated_at" in manifest
        assert "total_files" in manifest
        assert "files" in manifest
        assert manifest["total_files"] == 1
        file_entry = manifest["files"][0]
        assert "filename" in file_entry
        assert "size_bytes" in file_entry
        assert "sha256" in file_entry
        assert "row_count" in file_entry


def test_manifest_has_all_files():
    """Test that manifest contains all expected files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bible_dir = Path(tmpdir) / "data" / "bible"
        dict_dir = Path(tmpdir) / "data" / "dictionary" / "processed"
        parallel_dir = Path(tmpdir) / "data" / "parallel"
        for d in (bible_dir, dict_dir, parallel_dir):
            d.mkdir(parents=True)
        for d, fn in [(bible_dir, "grammar_patterns.jsonl"),
                      (bible_dir, "vocab_index.jsonl"),
                      (dict_dir, "dict_master.jsonl"),
                      (parallel_dir, "pairs.jsonl")]:
            (d / fn).write_text('{"id": 1}\n')
        (Path(tmpdir) / "AGENTS.md").touch()
        manifest = provenance_mod.generate_manifest(str(Path(tmpdir)))
        filenames = [f["filename"] for f in manifest["files"]]
        assert len(filenames) == 4
        assert any("bible" in f for f in filenames)
        assert any("dictionary" in f for f in filenames)
        assert any("parallel" in f for f in filenames)


def test_manifest_row_counts():
    """Test that row counts are correct."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "data" / "bible"
        data_dir.mkdir(parents=True)
        (data_dir / "empty.jsonl").write_text("")
        (data_dir / "single.jsonl").write_text('{"id": 1}\n')
        (data_dir / "triple.jsonl").write_text('{"id": 1}\n{"id": 2}\n{"id": 3}\n')
        (Path(tmpdir) / "AGENTS.md").touch()
        manifest = provenance_mod.generate_manifest(str(Path(tmpdir)))
        file_map = {f["filename"]: f for f in manifest["files"]}
        assert file_map["data/bible/empty.jsonl"]["row_count"] == 0
        assert file_map["data/bible/single.jsonl"]["row_count"] == 1
        assert file_map["data/bible/triple.jsonl"]["row_count"] == 3


def test_verify_detects_missing():
    """Test that verification detects missing files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "data" / "bible"
        data_dir.mkdir(parents=True)
        test_file = data_dir / "test_data.jsonl"
        test_file.write_text('{"id": 1}\n')
        (Path(tmpdir) / "AGENTS.md").touch()
        manifest = provenance_mod.generate_manifest(str(Path(tmpdir)))
        manifest_path = Path(tmpdir) / "data" / "provenance.json"
        manifest_path.write_text(json.dumps(manifest))
        test_file.unlink()
        warnings = provenance_mod.verify_manifest(str(Path(tmpdir)), str(manifest_path))
        assert len(warnings) == 1
        assert "MISSING" in warnings[0]


def test_verify_passes_on_clean():
    """Test that verification passes on clean state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "data" / "bible"
        data_dir.mkdir(parents=True)
        (data_dir / "test_data.jsonl").write_text('{"id": 1}\n')
        (Path(tmpdir) / "AGENTS.md").touch()
        manifest = provenance_mod.generate_manifest(str(Path(tmpdir)))
        manifest_path = Path(tmpdir) / "data" / "provenance.json"
        manifest_path.write_text(json.dumps(manifest))
        warnings = provenance_mod.verify_manifest(str(Path(tmpdir)), str(manifest_path))
        assert len(warnings) == 0
