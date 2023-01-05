from .fixtures import (
    app_client,
    make_app_client,
    TestClient as _TestClient,
    EXPECTED_PLUGINS,
)
import asyncio
from datasette.app import SETTINGS
from datasette.plugins import DEFAULT_PLUGINS
from datasette.cli import cli, serve
from datasette.version import __version__
from datasette.utils import tilde_encode
from datasette.utils.sqlite import sqlite3
from click.testing import CliRunner
import io
import json
import pathlib
import pytest
import sys
import textwrap
from unittest import mock
import urllib


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
    with open("foo.json") as fp:
        data = json.load(fp)
    assert ["fixtures"] == list(data.keys())


def test_serve_with_inspect_file_prepopulates_table_counts_cache():
    inspect_data = {"fixtures": {"tables": {"hithere": {"count": 44}}}}
    with make_app_client(inspect_data=inspect_data, is_immutable=True) as client:
        assert inspect_data == client.ds.inspect_data
        db = client.ds.databases["fixtures"]
        assert {"hithere": 44} == db.cached_table_counts


@pytest.mark.parametrize(
    "spatialite_paths,should_suggest_load_extension",
    (
        ([], False),
        (["/tmp"], True),
    ),
)
def test_spatialite_error_if_attempt_to_open_spatialite(
    spatialite_paths, should_suggest_load_extension
):
    with mock.patch("datasette.utils.SPATIALITE_PATHS", spatialite_paths):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["serve", str(pathlib.Path(__file__).parent / "spatialite.db")]
        )
        assert result.exit_code != 0
        assert "It looks like you're trying to load a SpatiaLite" in result.output
        suggestion = "--load-extension=spatialite"
        if should_suggest_load_extension:
            assert suggestion in result.output
        else:
            assert suggestion not in result.output


@mock.patch("datasette.utils.SPATIALITE_PATHS", ["/does/not/exist"])
def test_spatialite_error_if_cannot_find_load_extension_spatialite():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "serve",
            str(pathlib.Path(__file__).parent / "spatialite.db"),
            "--load-extension",
            "spatialite",
        ],
    )
    assert result.exit_code != 0
    assert "Could not find SpatiaLite extension" in result.output


def test_plugins_cli(app_client):
    runner = CliRunner()
    result1 = runner.invoke(cli, ["plugins"])
    assert json.loads(result1.output) == EXPECTED_PLUGINS
    # Try with --all
    result2 = runner.invoke(cli, ["plugins", "--all"])
    names = [p["name"] for p in json.loads(result2.output)]
    # Should have all the EXPECTED_PLUGINS
    assert set(names).issuperset({p["name"] for p in EXPECTED_PLUGINS})
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
        uds=None,
        reload=False,
        cors=False,
        sqlite_extensions=[],
        inspect_file=None,
        template_dir=None,
        plugins_dir=None,
        static=[],
        memory=False,
        config=[],
        settings=[],
        secret=None,
        root=False,
        version_note=None,
        get=None,
        help_settings=False,
        pdb=False,
        crossdb=False,
        nolock=False,
        open_browser=False,
        create=False,
        ssl_keyfile=None,
        ssl_certfile=None,
        return_instance=True,
    )
    client = _TestClient(ds)
    response = client.get("/-/metadata.json")
    assert {"title": "Hello from YAML"} == response.json


@mock.patch("datasette.cli.run_module")
def test_install(run_module):
    runner = CliRunner()
    runner.invoke(cli, ["install", "datasette-mock-plugin", "datasette-mock-plugin2"])
    run_module.assert_called_once_with("pip", run_name="__main__")
    assert sys.argv == [
        "pip",
        "install",
        "datasette-mock-plugin",
        "datasette-mock-plugin2",
    ]


@pytest.mark.parametrize("flag", ["-U", "--upgrade"])
@mock.patch("datasette.cli.run_module")
def test_install_upgrade(run_module, flag):
    runner = CliRunner()
    runner.invoke(cli, ["install", flag, "datasette"])
    run_module.assert_called_once_with("pip", run_name="__main__")
    assert sys.argv == ["pip", "install", "--upgrade", "datasette"]


@mock.patch("datasette.cli.run_module")
def test_uninstall(run_module):
    runner = CliRunner()
    runner.invoke(cli, ["uninstall", "datasette-mock-plugin", "-y"])
    run_module.assert_called_once_with("pip", run_name="__main__")
    assert sys.argv == ["pip", "uninstall", "datasette-mock-plugin", "-y"]


def test_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.output == f"cli, version {__version__}\n"


@pytest.mark.parametrize("invalid_port", ["-1", "0.5", "dog", "65536"])
def test_serve_invalid_ports(invalid_port):
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, ["--port", invalid_port])
    assert result.exit_code == 2
    assert "Invalid value for '-p'" in result.stderr


def test_setting():
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--setting", "default_page_size", "5", "--get", "/-/settings.json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["default_page_size"] == 5


def test_setting_type_validation():
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, ["--setting", "default_page_size", "dog"])
    assert result.exit_code == 2
    assert '"default_page_size" should be an integer' in result.stderr


@pytest.mark.parametrize("default_allow_sql", (True, False))
def test_setting_default_allow_sql(default_allow_sql):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--setting",
            "default_allow_sql",
            "on" if default_allow_sql else "off",
            "--get",
            "/_memory.json?sql=select+21&_shape=objects",
        ],
    )
    if default_allow_sql:
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["rows"][0] == {"21": 21}
    else:
        assert result.exit_code == 1, result.output
        # This isn't JSON at the moment, maybe it should be though
        assert "Forbidden" in result.output


def test_config_deprecated():
    # The --config option should show a deprecation message
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        cli, ["--config", "allow_download:off", "--get", "/-/settings.json"]
    )
    assert result.exit_code == 0
    assert not json.loads(result.output)["allow_download"]
    assert "will be deprecated in" in result.stderr


def test_sql_errors_logged_to_stderr():
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, ["--get", "/_memory.json?sql=select+blah"])
    assert result.exit_code == 1
    assert "sql = 'select blah', params = {}: no such column: blah\n" in result.stderr


def test_serve_create(tmpdir):
    runner = CliRunner()
    db_path = tmpdir / "does_not_exist_yet.db"
    assert not db_path.exists()
    result = runner.invoke(
        cli, [str(db_path), "--create", "--get", "/-/databases.json"]
    )
    assert result.exit_code == 0, result.output
    databases = json.loads(result.output)
    assert {
        "name": "does_not_exist_yet",
        "is_mutable": True,
        "is_memory": False,
        "hash": None,
    }.items() <= databases[0].items()
    assert db_path.exists()


def test_serve_duplicate_database_names(tmpdir):
    "'datasette db.db nested/db.db' should attach two databases, /db and /db_2"
    runner = CliRunner()
    db_1_path = str(tmpdir / "db.db")
    nested = tmpdir / "nested"
    nested.mkdir()
    db_2_path = str(tmpdir / "nested" / "db.db")
    for path in (db_1_path, db_2_path):
        sqlite3.connect(path).execute("vacuum")
    result = runner.invoke(cli, [db_1_path, db_2_path, "--get", "/-/databases.json"])
    assert result.exit_code == 0, result.output
    databases = json.loads(result.output)
    assert {db["name"] for db in databases} == {"db", "db_2"}


def test_serve_deduplicate_same_database_path(tmpdir):
    "'datasette db.db db.db' should only attach one database, /db"
    runner = CliRunner()
    db_path = str(tmpdir / "db.db")
    sqlite3.connect(db_path).execute("vacuum")
    result = runner.invoke(cli, [db_path, db_path, "--get", "/-/databases.json"])
    assert result.exit_code == 0, result.output
    databases = json.loads(result.output)
    assert {db["name"] for db in databases} == {"db"}


@pytest.mark.parametrize(
    "filename", ["test-database (1).sqlite", "database (1).sqlite"]
)
def test_weird_database_names(tmpdir, filename):
    # https://github.com/simonw/datasette/issues/1181
    runner = CliRunner()
    db_path = str(tmpdir / filename)
    sqlite3.connect(db_path).execute("vacuum")
    result1 = runner.invoke(cli, [db_path, "--get", "/"])
    assert result1.exit_code == 0, result1.output
    filename_no_stem = filename.rsplit(".", 1)[0]
    expected_link = '<a href="/{}">{}</a>'.format(
        tilde_encode(filename_no_stem), filename_no_stem
    )
    assert expected_link in result1.output
    # Now try hitting that database page
    result2 = runner.invoke(
        cli, [db_path, "--get", "/{}".format(tilde_encode(filename_no_stem))]
    )
    assert result2.exit_code == 0, result2.output


def test_help_settings():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help-settings"])
    for setting in SETTINGS:
        assert setting.name in result.output


@pytest.mark.parametrize("setting", ("hash_urls", "default_cache_ttl_hashed"))
def test_help_error_on_hash_urls_setting(setting):
    runner = CliRunner()
    result = runner.invoke(cli, ["--setting", setting, 1])
    assert result.exit_code == 2
    assert "The hash_urls setting has been removed" in result.output
