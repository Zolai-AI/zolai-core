"""Tests for the evaluation runner (ZolaiBench v0.1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from zolai.config import config
from zolai.eval.runner import app as runner_app
from zolai.eval.runner import load_pos_predictor, run_pos_eval

#: Every command here initialises (and ``init-gold`` writes to) the eval store,
#: so the suite runs against a scratch database instead of the corpus.
pytestmark = pytest.mark.usefixtures("scratch_db")

runner = CliRunner()


class TestEvalRunner:
    """Tests for the eval CLI runner."""

    def test_list_tasks(self) -> None:
        """Test listing evaluation tasks."""
        result = runner.invoke(runner_app, ["list-tasks"])
        assert result.exit_code == 0
        assert "tokenization" in result.output
        assert "pos" in result.output
        assert "morph" in result.output
        assert "grammar" in result.output

    def test_stats(self) -> None:
        """Test stats command."""
        result = runner.invoke(runner_app, ["stats"])
        assert result.exit_code == 0
        assert "Evaluation Database Stats" in result.output

    def test_run_unknown_task(self) -> None:
        """Test running unknown task - gracefully reports no gold data."""
        result = runner.invoke(runner_app, ["run", "invalid_task_xyz"])
        assert result.exit_code == 0
        assert "No gold data" in result.output

    def test_run_all_tasks(self) -> None:
        """Test running all tasks (uses real DB which has data now)."""
        result = runner.invoke(runner_app, ["run", "all"])
        assert result.exit_code == 0
        assert "tokenization" in result.output or "pos" in result.output


class TestEvalInitGold:
    """Tests for init-gold command."""

    def test_init_gold_missing_file(self) -> None:
        """Test init-gold with missing file."""
        result = runner.invoke(runner_app, ["init-gold", "nonexistent_task"])
        assert result.exit_code != 0
        assert "Gold file not found" in result.output or "Unknown task" in result.output


class TestEvalExport:
    """Tests for export command."""

    def test_export_unknown_task(self) -> None:
        """Test export with unknown task."""
        result = runner.invoke(runner_app, ["export", "unknown_task"])
        assert result.exit_code != 0
        assert "Unknown task" in result.output or "No gold data" in result.output


class TestPosSystemSelection:
    """`--system` decides what is being scored, and the label must say so."""

    def test_unknown_system_names_the_choices_instead_of_guessing(self) -> None:
        result = runner.invoke(runner_app, ["run", "pos", "--system", "transformer"])
        assert result.exit_code != 0
        assert "Unknown --system" in result.output
        assert "placeholder" in result.output and "crf" in result.output

    def test_placeholder_is_the_default(self) -> None:
        from zolai.eval.runner import POS_SYSTEMS, load_pos_predictor

        label, predict = load_pos_predictor("placeholder")
        assert label == POS_SYSTEMS["placeholder"]
        assert predict("na in zong") == ["X", "X", "X"]

    def test_a_model_pickled_from_a_script_still_loads(self, tmp_path, monkeypatch) -> None:
        """The artifact on disk names `__main__.POSTaggerModel`.

        train.py is run as a script, so that is what it saved — and a library
        reader has no such module. This reproduces the shape rather than
        depending on the trained model being present.
        """
        import pickle

        class Model:
            __name__ = "POSTaggerModel"
            __qualname__ = "POSTaggerModel"
            __module__ = "__main__"
            tagset = ["NOUN"]

        # pickle refuses a class it cannot find again under the name it records, so
        # the fake has to be reachable as `__main__.POSTaggerModel` at dump time.
        monkeypatch.setattr(sys.modules["__main__"], "POSTaggerModel", Model, raising=False)
        blob = pickle.dumps(Model())

        models = tmp_path / "models"
        models.mkdir()
        (models / "pos_tagger_crf.pkl").write_bytes(blob)

        monkeypatch.setattr(config.paths, "root", Path(models.parent))
        # Imported here rather than at module scope: zolai.pos_tagger.train pulls in
        # sklearn-crfsuite, and the eval CLI tests should not need a training stack.
        from zolai.pos_tagger.train import POSTaggerModel

        label, predict = load_pos_predictor("crf")
        assert "(in-sample)" in label
        # The redirect is the fix: unpickling resolves the name to the importable
        # class instead of raising "'__main__' has no attribute POSTaggerModel".
        assert isinstance(predict.__self__, POSTaggerModel)

    def test_missing_model_names_the_path_it_looked_at(self, tmp_path, monkeypatch) -> None:
        """Not via the CLI: an empty lane `continue`s before any predictor is built,
        so the CLI would exit 0 and the assertion would prove nothing."""
        monkeypatch.setattr(config.paths, "root", Path(tmp_path))
        with pytest.raises(typer.Exit) as exc:
            load_pos_predictor("crf")
        assert exc.value.exit_code == 1

    def test_tagged_coverage_is_reported(self) -> None:
        """Macro F1 needs a denominator: how much of the lane is actually tagged."""
        items = [
            SimpleNamespace(gold_annotation=json.dumps({
                "text": "na in zong zo",
                "tokens": [{"text": "na", "pos": "PRON"}, {"text": "in", "pos": "X"},
                           {"text": "zong", "pos": "NOUN"}, {"text": "zo", "pos": "ASP"}],
            }))
        ]
        result = run_pos_eval(items, system="placeholder")
        # Three of the four gold tokens carry a real tag; the fourth is the `X`
        # the annotation pass never resolved. Macro F1 averages over classes that
        # appear, so without this the reader assumes 4 tagged tokens were in play.
        assert result.metrics["tagged_coverage"] == 0.75
        assert result.system == "placeholder:all-X"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
