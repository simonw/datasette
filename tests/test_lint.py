import io
import sys
from pathlib import Path

import isort
import pytest
from click.testing import CliRunner

code_root = Path(__file__).parent.parent


@pytest.mark.skipif(
    sys.version_info[:2] < (3, 6), reason="Black requires Python 3.6 or later"
)
def test_black():
    # Do not import at top of module because Python 3.5 will not have it installed
    import black

    runner = CliRunner()
    result = runner.invoke(
        black.main, [str(code_root / "tests"), str(code_root / "datasette"), "--check"]
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "path",
    list(code_root.glob("tests/**/*.py")) + list(code_root.glob("datasette/**/*.py")),
)
def test_isort(path):
    # Have to capture stdout because isort uses print() directly
    stdout = sys.stdout
    sys.stdout = io.StringIO()
    result = isort.SortImports(path, check=True)
    assert (
        not result.incorrectly_sorted
    ), "{} has incorrectly sorted imports, fix with 'isort -rc tests && isort -rc datasette && black tests datasette'".format(
        path
    )
    sys.stdout = stdout
