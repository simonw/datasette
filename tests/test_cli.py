from .fixtures import app_client, make_app_client
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


def test_serve_with_inspect_file_prepopulates_table_counts_cache():
    inspect_data = {"fixtures": {"tables": {"hithere": {"count": 44}}}}
    for client in make_app_client(inspect_data=inspect_data, is_immutable=True):
        assert inspect_data == client.ds.inspect_data
        db = client.ds.databases["fixtures"]
        assert {"hithere": 44} == db.cached_table_counts


def test_spatialite_error_if_attempt_to_open_spatialite():
    runner = CliRunner()
    result = runner.invoke(
        cli, ["serve", str(pathlib.Path(__file__).parent / "spatialite.db")]
    )
    assert result.exit_code != 0
    assert "trying to load a SpatiaLite database" in result.output
