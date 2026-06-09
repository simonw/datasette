import asyncio
import json
import pathlib
import tempfile
import textwrap


def table_extras(cog):
    from datasette.extras import ExtraScope
    from datasette.views.table_extras import table_extra_registry

    classes = table_extra_registry.public_classes_for_scope(ExtraScope.TABLE)

    live_examples = asyncio.run(_fetch_live_examples(classes))
    cog.out("\n")
    for cls in classes:
        example = cls.example
        description = cls.description or ""
        notes = []
        if cls.expensive:
            notes.append("May execute additional queries.")
        if cls.docs_note:
            notes.append(cls.docs_note)
        if notes:
            description = "{} ({})".format(description, " ".join(notes)).strip()

        cog.out("``{}``\n".format(cls.key()))
        cog.out("    {}\n\n".format(description))
        if example is None:
            continue

        if example.path:
            value = live_examples[(example.path, example.key or cls.key())]
            cog.out("    ``GET {}``\n\n".format(example.path))
        else:
            value = example.value
        if example.note:
            cog.out("    {}\n\n".format(example.note))
        cog.out("    .. code-block:: json\n\n")
        cog.out(textwrap.indent(json.dumps(value, indent=2), "        "))
        cog.out("\n\n")


async def _fetch_live_examples(classes):
    from datasette.app import Datasette
    from datasette.fixtures import write_fixture_database

    examples = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = pathlib.Path(tmpdir) / "fixtures.db"
        write_fixture_database(db_path)
        datasette = Datasette([str(db_path)], settings={"num_sql_threads": 1})
        try:
            for cls in classes:
                example = cls.example
                if example is None or not example.path:
                    continue
                key = example.key or cls.key()
                response = await datasette.client.get(example.path)
                assert response.status_code == 200, example.path
                data = response.json()
                assert key in data, "{} missing from {}".format(key, example.path)
                examples[(example.path, key)] = data[key]
        finally:
            for db in datasette.databases.values():
                if not db.is_memory:
                    db.close()
    return examples
