from click.testing import CliRunner
from datasette import cli
from unittest import mock
import json
import os
import pytest
import textwrap


@pytest.mark.serial
@mock.patch("shutil.which")
def test_publish_cloudrun_requires_gcloud(mock_which, tmp_path_factory):
    mock_which.return_value = False
    runner = CliRunner()
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
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


@pytest.mark.serial
@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
@mock.patch("datasette.publish.cloudrun.get_existing_services")
def test_publish_cloudrun_prompts_for_service(
    mock_get_existing_services, mock_call, mock_output, mock_which, tmp_path_factory
):
    mock_get_existing_services.return_value = [
        {"name": "existing", "created": "2019-01-01", "url": "http://www.example.com/"}
    ]
    mock_output.return_value = "myproject"
    mock_which.return_value = True
    runner = CliRunner()
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
    result = runner.invoke(
        cli.cli, ["publish", "cloudrun", "test.db"], input="input-service"
    )
    assert (
        "Please provide a service name for this deployment\n\n"
        "Using an existing service name will over-write it\n\n"
        "Your existing services:\n\n"
        "  existing - created 2019-01-01 - http://www.example.com/\n\n"
        "Service name: input-service"
    ) == result.output.strip()
    assert 0 == result.exit_code
    tag = "gcr.io/myproject/datasette-input-service"
    mock_call.assert_has_calls(
        [
            mock.call(f"gcloud builds submit --tag {tag}", shell=True),
            mock.call(
                "gcloud run deploy --allow-unauthenticated --platform=managed --image {} input-service".format(
                    tag
                ),
                shell=True,
            ),
        ]
    )


@pytest.mark.serial
@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
def test_publish_cloudrun(mock_call, mock_output, mock_which, tmp_path_factory):
    mock_output.return_value = "myproject"
    mock_which.return_value = True
    runner = CliRunner()
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
    result = runner.invoke(
        cli.cli, ["publish", "cloudrun", "test.db", "--service", "test"]
    )
    assert 0 == result.exit_code
    tag = f"gcr.io/{mock_output.return_value}/datasette-test"
    mock_call.assert_has_calls(
        [
            mock.call(f"gcloud builds submit --tag {tag}", shell=True),
            mock.call(
                "gcloud run deploy --allow-unauthenticated --platform=managed --image {} test".format(
                    tag
                ),
                shell=True,
            ),
        ]
    )


@pytest.mark.serial
@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
@pytest.mark.parametrize(
    "memory,cpu,timeout,min_instances,max_instances,expected_gcloud_args",
    [
        ["1Gi", None, None, None, None, "--memory 1Gi"],
        ["2G", None, None, None, None, "--memory 2G"],
        ["256Mi", None, None, None, None, "--memory 256Mi"],
        [
            "4",
            None,
            None,
            None,
            None,
            None,
        ],
        [
            "GB",
            None,
            None,
            None,
            None,
            None,
        ],
        [None, 1, None, None, None, "--cpu 1"],
        [None, 2, None, None, None, "--cpu 2"],
        [None, 3, None, None, None, None],
        [None, 4, None, None, None, "--cpu 4"],
        ["2G", 4, None, None, None, "--memory 2G --cpu 4"],
        [None, None, 1800, None, None, "--timeout 1800"],
        [None, None, None, 2, None, "--min-instances 2"],
        [None, None, None, 2, 4, "--min-instances 2 --max-instances 4"],
        [None, 2, None, None, 4, "--cpu 2 --max-instances 4"],
    ],
)
def test_publish_cloudrun_memory_cpu(
    mock_call,
    mock_output,
    mock_which,
    memory,
    cpu,
    timeout,
    min_instances,
    max_instances,
    expected_gcloud_args,
    tmp_path_factory,
):
    mock_output.return_value = "myproject"
    mock_which.return_value = True
    runner = CliRunner()
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
    args = ["publish", "cloudrun", "test.db", "--service", "test"]
    if memory:
        args.extend(["--memory", memory])
    if cpu:
        args.extend(["--cpu", str(cpu)])
    if timeout:
        args.extend(["--timeout", str(timeout)])
    result = runner.invoke(cli.cli, args)
    if expected_gcloud_args is None:
        assert 2 == result.exit_code
        return
    assert 0 == result.exit_code
    tag = f"gcr.io/{mock_output.return_value}/datasette-test"
    expected_call = (
        "gcloud run deploy --allow-unauthenticated --platform=managed"
        " --image {} test".format(tag)
    )
    expected_build_call = f"gcloud builds submit --tag {tag}"
    if memory:
        expected_call += " --memory {}".format(memory)
    if cpu:
        expected_call += " --cpu {}".format(cpu)
    if timeout:
        expected_build_call += f" --timeout {timeout}"
    mock_call.assert_has_calls(
        [
            mock.call(expected_build_call, shell=True),
            mock.call(
                expected_call,
                shell=True,
            ),
        ]
    )


@pytest.mark.serial
@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
def test_publish_cloudrun_plugin_secrets(
    mock_call, mock_output, mock_which, tmp_path_factory
):
    mock_which.return_value = True
    mock_output.return_value = "myproject"

    runner = CliRunner()
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
    with open("metadata.yml", "w") as fp:
        fp.write(
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
    assert result.exit_code == 0
    dockerfile = (
        result.output.split("==== Dockerfile ====\n")[1]
        .split("\n====================\n")[0]
        .strip()
    )
    expected = textwrap.dedent(
        r"""
    FROM python:3.11.0-slim-bullseye
    COPY . /app
    WORKDIR /app

    ENV DATASETTE_AUTH_GITHUB_CLIENT_ID 'x-client-id'
    ENV DATASETTE_SECRET 'x-secret'
    RUN pip install -U datasette
    RUN datasette inspect test.db --inspect-file inspect-data.json
    ENV PORT 8001
    EXPOSE 8001
    CMD datasette serve --host 0.0.0.0 -i test.db --cors --inspect-file inspect-data.json --metadata metadata.json --setting force_https_urls on --port $PORT"""
    ).strip()
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
                "client_id": {"$env": "DATASETTE_AUTH_GITHUB_CLIENT_ID"},
                "foo": "bar",
            },
        },
    } == json.loads(metadata)


@pytest.mark.serial
@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
def test_publish_cloudrun_apt_get_install(
    mock_call, mock_output, mock_which, tmp_path_factory
):
    mock_which.return_value = True
    mock_output.return_value = "myproject"

    runner = CliRunner()
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
    result = runner.invoke(
        cli.cli,
        [
            "publish",
            "cloudrun",
            "test.db",
            "--service",
            "datasette",
            "--show-files",
            "--secret",
            "x-secret",
            "--apt-get-install",
            "ripgrep",
            "--spatialite",
        ],
    )
    assert result.exit_code == 0
    dockerfile = (
        result.output.split("==== Dockerfile ====\n")[1]
        .split("\n====================\n")[0]
        .strip()
    )
    expected = textwrap.dedent(
        r"""
    FROM python:3.11.0-slim-bullseye
    COPY . /app
    WORKDIR /app

    RUN apt-get update && \
        apt-get install -y ripgrep python3-dev gcc libsqlite3-mod-spatialite && \
        rm -rf /var/lib/apt/lists/*

    ENV DATASETTE_SECRET 'x-secret'
    ENV SQLITE_EXTENSIONS '/usr/lib/x86_64-linux-gnu/mod_spatialite.so'
    RUN pip install -U datasette
    RUN datasette inspect test.db --inspect-file inspect-data.json
    ENV PORT 8001
    EXPOSE 8001
    CMD datasette serve --host 0.0.0.0 -i test.db --cors --inspect-file inspect-data.json --setting force_https_urls on --port $PORT
    """
    ).strip()
    assert expected == dockerfile


@pytest.mark.serial
@mock.patch("shutil.which")
@mock.patch("datasette.publish.cloudrun.check_output")
@mock.patch("datasette.publish.cloudrun.check_call")
@pytest.mark.parametrize(
    "extra_options,expected",
    [
        ("", "--setting force_https_urls on"),
        (
            "--setting base_url /foo",
            "--setting base_url /foo --setting force_https_urls on",
        ),
        ("--setting force_https_urls off", "--setting force_https_urls off"),
    ],
)
def test_publish_cloudrun_extra_options(
    mock_call, mock_output, mock_which, extra_options, expected, tmp_path_factory
):
    mock_which.return_value = True
    mock_output.return_value = "myproject"

    runner = CliRunner()
    os.chdir(tmp_path_factory.mktemp("runner"))
    with open("test.db", "w") as fp:
        fp.write("data")
    result = runner.invoke(
        cli.cli,
        [
            "publish",
            "cloudrun",
            "test.db",
            "--service",
            "datasette",
            "--show-files",
            "--extra-options",
            extra_options,
        ],
    )
    assert result.exit_code == 0
    dockerfile = (
        result.output.split("==== Dockerfile ====\n")[1]
        .split("\n====================\n")[0]
        .strip()
    )
    last_line = dockerfile.split("\n")[-1]
    extra_options = (
        last_line.split("--inspect-file inspect-data.json")[1]
        .split("--port")[0]
        .strip()
    )
    assert extra_options == expected
