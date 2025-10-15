from click.testing import CliRunner
from datasette import cli
from unittest import mock
import os
import pathlib
import pytest


class CaptureDockerfile:
    def __call__(self, _):
        self.captured = (pathlib.Path() / "Dockerfile").read_text()


EXPECTED_DOCKERFILE = """
FROM python:3.14-slim-trixie
COPY . /app
WORKDIR /app

ENV DATASETTE_SECRET 'sekrit'
RUN pip install -U datasette
RUN datasette inspect test.db --inspect-file inspect-data.json
ENV PORT {port}
EXPOSE {port}
CMD datasette serve --host 0.0.0.0 -i test.db --cors --inspect-file inspect-data.json --port $PORT
""".strip()


@pytest.mark.serial
@mock.patch("shutil.which")
@mock.patch("datasette.cli.call")
def test_package(mock_call, mock_which, tmp_path_factory):
    mock_which.return_value = True
    runner = CliRunner()
    capture = CaptureDockerfile()
    mock_call.side_effect = capture
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
    result = runner.invoke(cli.cli, ["package", "test.db", "--secret", "sekrit"])
    assert 0 == result.exit_code
    mock_call.assert_has_calls([mock.call(["docker", "build", "."])])
    assert EXPECTED_DOCKERFILE.format(port=8001) == capture.captured


@mock.patch("shutil.which")
@mock.patch("datasette.cli.call")
def test_package_with_port(mock_call, mock_which, tmp_path_factory):
    mock_which.return_value = True
    capture = CaptureDockerfile()
    mock_call.side_effect = capture
    runner = CliRunner()
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
    result = runner.invoke(
        cli.cli, ["package", "test.db", "-p", "8080", "--secret", "sekrit"]
    )
    assert 0 == result.exit_code
    assert EXPECTED_DOCKERFILE.format(port=8080) == capture.captured
