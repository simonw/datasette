import sqlite3
import urllib

import pytest
from click.testing import CliRunner

from datasette.app import Datasette
from datasette.cli import cli


def test_crossdb_join(app_client_two_attached_databases_crossdb_enabled):
    app_client = app_client_two_attached_databases_crossdb_enabled
    sql = """
    select
      'extra database' as db,
      pk,
      text1,
      text2
    from
      [extra database].searchable
    union all
    select
      'fixtures' as db,
      pk,
      text1,
      text2
    from
      fixtures.searchable
    """
    response = app_client.get(
        "/_memory/-/query.json?"
        + urllib.parse.urlencode({"sql": sql, "_shape": "array"})
    )
    assert response.status == 200
    assert response.json == [
        {"db": "extra database", "pk": 1, "text1": "barry cat", "text2": "terry dog"},
        {"db": "extra database", "pk": 2, "text1": "terry dog", "text2": "sara weasel"},
        {"db": "fixtures", "pk": 1, "text1": "barry cat", "text2": "terry dog"},
        {"db": "fixtures", "pk": 2, "text1": "terry dog", "text2": "sara weasel"},
    ]


def test_crossdb_warning_if_too_many_databases(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("dbs")
    dbs = []
    for i in range(11):
        path = str(db_dir / f"db_{i}.db")
        conn = sqlite3.connect(path)
        conn.execute("vacuum")
        conn.close()
        dbs.append(path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "serve",
            "--crossdb",
            "--get",
            "/",
        ]
        + dbs,
        catch_exceptions=False,
    )
    assert (
        "Warning: --crossdb only works with the first 10 attached databases"
        in result.stderr
    )


def test_crossdb_attached_database_list_display(
    app_client_two_attached_databases_crossdb_enabled,
):
    app_client = app_client_two_attached_databases_crossdb_enabled
    response = app_client.get("/_memory")
    app_client.get("/")
    for fragment in (
        "databases are attached to this connection",
        "<li><strong>fixtures</strong> - ",
        '<li><strong>extra database</strong> - <a href="/extra+database/-/query?sql=',
    ):
        assert fragment in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", (False, True))
async def test_crossdb_versions(tmp_path, monkeypatch, relative):
    # _versions() opened its connection without uri=True, so the crossdb
    # ATTACH of "file:...?mode=ro" was a plain file name: a 500 error for an
    # absolute path, a stray empty file in the current directory for a
    # relative one
    directory = tmp_path / "dbs"
    directory.mkdir()
    conn = sqlite3.connect(str(directory / "data.db"))
    conn.execute("create table t (id integer)")
    conn.close()
    if relative:
        monkeypatch.chdir(directory)
        path = "data.db"
    else:
        path = str(directory / "data.db")
    ds = Datasette([path], crossdb=True)
    try:
        await ds.invoke_startup()
        response = await ds.client.get("/-/versions.json")
        assert response.status_code == 200
        assert "sqlite" in response.json()
        # The crossdb connection itself still works
        response = await ds.client.get(
            "/_memory/-/query.json?sql=select+count(*)+as+n+from+data.t&_shape=array"
        )
        assert response.json() == [{"n": 0}]
    finally:
        ds.close()
    assert sorted(p.name for p in directory.iterdir()) == ["data.db"]
