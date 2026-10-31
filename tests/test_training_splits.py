"""Group-aware, seeded train/val/test splitting (no pair may straddle splits)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from zolai.trainer.dataset import _group_key, _split_by_group
from zolai.trainer.manifest import content_version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "training"))

from create_training_splits import main as create_splits  # noqa: E402


def _pairs(n: int) -> list[dict]:
    """Bidirectional rows: two rows per underlying verse reference."""
    rows: list[dict] = []
    for i in range(n):
        ref = f"GEN 1:{i + 1}"
        rows.append({"reference": ref, "text": f"zo {i}", "direction": "en-zo"})
        rows.append({"reference": ref, "text": f"en {i}", "direction": "zo-en"})
    return rows


def _keys(rows: list[dict]) -> set[str]:
    return {_group_key(r) for r in rows}


class TestGroupKey:
    def test_reference_wins_over_text(self) -> None:
        assert _group_key({"reference": "GEN 1:1", "text": "a"}) == _group_key(
            {"reference": "GEN 1:1", "text": "b"}
        )

    def test_text_is_normalized_when_no_reference(self) -> None:
        assert _group_key({"text": "Ka  Lungdam"}) == _group_key({"text": "ka lungdam"})

    def test_rows_without_identity_are_never_merged(self) -> None:
        assert _group_key({"source": "x"}) != _group_key({"source": "x"})


class TestSplitByGroup:
    def test_no_reference_appears_in_two_splits(self) -> None:
        train, val, test = _split_by_group(_pairs(200), seed=42, val_ratio=0.1, test_ratio=0.1)
        assert _keys(train) & _keys(val) == set()
        assert _keys(train) & _keys(test) == set()
        assert _keys(val) & _keys(test) == set()

    def test_both_directions_land_in_the_same_split(self) -> None:
        train, val, test = _split_by_group(_pairs(50), seed=7, val_ratio=0.2, test_ratio=0.1)
        for split in (train, val, test):
            by_ref: dict[str, int] = {}
            for row in split:
                by_ref[row["reference"]] = by_ref.get(row["reference"], 0) + 1
            assert all(count == 2 for count in by_ref.values())

    def test_every_row_is_used_exactly_once(self) -> None:
        rows = _pairs(120)
        train, val, test = _split_by_group(rows, seed=1, val_ratio=0.1, test_ratio=0.1)
        assert len(train) + len(val) + len(test) == len(rows)

    def test_same_seed_is_reproducible(self) -> None:
        a = _split_by_group(_pairs(60), seed=42, val_ratio=0.2, test_ratio=0.1)
        b = _split_by_group(_pairs(60), seed=42, val_ratio=0.2, test_ratio=0.1)
        assert [r["text"] for r in a[0]] == [r["text"] for r in b[0]]
        assert [r["text"] for r in a[2]] == [r["text"] for r in b[2]]

    def test_dominant_group_cannot_empty_a_split(self) -> None:
        rows = [{"reference": f"R{i}", "text": f"t{i}"} for i in range(10)]
        rows += [{"reference": "HUGE", "text": f"h{i}"} for i in range(95)]
        train, val, test = _split_by_group(rows, seed=3, val_ratio=0.1, test_ratio=0.1)
        assert train and val and test
        huge_splits = sum("reference:HUGE" in _keys(part) for part in (train, val, test))
        assert huge_splits == 1


class TestContentVersion:
    """The manifest version is a hash of content, not of regeneration time."""

    def test_same_content_same_version_regardless_of_order(self) -> None:
        files = {
            "train": {"exists": True, "sha256": "aa", "bytes": 3},
            "test": {"exists": True, "sha256": "bb", "bytes": 5},
        }
        reversed_files = dict(reversed(list(files.items())))
        assert content_version(files) == content_version(reversed_files)

    def test_changed_bytes_change_the_version(self) -> None:
        before = {"train": {"exists": True, "sha256": "aa", "bytes": 3}}
        after = {"train": {"exists": True, "sha256": "ab", "bytes": 3}}
        assert content_version(before) != content_version(after)

    def test_missing_file_is_part_of_the_identity(self) -> None:
        present = {"train": {"exists": True, "sha256": "aa", "bytes": 3},
                   "test": {"exists": True, "sha256": "bb", "bytes": 5}}
        absent = {"train": {"exists": True, "sha256": "aa", "bytes": 3},
                  "test": {"exists": False}}
        assert content_version(present) != content_version(absent)


class TestCreateSplitsCli:
    """The legacy split script now uses the group splitter and its output is
    directly accepted by the leakage gate."""

    def _write_input(self, path: Path, n: int) -> list[str]:
        refs = []
        with open(path, "w", encoding="utf-8") as f:
            for i in range(n):
                ref = f"GEN 1:{i + 1}"
                refs.append(ref)
                for direction, text in (("en-zo", f"zo {i}"), ("zo-en", f"en {i}")):
                    f.write(json.dumps({"reference": ref, "text": text,
                                        "direction": direction}) + "\n")
        return refs

    def test_paired_rows_stay_in_one_split(self, tmp_path: Path) -> None:
        src = tmp_path / "input.jsonl"
        self._write_input(src, 100)
        out = tmp_path / "out"
        assert create_splits(["--input", str(src), "--out-dir", str(out), "--seed", "42"]) == 0

        by_ref: dict[str, set[str]] = {}
        for name in ("train", "val", "test"):
            with open(out / f"llm_{name}.jsonl", encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    assert row["split"] == name
                    by_ref.setdefault(row["reference"], set()).add(name)
        assert by_ref and all(len(splits) == 1 for splits in by_ref.values())

    def test_output_passes_the_leakage_gate(self, tmp_path: Path) -> None:
        from zolai.trainer.leakage import check_leakage

        src = tmp_path / "input.jsonl"
        self._write_input(src, 60)
        out = tmp_path / "out"
        assert create_splits(["--input", str(src), "--out-dir", str(out)]) == 0
        report = check_leakage({name: out / f"llm_{name}.jsonl"
                                for name in ("train", "val", "test")})
        assert report["ok"] is True

    def test_missing_input_exits_2(self, tmp_path: Path) -> None:
        assert create_splits(["--input", str(tmp_path / "nope.jsonl"),
                              "--out-dir", str(tmp_path / "out")]) == 2

    def test_malformed_input_exits_2(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.jsonl"
        bad.write_text("{not json}\n", encoding="utf-8")
        assert create_splits(["--input", str(bad), "--out-dir", str(tmp_path / "out")]) == 2
