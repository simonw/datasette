import sys
from pathlib import Path

import black
import pytest
from click.testing import CliRunner

code_root = Path(__file__).parent.parent


def test_black():
    runner = CliRunner()
    result = runner.invoke(black.main, [str(code_root), "--check"])
    assert result.exit_code == 0, result.output
