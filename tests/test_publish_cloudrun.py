from click.testing import CliRunner
from datasette import cli
from unittest import mock
import json
import pytest
import textwrap


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
    assert "Path 'woop.db' does not exist" in result.output


@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
@mock.patch("datasette.publish.cloudrun.get_existing_services")
def test_publish_cloudrun_prompts_for_service(
    mock_get_existing_services, mock_call, mock_output, mock_which
):
    mock_get_existing_services.return_value = [
        {"name": "existing", "created": "2019-01-01", "url": "http://www.example.com/"}
    ]
    mock_output.return_value = "myproject"
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli, ["publish", "cloudrun", "test.db"], input="input-service"
        )
        assert (
            """
Please provide a service name for this deployment

Using an existing service name will over-write it

Your existing services:

  existing - created 2019-01-01 - http://www.example.com/

Service name: input-service
""".strip()
            == result.output.strip()
        )
        assert 0 == result.exit_code
        tag = "gcr.io/myproject/datasette"
        mock_call.assert_has_calls(
            [
                mock.call("gcloud builds submit --tag {}".format(tag), shell=True),
                mock.call(
                    "gcloud run deploy --allow-unauthenticated --platform=managed --image {} input-service".format(
                        tag
                    ),
                    shell=True,
                ),
            ]
        )


@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
def test_publish_cloudrun(mock_call, mock_output, mock_which):
    mock_output.return_value = "myproject"
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli, ["publish", "cloudrun", "test.db", "--service", "test"]
        )
        assert 0 == result.exit_code
        tag = "gcr.io/{}/datasette".format(mock_output.return_value)
        mock_call.assert_has_calls(
            [
                mock.call("gcloud builds submit --tag {}".format(tag), shell=True),
                mock.call(
                    "gcloud run deploy --allow-unauthenticated --platform=managed --image {} test".format(
                        tag
                    ),
                    shell=True,
                ),
            ]
        )


@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
@pytest.mark.parametrize(
    "memory,should_fail",
    [["1Gi", False], ["2G", False], ["256Mi", False], ["4", True], ["GB", True],],
)
def test_publish_cloudrun_memory(
    mock_call, mock_output, mock_which, memory, should_fail
):
    mock_output.return_value = "myproject"
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli,
            ["publish", "cloudrun", "test.db", "--service", "test", "--memory", memory],
        )
        if should_fail:
            assert 2 == result.exit_code
            return
        assert 0 == result.exit_code
        tag = "gcr.io/{}/datasette".format(mock_output.return_value)
        mock_call.assert_has_calls(
            [
                mock.call("gcloud builds submit --tag {}".format(tag), shell=True),
                mock.call(
                    "gcloud run deploy --allow-unauthenticated --platform=managed --image {} test --memory {}".format(
                        tag, memory
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
        open("metadata.yml", "w").write(
            textwrap.dedent(
                """
                title: Hello from metadata YAML
                plugins:
                  datasette-auth-github:
                    foo: bar
                """
            ).strip()
        )
        result = runner.invoke(
            cli.cli,
            [
                "publish",
                "cloudrun",
                "test.db",
                "--metadata",
                "metadata.yml",
                "--service",
                "datasette",
                "--plugin-secret",
                "datasette-auth-github",
                "client_id",
                "x-client-id",
                "--show-files",
                "--secret",
                "x-secret",
            ],
        )
        dockerfile = (
            result.output.split("==== Dockerfile ====\n")[1]
            .split("\n====================\n")[0]
            .strip()
        )
        expected = """FROM python:3.8
COPY . /app
WORKDIR /app

ENV DATASETTE_AUTH_GITHUB_CLIENT_ID 'x-client-id'
ENV DATASETTE_SECRET 'x-secret'
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
            "title": "Hello from metadata YAML",
            "plugins": {
                "datasette-auth-github": {
                    "foo": "bar",
                    "client_id": {"$env": "DATASETTE_AUTH_GITHUB_CLIENT_ID"},
                }
            },
        } == json.loads(metadata)
