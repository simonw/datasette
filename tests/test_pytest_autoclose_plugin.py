"""
Tests for datasette._pytest_plugin — the pytest plugin that auto-closes
Datasette instances constructed inside test bodies.

These tests drive a real pytest session in a subprocess so the plugin
operates exactly as it would for a downstream consumer.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


def _run_pytest(tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-v", str(tmp_path)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )


def test_auto_close_of_instances_made_in_test_body(tmp_path):
    # Two ordered tests:
    #   test_a makes a Datasette() and stashes a hard reference
    #   test_b asserts that the hard-reffed instance was closed by the plugin
    (tmp_path / "test_sample.py").write_text(textwrap.dedent("""
            from datasette.app import Datasette

            _stash = {}

            def test_a():
                ds = Datasette(memory=True)
                _stash["ds"] = ds
                assert ds._closed is False

            def test_b():
                assert _stash["ds"]._closed is True
            """))
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_fixture_scoped_instance_is_not_closed(tmp_path):
    # A module-scoped fixture instance must survive across tests in the module.
    (tmp_path / "test_fixture.py").write_text(textwrap.dedent("""
            import pytest
            from datasette.app import Datasette

            @pytest.fixture(scope="module")
            def ds():
                return Datasette(memory=True)

            def test_first(ds):
                assert ds._closed is False

            def test_second(ds):
                # Still alive because the plugin only tracks instances
                # constructed during pytest_runtest_call, not during fixture
                # setup.
                assert ds._closed is False
            """))
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_opt_out_via_ini(tmp_path):
    # datasette_autoclose = false should leave instances untouched.
    (tmp_path / "pytest.ini").write_text(textwrap.dedent("""
            [pytest]
            datasette_autoclose = false
            """).strip())
    (tmp_path / "test_optout.py").write_text(textwrap.dedent("""
            from datasette.app import Datasette

            _stash = {}

            def test_a():
                ds = Datasette(memory=True)
                _stash["ds"] = ds

            def test_b():
                # Opt-out: plugin must not have closed it.
                assert _stash["ds"]._closed is False
                _stash["ds"].close()
            """))
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
