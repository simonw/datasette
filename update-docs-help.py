from click.testing import CliRunner
from datasette.cli import cli
from pathlib import Path

docs_path = Path(__file__).parent / "docs"

includes = (
    ("serve", "datasette-serve-help.txt"),
    ("package", "datasette-package-help.txt"),
    ("publish heroku", "datasette-publish-heroku-help.txt"),
    ("publish cloudrun", "datasette-publish-cloudrun-help.txt"),
)


def update_help_includes():
    for name, filename in includes:
        runner = CliRunner()
        result = runner.invoke(cli, name.split() + ["--help"], terminal_width=88)
        actual = f"$ datasette {name} --help\n\n{result.output}"
        actual = actual.replace("Usage: cli ", "Usage: datasette ")
        (docs_path / filename).write_text(actual)


if __name__ == "__main__":
    update_help_includes()
