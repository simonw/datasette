import black
from click.testing import CliRunner
from pathlib import Path
import pytest
import sys

code_root = Path(__file__).parent.parent


def test_black():
    runner = CliRunner()
    result = runner.invoke(black.main, [str(code_root), "--check"])
    assert result.exit_code == 0, result.output
