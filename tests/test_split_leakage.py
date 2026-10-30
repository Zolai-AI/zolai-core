"""Split leakage gate: train/val/test and eval-vs-train must be disjoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "training"))

from check_split_leakage import main  # noqa: E402

from zolai.trainer.leakage import (  # noqa: E402
    check_leakage,
    record_fingerprint,
)


def _write(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


class TestFingerprint:
    def test_normalizes_case_whitespace_and_width(self) -> None:
        a = record_fingerprint({"zolai": "Ka lungdam e."})
        b = record_fingerprint({"text": "  ka   LUNGDAM\ne.\u3000"})
        assert a == b

    def test_prefers_the_zolai_side_over_english(self) -> None:
        assert record_fingerprint(
            {"zolai": "A", "english": "different"}
        ) == record_fingerprint({"zolai": "A", "english": "other"})

    def test_returns_none_without_text(self) -> None:
        assert record_fingerprint({"source": "x"}) is None


class TestCheckLeakage:
    def test_disjoint_splits_pass(self, tmp_path: Path) -> None:
        train = _write(tmp_path / "train.jsonl", [{"zolai": "one"}, {"zolai": "two"}])
        val = _write(tmp_path / "val.jsonl", [{"zolai": "three"}])
        test = _write(tmp_path / "test.jsonl", [{"zolai": "four"}])
        report = check_leakage({"train": train, "val": val, "test": test})
        assert report["ok"] is True
        assert report["splits"] == {"train": 2, "val": 1, "test": 1}

    def test_shared_row_is_reported_with_line_numbers(self, tmp_path: Path) -> None:
        train = _write(tmp_path / "train.jsonl", [{"zolai": "shared"}, {"zolai": "x"}])
        test = _write(tmp_path / "test.jsonl", [{"zolai": "y"}, {"zolai": "SHARED"}])
        report = check_leakage({"train": train, "test": test})
        assert report["ok"] is False
        [overlap] = report["overlaps"]
        assert (overlap["left"], overlap["right"], overlap["count"]) == ("train", "test", 1)
        assert overlap["examples"] == [{"left_line": 1, "right_line": 2}]

    def test_eval_rows_must_not_appear_in_train(self, tmp_path: Path) -> None:
        train = _write(tmp_path / "train.jsonl", [{"zolai": "seen"}])
        eval_set = _write(tmp_path / "eval.jsonl", [{"zolai": "seen"}])
        report = check_leakage({"train": train}, eval_vs={"gold": eval_set})
        assert report["ok"] is False
        assert report["overlaps"][0]["right"] == "eval:gold"

    def test_blank_lines_and_textless_rows_are_skipped(self, tmp_path: Path) -> None:
        train = _write(tmp_path / "train.jsonl", [{"source": "no-text"}, {"zolai": "a"}])
        with open(train, "a", encoding="utf-8") as f:
            f.write("\n")
        report = check_leakage({"train": train})
        assert report["splits"]["train"] == 1


class TestCli:
    def test_exit_1_when_splits_overlap(self, tmp_path: Path, capsys) -> None:
        train = _write(tmp_path / "train.jsonl", [{"zolai": "same"}])
        val = _write(tmp_path / "val.jsonl", [{"zolai": "same"}])
        code = main(["--train", str(train), "--val", str(val), "--eval-dir", str(tmp_path / "nope")])
        assert code == 1
        assert "LEAKAGE DETECTED" in capsys.readouterr().out

    def test_exit_0_when_clean(self, tmp_path: Path) -> None:
        train = _write(tmp_path / "train.jsonl", [{"zolai": "a"}])
        val = _write(tmp_path / "val.jsonl", [{"zolai": "b"}])
        assert main(["--train", str(train), "--val", str(val), "--eval-dir", str(tmp_path / "nope")]) == 0

    def test_absent_training_dir_is_an_explicit_skip_not_a_pass(self, tmp_path: Path, capsys) -> None:
        code = main(["--training-dir", str(tmp_path / "missing"), "--json"])
        assert code == 0
        report = json.loads(capsys.readouterr().out)
        assert "nothing to check" in report["skipped"]

    @pytest.mark.parametrize("content", ["{not json}\n"])
    def test_unreadable_input_exits_2(self, tmp_path: Path, content: str) -> None:
        bad = tmp_path / "train.jsonl"
        bad.write_text(content, encoding="utf-8")
        assert main(["--train", str(bad), "--eval-dir", str(tmp_path / "nope")]) == 2
