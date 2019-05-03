from click.testing import CliRunner
from datasette import cli
from unittest import mock


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
