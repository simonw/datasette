from click.testing import CliRunner
from datasette import cli
from unittest import mock


@mock.patch("shutil.which")
def test_publish_heroku_requires_heroku(mock_which):
    mock_which.return_value = False
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "heroku", "test.db"])
        assert result.exit_code == 1
        assert "Publishing to Heroku requires heroku" in result.output


@mock.patch("shutil.which")
@mock.patch("datasette.publish.heroku.check_output")
@mock.patch("datasette.publish.heroku.call")
def test_publish_heroku_installs_plugin(mock_call, mock_check_output, mock_which):
    mock_which.return_value = True
    mock_check_output.side_effect = lambda s: {"['heroku', 'plugins']": b""}[repr(s)]
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("t.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "heroku", "t.db"], input="y\n")
        assert 0 != result.exit_code
    mock_check_output.assert_has_calls(
        [mock.call(["heroku", "plugins"]), mock.call(["heroku", "apps:list", "--json"])]
    )
    mock_call.assert_has_calls(
        [mock.call(["heroku", "plugins:install", "heroku-builds"])]
    )


@mock.patch("shutil.which")
def test_publish_heroku_invalid_database(mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    result = runner.invoke(cli.cli, ["publish", "heroku", "woop.db"])
    assert result.exit_code == 2
    assert "Path 'woop.db' does not exist" in result.output


@mock.patch("shutil.which")
@mock.patch("datasette.publish.heroku.check_output")
@mock.patch("datasette.publish.heroku.call")
def test_publish_heroku(mock_call, mock_check_output, mock_which):
    mock_which.return_value = True
    mock_check_output.side_effect = lambda s: {
        "['heroku', 'plugins']": b"heroku-builds",
        "['heroku', 'apps:list', '--json']": b"[]",
        "['heroku', 'apps:create', 'datasette', '--json']": b'{"name": "f"}',
    }[repr(s)]
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "heroku", "test.db"])
        assert 0 == result.exit_code, result.output
        mock_call.assert_has_calls(
            [
                mock.call(["heroku", "config:set", "-a", "f", "WEB_CONCURRENCY=1",]),
                mock.call(
                    ["heroku", "builds:create", "-a", "f", "--include-vcs-ignore"]
                ),
            ]
        )


@mock.patch("shutil.which")
@mock.patch("datasette.publish.heroku.check_output")
@mock.patch("datasette.publish.heroku.call")
def test_publish_heroku_plugin_secrets(mock_call, mock_check_output, mock_which):
    mock_which.return_value = True
    mock_check_output.side_effect = lambda s: {
        "['heroku', 'plugins']": b"heroku-builds",
        "['heroku', 'apps:list', '--json']": b"[]",
        "['heroku', 'apps:create', 'datasette', '--json']": b'{"name": "f"}',
    }[repr(s)]
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli,
            [
                "publish",
                "heroku",
                "test.db",
                "--plugin-secret",
                "datasette-auth-github",
                "client_id",
                "x-client-id",
            ],
        )
        assert 0 == result.exit_code, result.output
        mock_call.assert_has_calls(
            [
                mock.call(
                    [
                        "heroku",
                        "config:set",
                        "-a",
                        "f",
                        "DATASETTE_AUTH_GITHUB_CLIENT_ID=x-client-id",
                    ]
                ),
                mock.call(
                    ["heroku", "builds:create", "-a", "f", "--include-vcs-ignore"]
                ),
            ]
        )
