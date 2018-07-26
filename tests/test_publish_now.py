from click.testing import CliRunner
from datasette import cli
from unittest import mock


@mock.patch("shutil.which")
def test_publish_now_requires_now(mock_which):
    mock_which.return_value = False
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "now", "test.db"])
        assert result.exit_code == 1
        assert "Publishing to Zeit Now requires now" in result.output


@mock.patch("shutil.which")
def test_publish_now_invalid_database(mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    result = runner.invoke(cli.cli, ["publish", "now", "woop.db"])
    assert result.exit_code == 2
    assert 'Path "woop.db" does not exist' in result.output


@mock.patch("shutil.which")
@mock.patch("datasette.publish.now.call")
def test_publish_now(mock_call, mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "now", "test.db"])
        assert 0 == result.exit_code
        mock_call.assert_called_once_with("now")


@mock.patch("shutil.which")
@mock.patch("datasette.publish.now.call")
def test_publish_now_force_token(mock_call, mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli, ["publish", "now", "test.db", "--force", "--token=X"]
        )
        assert 0 == result.exit_code
        mock_call.assert_called_once_with(["now", "--force", "--token=X"])
