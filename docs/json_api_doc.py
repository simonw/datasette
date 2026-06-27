import asyncio
import json
import pathlib
import tempfile
import textwrap


def table_extras(cog):
    from datasette.extras import ExtraScope
    from datasette.views.table_extras import table_extra_registry

    scopes = [
        (
            ExtraScope.TABLE,
            "Table JSON responses",
            "The available table extras are listed below.",
        ),
        (
            ExtraScope.ROW,
            "Row JSON responses",
            "The following extras are available for row JSON responses.",
        ),
        (
            ExtraScope.QUERY,
            "Query JSON responses",
            (
                "The following extras are available for arbitrary SQL query "
                "responses and stored, named query responses."
            ),
        ),
    ]
    classes_by_scope = [
        (scope, heading, intro, table_extra_registry.public_classes_for_scope(scope))
        for scope, heading, intro in scopes
    ]

    live_examples = asyncio.run(
        _fetch_live_examples(
            [
                (scope, cls)
                for scope, _, _, classes in classes_by_scope
                for cls in classes
            ]
        )
    )
    cog.out("\n")
    for scope, heading, intro, classes in classes_by_scope:
        cog.out("{}\n{}\n\n".format(heading, "~" * len(heading)))
        cog.out("{}\n\n".format(intro))
        for cls in classes:
            examples = _examples_for_scope(cls, scope)
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
            for example in examples:
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


def _examples_for_scope(cls, scope):
    examples = cls.example_for_scope(scope)
    if examples is None:
        return []
    if isinstance(examples, list):
        return examples
    return [examples]


async def _fetch_live_examples(scoped_classes):
    from datasette.app import Datasette
    from datasette.fixtures import write_fixture_database

    examples = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = pathlib.Path(tmpdir) / "fixtures.db"
        write_fixture_database(db_path)
        datasette = Datasette(
            [str(db_path)],
            settings={"num_sql_threads": 1},
            metadata={
                "databases": {
                    "fixtures": {
                        "tables": {
                            "facetable": {
                                "description": "A demo table of places, used to demonstrate facets",
                                "columns": {"state": "Two letter US state code"},
                            }
                        }
                    }
                }
            },
            config={
                "databases": {
                    "fixtures": {
                        "tables": {
                            "facetable": {
                                "column_types": {"tags": "json"},
                            }
                        },
                        "queries": {
                            "neighborhood_search": {
                                "sql": textwrap.dedent("""
                                    select _neighborhood, facet_cities.name, state
                                    from facetable
                                        join facet_cities
                                            on facetable._city_id = facet_cities.id
                                    where _neighborhood like '%' || :text || '%'
                                    order by _neighborhood;
                                """),
                                "title": "Search neighborhoods",
                            }
                        },
                    }
                }
            },
        )
        try:
            for scope, cls in scoped_classes:
                for example in _examples_for_scope(cls, scope):
                    if not example.path:
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
