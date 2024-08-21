import json
import textwrap
from yaml import safe_dump
from ruamel.yaml import YAML


def metadata_example(cog, data=None, yaml=None):
    assert data or yaml, "Must provide data= or yaml="
    assert not (data and yaml), "Cannot use data= and yaml="
    output_yaml = None
    if yaml:
        # dedent it first
        yaml = textwrap.dedent(yaml).strip()
        data = YAML().load(yaml)
        output_yaml = yaml
    else:
        output_yaml = safe_dump(data, sort_keys=False)
    cog.out("\n.. tab:: metadata.yaml\n\n")
    cog.out("    .. code-block:: yaml\n\n")
    cog.out(textwrap.indent(output_yaml, "        "))
    cog.out("\n\n.. tab:: metadata.json\n\n")
    cog.out("    .. code-block:: json\n\n")
    cog.out(textwrap.indent(json.dumps(data, indent=2), "        "))
    cog.out("\n")


def config_example(
    cog, input, yaml_title="datasette.yaml", json_title="datasette.json"
):
    if type(input) is str:
        data = YAML().load(input)
        output_yaml = input
    else:
        data = input
        output_yaml = safe_dump(input, sort_keys=False)
    cog.out("\n.. tab:: {}\n\n".format(yaml_title))
    cog.out("    .. code-block:: yaml\n\n")
    cog.out(textwrap.indent(output_yaml, "        "))
    cog.out("\n\n.. tab:: {}\n\n".format(json_title))
    cog.out("    .. code-block:: json\n\n")
    cog.out(textwrap.indent(json.dumps(data, indent=2), "        "))
    cog.out("\n")


def internal_schema(cog):
    import asyncio
    from datasette.app import Datasette
    from sqlite_utils import Database

    ds = Datasette()
    db = ds.get_internal_database()

    def get_schema(conn):
        return Database(conn).schema

    async def inner():
        await ds.invoke_startup()
        await ds._refresh_schemas()
        return await db.execute_fn(get_schema)

    schema = asyncio.run(inner())
    cog.out("\n.. code-block:: sql")
    cog.out("\n\n")
    cog.out(textwrap.indent(schema, "    "))
    cog.out("\n\n")
