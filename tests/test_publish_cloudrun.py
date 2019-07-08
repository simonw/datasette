from click.testing import CliRunner
from datasette import cli
from unittest import mock
import json


@mock.patch("shutil.which")
def test_publish_cloudrun_requires_gcloud(mock_which):
    mock_which.return_value = False
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "cloudrun", "test.db"])
        assert result.exit_code == 1
        assert "Publishing to Google Cloud requires gcloud" in result.output


@mock.patch("shutil.which")
def test_publish_cloudrun_invalid_database(mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    result = runner.invoke(cli.cli, ["publish", "cloudrun", "woop.db"])
    assert result.exit_code == 2
    assert 'Path "woop.db" does not exist' in result.output


@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
def test_publish_cloudrun(mock_call, mock_output, mock_which):
    mock_output.return_value = "myproject"
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "cloudrun", "test.db"])
        assert 0 == result.exit_code
        tag = "gcr.io/{}/datasette".format(mock_output.return_value)
        mock_call.assert_has_calls(
            [
                mock.call("gcloud builds submit --tag {}".format(tag), shell=True),
                mock.call(
                    "gcloud beta run deploy --allow-unauthenticated --image {}".format(
                        tag
                    ),
                    shell=True,
                ),
            ]
        )


@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
def test_publish_cloudrun_plugin_secrets(mock_call, mock_output, mock_which):
    mock_which.return_value = True
    mock_output.return_value = "myproject"

    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli,
            [
                "publish",
                "cloudrun",
                "test.db",
                "--plugin-secret",
                "datasette-auth-github",
                "client_id",
                "x-client-id",
                "--show-files",
            ],
        )
        dockerfile = (
            result.output.split("==== Dockerfile ====\n")[1]
            .split("\n====================\n")[0]
            .strip()
        )
        expected = """FROM python:3.6
COPY . /app
WORKDIR /app

ENV DATASETTE_AUTH_GITHUB_CLIENT_ID 'x-client-id'
RUN pip install -U datasette
RUN datasette inspect test.db --inspect-file inspect-data.json
ENV PORT 8001
EXPOSE 8001
CMD datasette serve --host 0.0.0.0 -i test.db --cors --inspect-file inspect-data.json --metadata metadata.json --port $PORT""".strip()
        assert expected == dockerfile
        metadata = (
            result.output.split("=== metadata.json ===\n")[1]
            .split("\n==== Dockerfile ====\n")[0]
            .strip()
        )
        assert {
            "plugins": {
                "datasette-auth-github": {
                    "client_id": {"$env": "DATASETTE_AUTH_GITHUB_CLIENT_ID"}
                }
            }
        } == json.loads(metadata)
