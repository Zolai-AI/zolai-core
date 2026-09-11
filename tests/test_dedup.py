"""Tests for dictionary deduplication analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dedup_analysis import (
    find_duplicates,
    load_dict,
    merge_duplicates,
    write_dict,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


@pytest.fixture()
def tmp_dicts(tmp_path: Path):
    """Create two small dictionary files for testing."""
    master_file = tmp_path / "master.jsonl"
    canonical_file = tmp_path / "canonical.jsonl"

    master_entries = [
        {"zolai": "pasian", "english": ["God"], "source": "bible",
         "english_clean": "God"},
        {"zolai": "gam", "english": ["earth", "land"], "source": "bible",
         "english_clean": "earth"},
        {"zolai": "tui", "english": ["water"], "source": "bible",
         "english_clean": "water"},
    ]
    canonical_entries = [
        {"zolai": "pasian", "translations": ["God", "Creator"],
         "pos": ["n"], "headword": "pasian", "sources": ["bible"],
         "category": "dictionary", "translations_clean": "God"},
        {"zolai": "gam", "translations": ["earth"],
         "pos": ["n"], "headword": "gam", "sources": ["bible"],
         "category": "dictionary", "translations_clean": "earth"},
        {"zolai": "vantung", "translations": ["heaven"],
         "pos": ["n"], "headword": "vantung", "sources": ["bible"],
         "category": "dictionary", "translations_clean": "heaven"},
        {"headword": "topa", "translations": ["Lord"],
         "pos": ["n"], "sources": ["bible"],
         "category": "dictionary", "translations_clean": "Lord"},
    ]

    _write_jsonl(master_file, master_entries)
    _write_jsonl(canonical_file, canonical_entries)

    return master_file, canonical_file, master_entries, canonical_entries


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFindDuplicates:
    """Test duplicate identification between two dictionaries."""

    def test_finds_overlapping_headwords(self, tmp_dicts):
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)

        # "pasian" and "gam" overlap; "tui" unique to master;
        # "vantung" and "topa" unique to canonical
        assert report.overlapping == 2
        assert report.unique_master == 1
        assert report.unique_canonical == 2

    def test_sample_overlaps_populated(self, tmp_dicts):
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)

        assert len(report.sample_overlaps) == 2
        headwords = {s["headword"] for s in report.sample_overlaps}
        assert "pasian" in headwords
        assert "gam" in headwords

    def test_sample_translations_in_report(self, tmp_dicts):
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        d = report.to_dict()

        assert d["overlapping"] == 2
        assert d["master_total"] == 3
        assert d["canonical_total"] == 4
        assert d["overlap_pct_of_master"] == pytest.approx(66.67, abs=0.01)


class TestUniqueEntries:
    """Test that unique entries are preserved correctly."""

    def test_unique_to_master_preserved(self, tmp_dicts):
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        unique_headwords = {s["headword"] for s in report.unique_master_samples}
        assert "tui" in unique_headwords

    def test_unique_to_canonical_preserved(self, tmp_dicts):
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        unique_headwords = {s["headword"] for s in report.unique_canonical_samples}
        assert "vantung" in unique_headwords
        assert "topa" in unique_headwords


class TestMerge:
    """Test merge behavior when --fix is used."""

    def test_merge_prefers_master(self, tmp_dicts):
        """Master is authoritative: its translations take priority."""
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        merged = merge_duplicates(master, canonical, report)

        # "pasian" should exist with master entry first
        assert "pasian" in merged
        master_entry = merged["pasian"][0]
        assert _get_translations_flat(master_entry) == ["God"]

    def test_merge_adds_unique_canonical(self, tmp_dicts):
        """Unique-to-canonical entries are added to merged output."""
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        merged = merge_duplicates(master, canonical, report)

        assert "vantung" in merged
        assert "topa" in merged

    def test_merge_total_count(self, tmp_dicts):
        """Merged output has all unique headwords from both files."""
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        merged = merge_duplicates(master, canonical, report)

        # 3 unique headwords from master + 2 from canonical = 5
        assert len(merged) == 5


class TestReportStructure:
    """Test that the report JSON has the expected structure."""

    def test_report_to_dict_keys(self, tmp_dicts):
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        d = report.to_dict()

        required_keys = [
            "master_total", "canonical_total", "overlapping",
            "unique_master", "unique_canonical",
            "overlap_pct_of_master", "overlap_pct_of_canonical",
            "sample_overlaps", "unique_master_samples",
            "unique_canonical_samples",
        ]
        for key in required_keys:
            assert key in d, f"Missing key: {key}"


class TestEdgeCases:
    """Test edge cases with empty or minimal data."""

    def test_empty_dicts_handled(self, tmp_path: Path):
        master_file = tmp_path / "empty_master.jsonl"
        canonical_file = tmp_path / "empty_canonical.jsonl"
        master_file.touch()
        canonical_file.touch()

        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        assert report.overlapping == 0
        assert report.unique_master == 0
        assert report.unique_canonical == 0
        assert report.master_total == 0
        assert report.canonical_total == 0

    def test_one_empty_one_populated(self, tmp_path: Path):
        master_file = tmp_path / "m.jsonl"
        canonical_file = tmp_path / "c.jsonl"
        _write_jsonl(master_file, [
            {"zolai": "a", "english": ["one"], "source": "test",
             "english_clean": "one"}
        ])
        canonical_file.touch()

        master = load_dict(master_file)
        canonical = load_dict(canonical_file)
        report = find_duplicates(master, canonical)

        assert report.overlapping == 0
        assert report.unique_master == 1
        assert report.unique_canonical == 0

    def test_identical_dicts(self, tmp_path: Path):
        entries = [
            {"zolai": "pasian", "english": ["God"], "source": "bible",
             "english_clean": "God"},
        ]
        master_file = tmp_path / "m.jsonl"
        canonical_file = tmp_path / "c.jsonl"
        _write_jsonl(master_file, entries)
        _write_jsonl(canonical_file, entries)

        master = load_dict(master_file)
        canonical = load_dict(canonical_file)
        report = find_duplicates(master, canonical)

        assert report.overlapping == 1
        assert report.unique_master == 0
        assert report.unique_canonical == 0


class TestWriteDict:
    """Test write_dict round-trip."""

    def test_round_trip(self, tmp_path: Path, tmp_dicts):
        master_file, canonical_file, _, _ = tmp_dicts
        master = load_dict(master_file)
        canonical = load_dict(canonical_file)

        report = find_duplicates(master, canonical)
        merged = merge_duplicates(master, canonical, report)

        out_file = tmp_path / "merged.jsonl"
        count = write_dict(merged, out_file)
        assert count > 0

        reloaded = load_dict(out_file)
        assert len(reloaded) == len(merged)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_translations_flat(entry: dict) -> list[str]:
    for key in ("english", "translations"):
        val = entry.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [val]
    return []
