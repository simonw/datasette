from datasette.app import Datasette
from datasette.cli import cli
from click.testing import CliRunner
import pytest
import urllib
import sqlite3


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
        path = str(db_dir / "db_{}.db".format(i))
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
async def test_crossdb_attaches_database_with_closing_bracket_in_name(tmp_path):
    main_db = tmp_path / "fixtures.db"
    extra_db = tmp_path / "extra]database.db"
    for path in (main_db, extra_db):
        conn = sqlite3.connect(path)
        conn.execute("create table searchable (pk integer primary key, text1 text)")
        conn.execute("insert into searchable (text1) values ('ok')")
        conn.commit()
        conn.close()

    ds = Datasette([str(main_db), str(extra_db)], crossdb=True)
    response = await ds.client.get("/_memory")
    assert response.status_code == 200
    assert "extra]database" in response.text
    for db in ds.databases.values():
        db.close()
