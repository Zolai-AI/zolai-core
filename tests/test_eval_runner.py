"""Tests for the evaluation runner (ZolaiBench v0.1)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from zolai.eval.runner import app as runner_app


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
