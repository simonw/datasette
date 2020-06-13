from .fixtures import (
    app_client,
    make_app_client,
    TestClient as _TestClient,
    EXPECTED_PLUGINS,
)
from datasette.plugins import DEFAULT_PLUGINS
from datasette.cli import cli, serve
from click.testing import CliRunner
import io
import json
import pathlib
import pytest
import textwrap


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
    with make_app_client(inspect_data=inspect_data, is_immutable=True) as client:
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


def test_plugins_cli(app_client):
    runner = CliRunner()
    result1 = runner.invoke(cli, ["plugins"])
    assert sorted(EXPECTED_PLUGINS, key=lambda p: p["name"]) == sorted(
        json.loads(result1.output), key=lambda p: p["name"]
    )
    # Try with --all
    result2 = runner.invoke(cli, ["plugins", "--all"])
    names = [p["name"] for p in json.loads(result2.output)]
    # Should have all the EXPECTED_PLUGINS
    assert set(names).issuperset(set(p["name"] for p in EXPECTED_PLUGINS))
    # And the following too:
    assert set(names).issuperset(DEFAULT_PLUGINS)


def test_metadata_yaml():
    yaml_file = io.StringIO(
        textwrap.dedent(
            """
    title: Hello from YAML
    """
        )
    )
    # Annoyingly we have to provide all default arguments here:
    ds = serve.callback(
        [],
        metadata=yaml_file,
        immutable=[],
        host="127.0.0.1",
        port=8001,
        debug=False,
        reload=False,
        cors=False,
        sqlite_extensions=[],
        inspect_file=None,
        template_dir=None,
        plugins_dir=None,
        static=[],
        memory=False,
        config=[],
        secret=None,
        root=False,
        version_note=None,
        help_config=False,
        return_instance=True,
    )
    client = _TestClient(ds.app())
    client.ds = ds
    response = client.get("/-/metadata.json")
    assert {"title": "Hello from YAML"} == response.json
