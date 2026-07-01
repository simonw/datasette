import time

import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette
from datasette.utils import sqlite3


def write_token(ds, actor_id="root", permissions=None):
    to_sign = {"a": actor_id, "token": "dstok", "t": int(time.time())}
    if permissions:
        to_sign["_r"] = {"a": permissions}
    return "dstok_{}".format(ds.sign(to_sign, namespace="token"))


def _headers(token):
    return {
        "Authorization": "Bearer {}".format(token),
        "Content-Type": "application/json",
    }


def _window_data_from_html(html, variable_name):
    soup = Soup(html, "html.parser")
    scripts = soup.find_all("script")
    matching_scripts = [
        script for script in scripts if variable_name in (script.string or "")
    ]
    assert len(matching_scripts) == 1
    script_text = matching_scripts[0].string.strip()
    prefix = f"window.{variable_name} = "
    assert script_text.startswith(prefix)
    import json

    return json.loads(script_text[len(prefix) :].rstrip(";"))


@pytest.fixture
def ds_lc(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute(
        "create table people (id integer primary key, first_name text, last_name text)"
    )
    db.execute("insert into people values (1, 'Alice', 'Smith')")
    db.commit()
    ds = Datasette([db_path])
    ds.root_enabled = True
    yield ds
    ds.close()


@pytest.fixture
def ds_lc_editor_permission(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute(
        "create table people (id integer primary key, first_name text, last_name text)"
    )
    db.execute("insert into people values (1, 'Alice', 'Smith')")
    db.commit()
    ds = Datasette(
        [db_path],
        config={
            "databases": {
                "data": {
                    "tables": {
                        "people": {
                            "permissions": {"set-label-columns": {"id": "editor"}},
                        }
                    }
                }
            }
        },
    )
    ds.root_enabled = True
    yield ds
    ds.close()


@pytest.mark.asyncio
async def test_set_label_columns_api_single_column(ds_lc):
    await ds_lc.invoke_startup()
    token = write_token(ds_lc, permissions=["slc"])
    response = await ds_lc.client.post(
        "/data/people/-/set-label-columns",
        json={"columns": ["first_name"]},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "database": "data",
        "table": "people",
        "columns": ["first_name"],
    }
    assert await ds_lc.get_label_columns("data", "people") == ["first_name"]


@pytest.mark.asyncio
async def test_set_label_columns_api_multiple_columns(ds_lc):
    await ds_lc.invoke_startup()
    token = write_token(ds_lc, permissions=["slc"])
    response = await ds_lc.client.post(
        "/data/people/-/set-label-columns",
        json={"columns": ["first_name", "last_name"]},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["columns"] == ["first_name", "last_name"]
    db = ds_lc.databases["data"]
    assert await db.label_columns_for_table("people") == ["first_name", "last_name"]


@pytest.mark.asyncio
async def test_clear_label_columns_api(ds_lc):
    await ds_lc.invoke_startup()
    await ds_lc.set_label_columns("data", "people", ["first_name"])
    token = write_token(ds_lc, permissions=["slc"])
    response = await ds_lc.client.post(
        "/data/people/-/set-label-columns",
        json={"columns": None},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "database": "data",
        "table": "people",
        "columns": None,
    }
    assert await ds_lc.get_label_columns("data", "people") is None


@pytest.mark.asyncio
async def test_set_label_columns_reflected_in_foreign_key_labels(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute(
        "create table people (id integer primary key, first_name text, last_name text)"
    )
    db.execute("insert into people values (1, 'Alice', 'Smith')")
    db.execute(
        "create table orders (id integer primary key, person_id integer, "
        "foreign key (person_id) references people(id))"
    )
    db.execute("insert into orders values (1, 1)")
    db.commit()
    ds = Datasette([db_path])
    ds.root_enabled = True
    try:
        await ds.invoke_startup()
        await ds.set_label_columns("data", "people", ["first_name", "last_name"])
        response = await ds.client.get(
            "/data/orders.json?_labels=on", actor={"id": "root"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rows"][0]["person_id"] == {"value": 1, "label": "Alice Smith"}
    finally:
        db.close()
        for database in ds.databases.values():
            if not database.is_memory:
                database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,special_case,expected_status,expected_errors",
    (
        (
            {"columns": ["first_name"]},
            "no_permission",
            403,
            ["Permission denied"],
        ),
        (
            None,
            "invalid_json",
            400,
            [
                "Invalid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
            ],
        ),
        (
            {"columns": ["first_name"]},
            "invalid_content_type",
            400,
            ["Invalid content-type, must be application/json"],
        ),
        (
            [],
            None,
            400,
            ["JSON must be a dictionary"],
        ),
        (
            {},
            None,
            400,
            ['"columns" is required'],
        ),
        (
            {"columns": "first_name"},
            None,
            400,
            ['"columns" must be a non-empty list of strings, or null'],
        ),
        (
            {"columns": []},
            None,
            400,
            ['"columns" must be a non-empty list of strings, or null'],
        ),
        (
            {"columns": [1]},
            None,
            400,
            ['"columns" must be a non-empty list of strings, or null'],
        ),
        (
            {"columns": ["first_name", "first_name"]},
            None,
            400,
            ['"columns" must not contain duplicates'],
        ),
        (
            {"columns": ["not_a_column"]},
            None,
            400,
            ["Column not found: not_a_column"],
        ),
        (
            {"columns": ["first_name"], "extra": True},
            None,
            400,
            ['Invalid parameter: "extra"'],
        ),
    ),
)
async def test_set_label_columns_api_errors(
    ds_lc, body, special_case, expected_status, expected_errors
):
    await ds_lc.invoke_startup()
    token = write_token(
        ds_lc,
        permissions=(["slc"] if special_case != "no_permission" else ["vi"]),
    )
    kwargs = {
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": (
                "text/plain"
                if special_case == "invalid_content_type"
                else "application/json"
            ),
        }
    }
    if special_case == "invalid_json":
        kwargs["content"] = "{bad json"
    else:
        kwargs["json"] = body
    response = await ds_lc.client.post("/data/people/-/set-label-columns", **kwargs)
    assert response.status_code == expected_status
    assert response.json() == {"ok": False, "errors": expected_errors}


@pytest.mark.asyncio
async def test_set_label_columns_api_works_for_immutable_database(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "immutable.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute(
        "create table people (id integer primary key, first_name text, last_name text)"
    )
    db.commit()
    ds = Datasette([], immutables=[db_path])
    ds.root_enabled = True
    try:
        await ds.invoke_startup()
        token = write_token(ds, permissions=["slc"])
        response = await ds.client.post(
            "/immutable/people/-/set-label-columns",
            json={"columns": ["first_name"]},
            headers=_headers(token),
        )
        assert response.status_code == 200
        assert await ds.get_label_columns("immutable", "people") == ["first_name"]
    finally:
        db.close()
        for database in ds.databases.values():
            if not database.is_memory:
                database.close()


def table_data_from_soup(soup):
    import json
    import re

    table_script = [
        s for s in soup.find_all("script") if "_datasetteTableData" in (s.string or "")
    ][0]
    match = re.search(
        r"window\._datasetteTableData\s*=\s*({.*?});",
        table_script.string,
        re.DOTALL,
    )
    return json.loads(match.group(1))


@pytest.mark.asyncio
async def test_set_label_columns_action_button_hidden_without_permission(ds_lc):
    await ds_lc.invoke_startup()
    response = await ds_lc.client.get("/data/people")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert soup.select_one('button[data-table-action="set-label-columns"]') is None
    assert "labelColumns" not in table_data_from_soup(soup)


@pytest.mark.asyncio
async def test_set_label_columns_action_button_and_data(ds_lc):
    await ds_lc.invoke_startup()
    response = await ds_lc.client.get("/data/people", actor={"id": "root"})
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")

    button = soup.select_one(
        'button.action-menu-button[data-table-action="set-label-columns"]'
    )
    assert button is not None
    assert button["aria-label"] == "Set label columns for people"
    description = button.find("span", class_="dropdown-description")
    assert description.text.strip() == (
        "Choose which column(s) are used to label this table's rows."
    )

    data = table_data_from_soup(soup)["labelColumns"]
    assert data["path"] == "/data/people/-/set-label-columns"
    assert data["tableName"] == "people"
    assert data["columns"] == ["id", "first_name", "last_name"]
    assert data["current"] == []
    assert data["isOverridden"] is False


@pytest.mark.asyncio
async def test_set_label_columns_ui_data_reflects_override(ds_lc):
    await ds_lc.invoke_startup()
    await ds_lc.set_label_columns("data", "people", ["first_name", "last_name"])
    response = await ds_lc.client.get("/data/people", actor={"id": "root"})
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    data = table_data_from_soup(soup)["labelColumns"]
    assert data["current"] == ["first_name", "last_name"]
    assert data["isOverridden"] is True
