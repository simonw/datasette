from click.testing import CliRunner
from datasette import cli
from unittest import mock
import subprocess


@mock.patch("shutil.which")
def test_publish_now_requires_now(mock_which):
    mock_which.return_value = False
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "nowv1", "test.db"])
        assert result.exit_code == 1
        assert "Publishing to Zeit Now requires now" in result.output


@mock.patch("shutil.which")
def test_publish_now_invalid_database(mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    result = runner.invoke(cli.cli, ["publish", "nowv1", "woop.db"])
    assert result.exit_code == 2
    assert 'Path "woop.db" does not exist' in result.output


@mock.patch("shutil.which")
def test_publish_now_using_now_alias(mock_which):
    mock_which.return_value = True
    result = CliRunner().invoke(cli.cli, ["publish", "now", "woop.db"])
    assert result.exit_code == 2


@mock.patch("shutil.which")
@mock.patch("datasette.publish.now.run")
def test_publish_now(mock_run, mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(cli.cli, ["publish", "nowv1", "test.db"])
        assert 0 == result.exit_code
        mock_run.assert_called_once_with("now", stdout=subprocess.PIPE)


@mock.patch("shutil.which")
@mock.patch("datasette.publish.now.run")
def test_publish_now_force_token(mock_run, mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli, ["publish", "nowv1", "test.db", "--force", "--token=X"]
        )
        assert 0 == result.exit_code
        mock_run.assert_called_once_with(
            ["now", "--force", "--token=X"], stdout=subprocess.PIPE
        )


@mock.patch("shutil.which")
@mock.patch("datasette.publish.now.run")
def test_publish_now_multiple_aliases(mock_run, mock_which):
    mock_which.return_value = True
    mock_run.return_value = mock.Mock(0)
    mock_run.return_value.stdout = b"https://demo.example.com/"
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        runner.invoke(
            cli.cli,
            [
                "publish",
                "now",
                "test.db",
                "--token",
                "XXX",
                "--alias",
                "alias1",
                "--alias",
                "alias2",
            ],
        )
        mock_run.assert_has_calls(
            [
                mock.call(["now", "--token=XXX"], stdout=subprocess.PIPE),
                mock.call(
                    [
                        "now",
                        "alias",
                        b"https://demo.example.com/",
                        "alias1",
                        "--token=XXX",
                    ]
                ),
                mock.call(
                    [
                        "now",
                        "alias",
                        b"https://demo.example.com/",
                        "alias2",
                        "--token=XXX",
                    ]
                ),
            ]
        )
