import pytest

from datasette.app import Datasette


@pytest.mark.asyncio
async def test_no_warning_during_startup(tmp_path, capfd):
    """Catalog/metadata writes during _refresh_schemas must not warn."""
    data_db = tmp_path / "data.db"
    import sqlite3

    conn = sqlite3.connect(str(data_db))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.close()

    ds = Datasette(files=[str(data_db)])
    await ds.invoke_startup()
    # Trigger the schema refresh / index hit so all catalog writes happen.
    response = await ds.client.get("/")
    assert response.status_code == 200

    captured = capfd.readouterr()
    assert "internal database" not in captured.err
    assert ds._warned_internal_temp_write is False


@pytest.mark.asyncio
async def test_warning_fires_once_on_plugin_style_write(tmp_path, capfd):
    """A write to the temp internal DB outside the ephemeral context must
    print the warning to stderr exactly once."""
    data_db = tmp_path / "data.db"
    import sqlite3

    conn = sqlite3.connect(str(data_db))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.close()

    ds = Datasette(files=[str(data_db)])
    await ds.invoke_startup()
    await ds.client.get("/")
    capfd.readouterr()  # discard startup output

    internal_db = ds.get_internal_database()
    await internal_db.execute_write(
        "CREATE TABLE IF NOT EXISTS plugin_table (k TEXT)"
    )
    first = capfd.readouterr()
    assert "internal database" in first.err
    assert "--internal" in first.err
    assert ds._warned_internal_temp_write is True

    # A second plugin write must not warn again.
    await internal_db.execute_write(
        "INSERT INTO plugin_table (k) VALUES (?)", ["v"]
    )
    second = capfd.readouterr()
    assert second.err == ""


@pytest.mark.asyncio
async def test_no_warning_with_persistent_internal(tmp_path, capfd):
    """When --internal is given, is_temp_disk is False so no warning fires."""
    data_db = tmp_path / "data.db"
    import sqlite3

    conn = sqlite3.connect(str(data_db))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.close()

    ds = Datasette(
        files=[str(data_db)],
        internal=str(tmp_path / "internal.db"),
    )
    await ds.invoke_startup()
    await ds.client.get("/")

    internal_db = ds.get_internal_database()
    await internal_db.execute_write(
        "CREATE TABLE IF NOT EXISTS plugin_table (k TEXT)"
    )
    captured = capfd.readouterr()
    assert "internal database" not in captured.err
    assert ds._warned_internal_temp_write is False
