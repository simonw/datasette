from .fixtures import app_client
from datasette.cli import cli
from click.testing import CliRunner
import pathlib
import json


def test_inspect_cli(app_client):
    runner = CliRunner()
    result = runner.invoke(cli, ["inspect", "fixtures.db"])
    data = json.loads(result.output)
    assert ["fixtures"] == list(data.keys())
    database = data["fixtures"]
    assert "fixtures.db" == database["file"]
    assert isinstance(database["hash"], str)
    assert 64 == len(database["hash"])
    for table_name, expected_count in {
        "Table With Space In Name": 0,
        "facetable": 15,
    }.items():
        assert expected_count == database["tables"][table_name]["count"]


def test_inspect_cli_writes_to_file(app_client):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["inspect", "fixtures.db", "--inspect-file", "foo.json"]
    )
    assert 0 == result.exit_code, result.output
    data = json.load(open("foo.json"))
    assert ["fixtures"] == list(data.keys())


def test_spatialite_error_if_attempt_to_open_spatialite():
    runner = CliRunner()
    result = runner.invoke(
        cli, ["serve", str(pathlib.Path(__file__).parent / "spatialite.db")]
    )
    assert result.exit_code != 0
    assert "trying to load a SpatiaLite database" in result.output
