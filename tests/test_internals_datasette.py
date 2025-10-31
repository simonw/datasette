"""
Tests for the datasette.app.Datasette class
"""

import dataclasses
from datasette import Context
from datasette.app import Datasette, Database, ResourcesSQL
from datasette.resources import DatabaseResource
from itsdangerous import BadSignature
import pytest


@pytest.fixture
def datasette(ds_client):
    return ds_client.ds


def test_get_database(datasette):
    db = datasette.get_database("fixtures")
    assert "fixtures" == db.name
    with pytest.raises(KeyError):
        datasette.get_database("missing")


def test_get_database_no_argument(datasette):
    # Returns the first available database:
    db = datasette.get_database()
    assert "fixtures" == db.name


@pytest.mark.parametrize("value", ["hello", 123, {"key": "value"}])
@pytest.mark.parametrize("namespace", [None, "two"])
def test_sign_unsign(datasette, value, namespace):
    extra_args = [namespace] if namespace else []
    signed = datasette.sign(value, *extra_args)
    assert value != signed
    assert value == datasette.unsign(signed, *extra_args)
    with pytest.raises(BadSignature):
        datasette.unsign(signed[:-1] + ("!" if signed[-1] != "!" else ":"))


@pytest.mark.parametrize(
    "setting,expected",
    (
        ("base_url", "/"),
        ("max_csv_mb", 100),
        ("allow_csv_stream", True),
    ),
)
def test_datasette_setting(datasette, setting, expected):
    assert datasette.setting(setting) == expected


@pytest.mark.asyncio
async def test_datasette_constructor():
    ds = Datasette()
    databases = (await ds.client.get("/-/databases.json")).json()
    assert databases == [
        {
            "name": "_memory",
            "route": "_memory",
            "path": None,
            "size": 0,
            "is_mutable": False,
            "is_memory": True,
            "hash": None,
        }
    ]


@pytest.mark.asyncio
async def test_num_sql_threads_zero():
    ds = Datasette([], memory=True, settings={"num_sql_threads": 0})
    db = ds.add_database(Database(ds, memory_name="test_num_sql_threads_zero"))
    await db.execute_write("create table t(id integer primary key)")
    await db.execute_write("insert into t (id) values (1)")
    response = await ds.client.get("/-/threads.json")
    assert response.json() == {"num_threads": 0, "threads": []}
    response2 = await ds.client.get("/test_num_sql_threads_zero/t.json?_shape=array")
    assert response2.json() == [{"id": 1}]


ROOT = {"id": "root"}
ALLOW_ROOT = {"allow": {"id": "root"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,config,action,resource,should_allow,expected_private",
    (
        (None, ALLOW_ROOT, "view-instance", None, False, False),
        (ROOT, ALLOW_ROOT, "view-instance", None, True, True),
        (
            None,
            {"databases": {"_memory": ALLOW_ROOT}},
            "view-database",
            DatabaseResource(database="_memory"),
            False,
            False,
        ),
        (
            ROOT,
            {"databases": {"_memory": ALLOW_ROOT}},
            "view-database",
            DatabaseResource(database="_memory"),
            True,
            True,
        ),
        # Check private is false for non-protected instance check
        (
            ROOT,
            {"allow": True},
            "view-instance",
            None,
            True,
            False,
        ),
    ),
)
async def test_datasette_check_visibility(
    actor, config, action, resource, should_allow, expected_private
):
    ds = Datasette([], memory=True, config=config)
    await ds.invoke_startup()
    visible, private = await ds.check_visibility(
        actor, action=action, resource=resource
    )
    assert visible == should_allow
    assert private == expected_private


@pytest.mark.asyncio
async def test_datasette_render_template_no_request():
    # https://github.com/simonw/datasette/issues/1849
    ds = Datasette(memory=True)
    await ds.invoke_startup()
    rendered = await ds.render_template("error.html")
    assert "Error " in rendered


@pytest.mark.asyncio
async def test_datasette_render_template_with_dataclass():
    @dataclasses.dataclass
    class ExampleContext(Context):
        title: str
        status: int
        error: str

    context = ExampleContext(title="Hello", status=200, error="Error message")
    ds = Datasette(memory=True)
    await ds.invoke_startup()
    rendered = await ds.render_template("error.html", context)
    assert "<h1>Hello</h1>" in rendered
    assert "Error message" in rendered


def test_datasette_error_if_string_not_list(tmpdir):
    # https://github.com/simonw/datasette/issues/1985
    db_path = str(tmpdir / "data.db")
    with pytest.raises(ValueError):
        ds = Datasette(db_path)


@pytest.mark.asyncio
async def test_get_action(ds_client):
    ds = ds_client.ds
    for name_or_abbr in ("vi", "view-instance", "vt", "view-table"):
        action = ds.get_action(name_or_abbr)
        if "-" in name_or_abbr:
            assert action.name == name_or_abbr
        else:
            assert action.abbr == name_or_abbr
    # And test None return for missing action
    assert ds.get_action("missing-permission") is None


@pytest.mark.asyncio
async def test_apply_metadata_json():
    ds = Datasette(
        metadata={
            "databases": {
                "legislators": {
                    "tables": {"offices": {"summary": "office address or sumtin"}},
                    "queries": {
                        "millennial_representatives": {
                            "summary": "Social media accounts for current legislators"
                        }
                    },
                }
            },
            "weird_instance_value": {"nested": [1, 2, 3]},
        },
    )
    await ds.invoke_startup()
    assert (await ds.client.get("/")).status_code == 200
    value = (await ds.get_instance_metadata()).get("weird_instance_value")
    assert value == '{"nested": [1, 2, 3]}'


@pytest.mark.asyncio
async def test_allowed_resources_sql(datasette):
    result = await datasette.allowed_resources_sql(
        action="view-table",
        actor=None,
    )
    assert isinstance(result, ResourcesSQL)
    assert "all_rules AS" in result.sql
    assert result.params["action"] == "view-table"
