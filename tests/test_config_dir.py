import json
import pathlib

import pytest

from datasette.app import Datasette
from datasette.plugins import pm
from datasette.utils import StartupError
from datasette.utils.sqlite import sqlite3

PLUGIN = """
from datasette import hookimpl

@hookimpl
def extra_template_vars():
    return {
        "from_plugin": "hooray"
    }
"""
METADATA = {"title": "This is from metadata"}
CONFIG = {
    "settings": {
        "default_cache_ttl": 60,
    }
}
CSS = """
body { margin-top: 3em}
"""


@pytest.fixture(scope="session")
def config_dir(tmp_path_factory):
    config_dir = tmp_path_factory.mktemp("config-dir")
    plugins_dir = config_dir / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "hooray.py").write_text(PLUGIN, "utf-8")
    (plugins_dir / "non_py_file.txt").write_text(PLUGIN, "utf-8")
    (plugins_dir / ".mypy_cache").mkdir()

    templates_dir = config_dir / "templates"
    templates_dir.mkdir()
    (templates_dir / "row.html").write_text(
        "Show row here. Plugin says {{ from_plugin }}", "utf-8"
    )

    static_dir = config_dir / "static"
    static_dir.mkdir()
    (static_dir / "hello.css").write_text(CSS, "utf-8")

    (config_dir / "metadata.json").write_text(json.dumps(METADATA), "utf-8")
    (config_dir / "datasette.json").write_text(json.dumps(CONFIG), "utf-8")

    for dbname in ("demo.db", "immutable.db", "j.sqlite3", "k.sqlite"):
        db = sqlite3.connect(str(config_dir / dbname))
        db.executescript("""
        CREATE TABLE cities (
            id integer primary key,
            name text
        );
        INSERT INTO cities (id, name) VALUES
            (1, 'San Francisco')
        ;
        """)
        db.close()

    # Mark "immutable.db" as immutable
    (config_dir / "inspect-data.json").write_text(
        json.dumps(
            {
                "immutable": {
                    "hash": "hash",
                    "size": 8192,
                    "file": "immutable.db",
                    "tables": {"cities": {"count": 1}},
                }
            }
        ),
        "utf-8",
    )
    return config_dir


def test_invalid_settings(config_dir):
    previous = (config_dir / "datasette.json").read_text("utf-8")
    (config_dir / "datasette.json").write_text(
        json.dumps({"settings": {"invalid": "invalid-setting"}}), "utf-8"
    )
    try:
        with pytest.raises(StartupError) as ex:
            Datasette([], config_dir=config_dir)
        assert ex.value.args[0] == "Invalid setting 'invalid' in config file"
    finally:
        (config_dir / "datasette.json").write_text(previous, "utf-8")


@pytest.fixture(scope="module")
def config_dir_client(config_dir):
    ds = Datasette([], config_dir=config_dir)
    try:
        yield ds.client
    finally:
        # plugins_dir plugins are registered on the global plugin manager
        pm.unregister(name="hooray.py")
        ds.close()


async def test_settings(config_dir_client):
    response = await config_dir_client.get("/-/settings.json")
    assert response.status_code == 200
    assert response.json()["default_cache_ttl"] == 60


async def test_plugins(config_dir_client):
    response = await config_dir_client.get("/-/plugins.json")
    assert response.status_code == 200
    plugins = response.json()
    assert "hooray.py" in {p["name"] for p in plugins}
    assert "non_py_file.txt" not in {p["name"] for p in plugins}
    assert "mypy_cache" not in {p["name"] for p in plugins}


async def test_templates_and_plugin(config_dir_client):
    response = await config_dir_client.get("/demo/cities/1")
    assert response.status_code == 200
    assert response.text == "Show row here. Plugin says hooray"


async def test_static(config_dir_client):
    response = await config_dir_client.get("/static/hello.css")
    assert response.status_code == 200
    assert CSS == response.text
    assert response.headers["content-type"] == "text/css"


async def test_static_directory_browsing_not_allowed(config_dir_client):
    response = await config_dir_client.get("/static/")
    assert response.status_code == 403
    assert response.text == "403: Directory listing is not allowed"


async def test_databases(config_dir_client):
    response = await config_dir_client.get("/-/databases.json")
    assert response.status_code == 200
    databases = response.json()["databases"]
    assert len(databases) == 4
    databases.sort(key=lambda d: d["name"])
    for db, expected_name in zip(databases, ("demo", "immutable", "j", "k")):
        assert db["name"] == expected_name
        assert db["is_mutable"] == (expected_name != "immutable")


def test_store_config_dir(config_dir_client):
    ds = config_dir_client.ds

    assert hasattr(ds, "config_dir")
    assert ds.config_dir is not None
    assert isinstance(ds.config_dir, pathlib.Path)
