from datasette.cli import cli, serve
from datasette.plugins import pm
from click.testing import CliRunner
import textwrap
import json


def test_serve_with_get(tmp_path_factory):
    plugins_dir = tmp_path_factory.mktemp("plugins_for_serve_with_get")
    (plugins_dir / "init_for_serve_with_get.py").write_text(
        textwrap.dedent(
            """
        from datasette import hookimpl

        @hookimpl
        def startup(datasette):
            with open("{}", "w") as fp:
                fp.write("hello")
    """.format(
                str(plugins_dir / "hello.txt")
            ),
        ),
        "utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "serve",
            "--memory",
            "--plugins-dir",
            str(plugins_dir),
            "--get",
            "/_memory.json?sql=select+sqlite_version()",
        ],
    )
    assert 0 == result.exit_code, result.output
    assert {
        "database": "_memory",
        "truncated": False,
        "columns": ["sqlite_version()"],
    }.items() <= json.loads(result.output).items()

    # The plugin should have created hello.txt
    assert (plugins_dir / "hello.txt").read_text() == "hello"

    # Annoyingly that new test plugin stays resident - we need
    # to manually unregister it to avoid conflict with other tests
    to_unregister = [
        p for p in pm.get_plugins() if p.__name__ == "init_for_serve_with_get.py"
    ][0]
    pm.unregister(to_unregister)


def test_serve_with_get_exit_code_for_error(tmp_path_factory):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "serve",
            "--memory",
            "--get",
            "/this-is-404",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "404" in result.output
