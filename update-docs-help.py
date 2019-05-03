from click.testing import CliRunner
from datasette.cli import cli
from pathlib import Path

docs_path = Path(__file__).parent / "docs"

includes = (
    ("serve", "datasette-serve-help.txt"),
    ("package", "datasette-package-help.txt"),
    ("publish now", "datasette-publish-now-help.txt"),
    ("publish heroku", "datasette-publish-heroku-help.txt"),
    ("publish cloudrun", "datasette-publish-cloudrun-help.txt"),
)


def update_help_includes():
    for name, filename in includes:
        runner = CliRunner()
        result = runner.invoke(
            cli, name.split() + ["--help"], terminal_width=88
        )
        actual = "$ datasette {} --help\n\n{}".format(
            name, result.output
        )
        actual = actual.replace('Usage: cli ', 'Usage: datasette ')
        open(docs_path / filename, "w").write(actual)


if __name__ == "__main__":
    update_help_includes()
