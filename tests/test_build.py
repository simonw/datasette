import base64
import hashlib
import importlib.metadata
import json
import sqlite3
import tarfile
import zipfile

import pytest
from click.testing import CliRunner

from datasette import build
from datasette.cli import cli
from datasette.config_dir import (
    REDIRECT_FILENAME,
    bundle_files,
    database_files,
    installed_config_dirs,
)
from datasette.plugins import pm

PYPROJECT = """
[project]
name = "My-Datasette.App"
version = "0.1"
description = "An example"
requires-python = ">=3.11"
dependencies = ["datasette", "datasette-cluster-map>=0.18"]

[build-system]
requires = ["datasette"]
build-backend = "datasette.build"
"""


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "pyproject.toml").write_text(PYPROJECT, "utf-8")
    for filename in ("zebra.db", "apple.sqlite"):
        conn = sqlite3.connect(app_dir / filename)
        conn.executescript(
            "create table t (id integer primary key); insert into t values (1), (2);"
        )
        conn.close()
    (app_dir / "datasette.yaml").write_text(
        "settings:\n  default_page_size: 7\n", "utf-8"
    )
    (app_dir / "metadata.json").write_text('{"title": "Packaged"}', "utf-8")
    (app_dir / "templates" / "pages").mkdir(parents=True)
    (app_dir / "templates" / "pages" / "about.html").write_text("About", "utf-8")
    (app_dir / "static").mkdir()
    (app_dir / "static" / "app.css").write_text("body {}", "utf-8")
    (app_dir / "plugins" / "__pycache__").mkdir(parents=True)
    (app_dir / "plugins" / "__pycache__" / "hello.cpython-313.pyc").write_bytes(b"")
    (app_dir / "plugins" / "hello.py").write_text("", "utf-8")
    # These are not part of a configuration directory
    (app_dir / "notes.txt").write_text("Not included", "utf-8")
    (app_dir / "other").mkdir()
    (app_dir / "other" / "nested.db").write_bytes(b"")
    monkeypatch.chdir(app_dir)
    yield app_dir
    # plugins_dir plugins are registered on the global plugin manager
    if pm.has_plugin("hello.py"):
        pm.unregister(name="hello.py")


def test_database_files_are_sorted(app_dir):
    assert [path.name for path in database_files(app_dir)] == [
        "apple.sqlite",
        "zebra.db",
    ]


def test_bundle_files(app_dir):
    assert [path.as_posix() for path in bundle_files(app_dir)] == [
        "apple.sqlite",
        "datasette.yaml",
        "metadata.json",
        "plugins/hello.py",
        "static/app.css",
        "templates/pages/about.html",
        "zebra.db",
    ]


def test_build_wheel(app_dir, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel_name = build.build_wheel(str(dist))
    assert wheel_name == "my_datasette_app-0.1-py3-none-any.whl"
    with zipfile.ZipFile(dist / wheel_name) as wheel:
        names = set(wheel.namelist())
        assert names == {
            "my_datasette_app/apple.sqlite",
            "my_datasette_app/datasette.yaml",
            "my_datasette_app/inspect-data.json",
            "my_datasette_app/metadata.json",
            "my_datasette_app/plugins/hello.py",
            "my_datasette_app/static/app.css",
            "my_datasette_app/templates/pages/about.html",
            "my_datasette_app/zebra.db",
            "my_datasette_app-0.1.dist-info/METADATA",
            "my_datasette_app-0.1.dist-info/WHEEL",
            "my_datasette_app-0.1.dist-info/entry_points.txt",
            "my_datasette_app-0.1.dist-info/RECORD",
        }
        metadata = wheel.read("my_datasette_app-0.1.dist-info/METADATA").decode()
        assert metadata.splitlines() == [
            "Metadata-Version: 2.1",
            "Name: My-Datasette.App",
            "Version: 0.1",
            "Summary: An example",
            "Requires-Python: >=3.11",
            "Requires-Dist: datasette",
            "Requires-Dist: datasette-cluster-map>=0.18",
        ]
        assert (
            wheel.read("my_datasette_app-0.1.dist-info/entry_points.txt").decode()
            == "[datasette.config_dir]\nmy_datasette_app = my_datasette_app\n"
        )
        # Inspect data uses bare filenames, so it works wherever it is installed
        inspect_data = json.loads(wheel.read("my_datasette_app/inspect-data.json"))
        assert {key: value["file"] for key, value in inspect_data.items()} == {
            "apple": "apple.sqlite",
            "zebra": "zebra.db",
        }
        assert inspect_data["zebra"]["tables"] == {"t": {"count": 2}}
        # Every file other than RECORD itself is listed with its hash and size
        record = wheel.read("my_datasette_app-0.1.dist-info/RECORD").decode()
        recorded = dict(line.split(",", 1) for line in record.splitlines())
        assert set(recorded) == names
        for name in names - {"my_datasette_app-0.1.dist-info/RECORD"}:
            data = wheel.read(name)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            assert recorded[name] == "sha256={},{}".format(
                digest.rstrip(b"=").decode(), len(data)
            )
    # The source directory is left alone
    assert not (app_dir / "inspect-data.json").exists()


def test_build_wheel_without_databases(app_dir, tmp_path):
    for path in database_files(app_dir):
        path.unlink()
    wheel_name = build.build_wheel(str(tmp_path))
    with zipfile.ZipFile(tmp_path / wheel_name) as wheel:
        assert "my_datasette_app/inspect-data.json" not in wheel.namelist()


def test_build_sdist_then_wheel(app_dir, tmp_path, monkeypatch):
    sdist_name = build.build_sdist(str(tmp_path))
    assert sdist_name == "my_datasette_app-0.1.tar.gz"
    with tarfile.open(tmp_path / sdist_name) as sdist:
        assert set(sdist.getnames()) == {
            "my_datasette_app-0.1/PKG-INFO",
            "my_datasette_app-0.1/pyproject.toml",
            "my_datasette_app-0.1/apple.sqlite",
            "my_datasette_app-0.1/datasette.yaml",
            "my_datasette_app-0.1/metadata.json",
            "my_datasette_app-0.1/plugins/hello.py",
            "my_datasette_app-0.1/static/app.css",
            "my_datasette_app-0.1/templates/pages/about.html",
            "my_datasette_app-0.1/zebra.db",
        }
        sdist.extractall(tmp_path / "unpacked", filter="data")
    # uv build and python -m build create the wheel from the unpacked sdist
    monkeypatch.chdir(tmp_path / "unpacked" / "my_datasette_app-0.1")
    wheel_name = build.build_wheel(str(tmp_path))
    with zipfile.ZipFile(tmp_path / wheel_name) as wheel:
        assert "my_datasette_app/zebra.db" in wheel.namelist()


@pytest.mark.parametrize(
    "pyproject,expected",
    (
        ('[project]\nversion = "1"', "pyproject.toml needs a [project] name"),
        ('[project]\nname = "x"', "pyproject.toml needs a [project] version"),
        (
            '[project]\nname = "x"\nversion = "1"\ndynamic = ["dependencies"]',
            "datasette.build does not support [project] dynamic",
        ),
    ),
)
def test_build_errors(app_dir, tmp_path, pyproject, expected):
    (app_dir / "pyproject.toml").write_text(pyproject, "utf-8")
    with pytest.raises(build.BuildError) as ex:
        build.build_wheel(str(tmp_path))
    assert ex.value.args[0] == expected


def _install(wheel_path, site_packages, monkeypatch):
    # Unpacking a wheel is enough for importlib.metadata to find it
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel.extractall(site_packages)
    real_entry_points = importlib.metadata.entry_points

    def entry_points(**params):
        if params == {"group": "datasette.config_dir"}:
            return [
                entry_point
                for dist in importlib.metadata.distributions(path=[str(site_packages)])
                for entry_point in dist.entry_points
                if entry_point.group == "datasette.config_dir"
            ]
        return real_entry_points(**params)

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)


def test_serve_installed_wheel(app_dir, tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    _install(tmp_path / build.build_wheel(str(tmp_path)), site_packages, monkeypatch)
    assert installed_config_dirs() == {
        "my_datasette_app": site_packages / "my_datasette_app"
    }
    # Running from a different directory, with no arguments
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--get", "/-/databases.json"])
    assert result.exit_code == 0, result.output
    assert [
        (db["name"], db["is_mutable"]) for db in json.loads(result.output)["databases"]
    ] == [("apple", False), ("zebra", False)]
    result = runner.invoke(cli, ["--get", "/-/settings.json"])
    assert json.loads(result.output)["default_page_size"] == 7
    result = runner.invoke(cli, ["--get", "/static/app.css"])
    assert result.output.strip() == "body {}"
    result = runner.invoke(cli, ["--get", "/about"])
    assert result.output.strip() == "About"
    # Naming something to serve takes over from the installed directory
    conn = sqlite3.connect(tmp_path / "explicit.db")
    conn.execute("vacuum")
    conn.close()
    result = runner.invoke(
        cli, [str(tmp_path / "explicit.db"), "--get", "/-/databases.json"]
    )
    assert [db["name"] for db in json.loads(result.output)["databases"]] == ["explicit"]


def test_serve_editable_install(app_dir, tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    wheel_name = build.build_editable(str(tmp_path))
    with zipfile.ZipFile(tmp_path / wheel_name) as wheel:
        assert f"my_datasette_app/{REDIRECT_FILENAME}" in wheel.namelist()
        assert "my_datasette_app/zebra.db" not in wheel.namelist()
    _install(tmp_path / wheel_name, site_packages, monkeypatch)
    assert installed_config_dirs() == {"my_datasette_app": app_dir.resolve()}
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["--get", "/-/databases.json"])
    assert result.exit_code == 0, result.output
    # An editable install serves the live files, so databases stay mutable
    assert [
        (db["name"], db["is_mutable"]) for db in json.loads(result.output)["databases"]
    ] == [("apple", True), ("zebra", True)]


def test_serve_error_if_two_installed(app_dir, tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    _install(tmp_path / build.build_wheel(str(tmp_path)), site_packages, monkeypatch)
    (app_dir / "pyproject.toml").write_text(
        PYPROJECT.replace("My-Datasette.App", "another"), "utf-8"
    )
    _install(tmp_path / build.build_wheel(str(tmp_path)), site_packages, monkeypatch)
    result = CliRunner().invoke(cli, ["--get", "/"])
    assert result.exit_code == 1
    assert result.output.splitlines() == [
        "Error: More than one configuration directory is installed, pass the path to one of them:",
        f"  another: {site_packages / 'another'}",
        f"  my_datasette_app: {site_packages / 'my_datasette_app'}",
    ]
