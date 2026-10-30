"""Group-aware, seeded train/val/test splitting (no pair may straddle splits)."""

from __future__ import annotations

from zolai.trainer.dataset import _group_key, _split_by_group


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
