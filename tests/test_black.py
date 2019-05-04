import black
from click.testing import CliRunner
from pathlib import Path
import pytest
import sys

code_root = Path(__file__).parent.parent


# @pytest.mark.skipif(
#     sys.version_info[:2] > (3, 6),
#     reason="Breaks on 3.7 at the moment, but it only needs to run under one Python version",
# )
def test_black():
    runner = CliRunner()
    result = runner.invoke(
        black.main, [str(code_root / "tests"), str(code_root / "datasette"), "--check"]
    )
    assert result.exit_code == 0, result.output
