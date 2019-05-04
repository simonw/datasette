from click.testing import CliRunner
from pathlib import Path
import pytest
import sys

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
