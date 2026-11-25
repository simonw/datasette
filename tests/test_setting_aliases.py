"""
A setting that has been renamed keeps working under its previous name,
which is listed in datasette.app.SETTING_ALIASES but is not documented.
"""

import json
import re

import pytest
from click.testing import CliRunner

from datasette.app import DEFAULT_SETTINGS, SETTING_ALIASES, Datasette
from datasette.cli import cli
from datasette.utils import StartupError

from .fixtures import make_ds, make_ds_on_disk


def test_aliases_point_at_real_settings():
    for old_name, name in SETTING_ALIASES.items():
        assert name in DEFAULT_SETTINGS
        assert old_name not in DEFAULT_SETTINGS


@pytest.mark.parametrize(
    "kwargs",
    (
        {"settings": {"max_csv_mb": 7, "allow_csv_stream": False}},
        {"config": {"settings": {"max_csv_mb": 7, "allow_csv_stream": False}}},
        {"settings": {"max_stream_mb": 7, "default_allow_stream": False}},
    ),
)
def test_previous_names_set_the_renamed_settings(kwargs):
    ds = Datasette(memory=True, **kwargs)
    try:
        # Readable using either name
        assert ds.setting("max_stream_mb") == 7
        assert ds.setting("max_csv_mb") == 7
        assert ds.setting("default_allow_stream") is False
        assert ds.setting("allow_csv_stream") is False
    finally:
        ds.close()


def test_settings_parameter_overrides_config_using_either_name():
    ds = Datasette(
        memory=True,
        config={"settings": {"max_stream_mb": 5}},
        settings={"max_csv_mb": 9},
    )
    try:
        assert ds.setting("max_stream_mb") == 9
    finally:
        ds.close()


async def test_settings_json_only_lists_current_names():
    ds = Datasette(memory=True, settings={"max_csv_mb": 7})
    try:
        settings = (await ds.client.get("/-/settings.json")).json()
        assert settings["max_stream_mb"] == 7
        assert not set(SETTING_ALIASES) & set(settings)
    finally:
        ds.close()


def test_settings_dict_for_templates_includes_previous_names():
    ds = Datasette(memory=True, settings={"max_stream_mb": 7})
    try:
        settings = ds.settings_dict()
        assert settings["max_stream_mb"] == 7
        assert settings["max_csv_mb"] == 7
        assert settings["allow_csv_stream"] is True
    finally:
        ds.close()


@pytest.mark.parametrize(
    "kwargs,source",
    (
        ({"settings": {"max_csv_mb": 1, "max_stream_mb": 2}}, "settings parameter"),
        (
            {"config": {"settings": {"max_stream_mb": 2, "max_csv_mb": 1}}},
            "config file",
        ),
    ),
)
def test_setting_both_names_is_an_error(kwargs, source):
    with pytest.raises(StartupError) as ex:
        Datasette(memory=True, **kwargs)
    assert str(ex.value) == (
        "Setting 'max_stream_mb' and its previous name 'max_csv_mb' "
        f"are both set in {source}"
    )


def test_previous_name_is_still_type_checked():
    with pytest.raises(StartupError, match="'max_stream_mb'.*incorrect type"):
        Datasette(memory=True, settings={"max_csv_mb": "lots"})


@pytest.mark.parametrize(
    "args",
    (
        ["--setting", "max_csv_mb", "7"],
        ["--setting", "settings.max_csv_mb", "7"],
        ["--setting", "max_stream_mb", "7"],
    ),
)
def test_cli_setting(args):
    result = CliRunner().invoke(cli, ["--get", "/-/settings.json"] + args)
    assert result.exit_code == 0, result.output
    settings = json.loads(result.output)
    assert settings["max_stream_mb"] == 7
    assert "max_csv_mb" not in settings


def test_cli_previous_name_is_still_validated():
    result = CliRunner().invoke(
        cli, ["--get", "/-/settings.json", "--setting", "allow_csv_stream", "maybe"]
    )
    assert result.exit_code != 0
    assert "should be on/off/true/false/1/0" in result.output


def test_help_settings_only_lists_current_names():
    result = CliRunner().invoke(cli, ["--help-settings"])
    assert "max_stream_mb" in result.output
    assert "default_allow_stream" in result.output
    # allow_facet is part of default_allow_facet, so compare whole names
    listed = set(re.findall(r"^\s+(\w+)\s", result.output, re.MULTILINE))
    assert "default_allow_facet" in listed
    assert not set(SETTING_ALIASES) & listed


async def test_previous_name_still_disables_streaming():
    ds = Datasette(memory=True, settings={"allow_csv_stream": False})
    try:
        response = await ds.client.get("/_memory/-/query.csv?sql=select+1&_stream=1")
        assert response.status_code == 400
        assert response.text == "Streaming is disabled"
    finally:
        ds.close()


@pytest.mark.parametrize(
    "old_name,name",
    (
        ("allow_facet", "default_allow_facet"),
        ("allow_download", "default_allow_download"),
        ("allow_csv_stream", "default_allow_stream"),
    ),
)
def test_default_allow_settings_previous_names(old_name, name):
    assert SETTING_ALIASES[old_name] == name
    ds = Datasette(memory=True, settings={old_name: False})
    try:
        assert ds.setting(name) is False
        assert ds.setting(old_name) is False
        assert ds.settings_dict()[old_name] is False
    finally:
        ds.close()


async def test_previous_name_still_disables_facets():
    ds = await make_ds(settings={"allow_facet": False})
    response = await ds.client.get("/fixtures/facetable.json?_facet=state")
    assert response.status_code == 400
    assert response.json()["error"] == "_facet= is not allowed"


async def test_previous_name_still_disables_downloads():
    with make_ds_on_disk(is_immutable=True, settings={"allow_download": False}) as ds:
        client = ds.client
        assert (await client.get("/fixtures.db")).status_code == 403


def test_allow_stream_is_not_a_setting():
    # allow_stream was never in a release, so it has no alias
    assert "allow_stream" not in SETTING_ALIASES
    assert "allow_stream" not in DEFAULT_SETTINGS
