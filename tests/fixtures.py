from datasette.app import Datasette
from datasette.fixtures import (
    EXTRA_DATABASE_SQL,
    write_extra_database,
    write_fixture_database,
)
from datasette.utils.testing import TestClient
import click
import contextlib
import json
import os
import pathlib
import pytest
import tempfile
import textwrap

# This temp file is used by one of the plugin config tests
TEMP_PLUGIN_SECRET_FILE = os.path.join(tempfile.gettempdir(), "plugin-secret")

PLUGINS_DIR = str(pathlib.Path(__file__).parent / "plugins")

EXPECTED_PLUGINS = [
    {
        "name": "messages_output_renderer.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["register_output_renderer"],
    },
    {
        "name": "my_plugin.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": [
            "actor_from_request",
            "asgi_wrapper",
            "database_actions",
            "extra_body_script",
            "extra_css_urls",
            "extra_js_urls",
            "extra_template_vars",
            "forbidden",
            "homepage_actions",
            "menu_links",
            "permission_resources_sql",
            "prepare_connection",
            "prepare_jinja2_environment",
            "query_actions",
            "register_actions",
            "register_facet_classes",
            "register_magic_parameters",
            "register_routes",
            "register_token_handler",
            "render_cell",
            "row_actions",
            "startup",
            "table_actions",
            "view_actions",
        ],
    },
    {
        "name": "my_plugin_2.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": [
            "actor_from_request",
            "asgi_wrapper",
            "extra_js_urls",
            "extra_template_vars",
            "handle_exception",
            "menu_links",
            "prepare_jinja2_environment",
            "register_routes",
            "render_cell",
            "startup",
            "table_actions",
        ],
    },
    {
        "name": "register_output_renderer.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["register_output_renderer"],
    },
    {
        "name": "sleep_sql_function.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["prepare_connection"],
    },
    {
        "name": "view_name.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["extra_template_vars"],
    },
]


@contextlib.contextmanager
def make_app_client(
    sql_time_limit_ms=None,
    max_returned_rows=None,
    cors=False,
    memory=False,
    settings=None,
    filename="fixtures.db",
    is_immutable=False,
    extra_databases=None,
    inspect_data=None,
    static_mounts=None,
    template_dir=None,
    config=None,
    metadata=None,
    crossdb=False,
):
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, filename)
        if is_immutable:
            files = []
            immutables = [filepath]
        else:
            files = [filepath]
            immutables = []
        write_fixture_database(filepath)
        if extra_databases is not None:
            for extra_filename, extra_sql in extra_databases.items():
                extra_filepath = os.path.join(tmpdir, extra_filename)
                if extra_sql == EXTRA_DATABASE_SQL:
                    write_extra_database(extra_filepath)
                else:
                    from datasette.utils.sqlite import sqlite3

                    c2 = sqlite3.connect(extra_filepath)
                    c2.executescript(extra_sql)
                    c2.close()
                # Insert at start to help test /-/databases ordering:
                files.insert(0, extra_filepath)
        os.chdir(os.path.dirname(filepath))
        settings = settings or {}
        for key, value in {
            "default_page_size": 50,
            "max_returned_rows": max_returned_rows or 100,
            "sql_time_limit_ms": sql_time_limit_ms or 200,
            # Default is 3 but this results in "too many open files"
            # errors when running the full test suite:
            "num_sql_threads": 1,
        }.items():
            if key not in settings:
                settings[key] = value
        ds = Datasette(
            files,
            immutables=immutables,
            memory=memory,
            cors=cors,
            metadata=metadata or METADATA,
            config=config or CONFIG,
            plugins_dir=PLUGINS_DIR,
            settings=settings,
            inspect_data=inspect_data,
            static_mounts=static_mounts,
            template_dir=template_dir,
            crossdb=crossdb,
        )
        yield TestClient(ds)
        # Close as many database connections as possible
        # to try and avoid too many open files error
        for db in ds.databases.values():
            if not db.is_memory:
                db.close()


@pytest.fixture(scope="session")
def app_client():
    with make_app_client() as client:
        yield client


@pytest.fixture(scope="session")
def app_client_no_files():
    ds = Datasette([])
    yield TestClient(ds)
    for db in ds.databases.values():
        db.close()


@pytest.fixture(scope="session")
def app_client_base_url_prefix():
    with make_app_client(settings={"base_url": "/prefix/"}) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_two_attached_databases():
    with make_app_client(
        extra_databases={"extra database.db": EXTRA_DATABASE_SQL}
    ) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_two_attached_databases_crossdb_enabled():
    with make_app_client(
        extra_databases={"extra database.db": EXTRA_DATABASE_SQL},
        crossdb=True,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_conflicting_database_names():
    with make_app_client(
        extra_databases={"foo.db": EXTRA_DATABASE_SQL, "foo-bar.db": EXTRA_DATABASE_SQL}
    ) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_two_attached_databases_one_immutable():
    with make_app_client(
        is_immutable=True, extra_databases={"extra database.db": EXTRA_DATABASE_SQL}
    ) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_with_trace():
    with make_app_client(settings={"trace_debug": True}, is_immutable=True) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_shorter_time_limit():
    with make_app_client(20) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_returned_rows_matches_page_size():
    with make_app_client(max_returned_rows=50) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_larger_cache_size():
    with make_app_client(settings={"cache_size_kb": 2500}) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_csv_max_mb_one():
    with make_app_client(settings={"max_csv_mb": 1}) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_with_dot():
    with make_app_client(filename="fixtures.dot.db") as client:
        yield client


@pytest.fixture(scope="session")
def app_client_with_cors():
    with make_app_client(is_immutable=True, cors=True) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_immutable_and_inspect_file():
    inspect_data = {"fixtures": {"tables": {"sortable": {"count": 100}}}}
    with make_app_client(is_immutable=True, inspect_data=inspect_data) as client:
        yield client


CONFIG = {
    "plugins": {
        "name-of-plugin": {"depth": "root"},
        "env-plugin": {"foo": {"$env": "FOO_ENV"}},
        "env-plugin-list": [{"in_a_list": {"$env": "FOO_ENV"}}],
        "file-plugin": {"foo": {"$file": TEMP_PLUGIN_SECRET_FILE}},
    },
    "databases": {
        "fixtures": {
            "plugins": {"name-of-plugin": {"depth": "database"}},
            "tables": {
                "simple_primary_key": {
                    "plugins": {
                        "name-of-plugin": {
                            "depth": "table",
                            "special": "this-is-simple_primary_key",
                        }
                    },
                },
                "sortable": {
                    "plugins": {"name-of-plugin": {"depth": "table"}},
                },
            },
            "queries": {
                "𝐜𝐢𝐭𝐢𝐞𝐬": "select id, name from facet_cities order by id limit 1;",
                "pragma_cache_size": "PRAGMA cache_size;",
                "magic_parameters": {
                    "sql": "select :_header_user_agent as user_agent, :_now_datetime_utc as datetime",
                },
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
                    "description_html": "<b>Demonstrating</b> simple like search",
                    "fragment": "fragment-goes-here",
                    "hide_sql": True,
                },
            },
        }
    },
    "extra_css_urls": ["/static/extra-css-urls.css"],
}

METADATA = {
    "title": "Datasette Fixtures",
    "description_html": 'An example SQLite database demonstrating Datasette. <a href="/login-as-root">Sign in as root user</a>',
    "license": "Apache License 2.0",
    "license_url": "https://github.com/simonw/datasette/blob/main/LICENSE",
    "source": "tests/fixtures.py",
    "source_url": "https://github.com/simonw/datasette/blob/main/tests/fixtures.py",
    "about": "About Datasette",
    "about_url": "https://github.com/simonw/datasette",
    "databases": {
        "fixtures": {
            "description": "Test tables description",
            "tables": {
                "simple_primary_key": {
                    "description_html": "Simple <em>primary</em> key",
                    "title": "This <em>HTML</em> is escaped",
                },
                "sortable": {
                    "sortable_columns": [
                        "sortable",
                        "sortable_with_nulls",
                        "sortable_with_nulls_2",
                        "text",
                    ],
                },
                "no_primary_key": {"sortable_columns": [], "hidden": True},
                "primary_key_multiple_columns_explicit_label": {
                    "label_column": "content2"
                },
                "simple_view": {"sortable_columns": ["content"]},
                "searchable_view_configured_by_metadata": {
                    "fts_table": "searchable_fts",
                    "fts_pk": "pk",
                },
                "roadside_attractions": {
                    "columns": {
                        "name": "The name of the attraction",
                        "address": "The street address for the attraction",
                    }
                },
                "attraction_characteristic": {"sort_desc": "pk"},
                "facet_cities": {"sort": "name"},
                "paginated_view": {"size": 25},
            },
        }
    },
}


def assert_permissions_checked(datasette, actions):
    # actions is a list of "action" or (action, resource) tuples
    for action in actions:
        if isinstance(action, str):
            resource = None
        else:
            action, resource = action

        # Convert PermissionCheck dataclass to old resource format for comparison
        def check_matches(pc, action, resource):
            if pc.action != action:
                return False
            # Convert parent/child to old resource format
            if pc.parent and pc.child:
                pc_resource = (pc.parent, pc.child)
            elif pc.parent:
                pc_resource = pc.parent
            else:
                pc_resource = None
            return pc_resource == resource

        assert [
            pc
            for pc in datasette._permission_checks
            if check_matches(pc, action, resource)
        ], """Missing expected permission check: action={}, resource={}
        Permission checks seen: {}
        """.format(
            action,
            resource,
            json.dumps(
                [
                    {
                        "action": pc.action,
                        "parent": pc.parent,
                        "child": pc.child,
                        "result": pc.result,
                    }
                    for pc in datasette._permission_checks
                ],
                indent=4,
            ),
        )


@click.command()
@click.argument(
    "db_filename",
    default="fixtures.db",
    type=click.Path(file_okay=True, dir_okay=False),
)
@click.argument("config", required=False)
@click.argument("metadata", required=False)
@click.argument(
    "plugins_path", type=click.Path(file_okay=False, dir_okay=True), required=False
)
@click.option(
    "--recreate",
    is_flag=True,
    default=False,
    help="Delete and recreate database if it exists",
)
@click.option(
    "--extra-db-filename",
    type=click.Path(file_okay=True, dir_okay=False),
    help="Write out second test DB to this file",
)
def cli(db_filename, config, metadata, plugins_path, recreate, extra_db_filename):
    """Write out the fixtures database used by Datasette's test suite"""
    if metadata and not metadata.endswith(".json"):
        raise click.ClickException("Metadata should end with .json")
    if not db_filename.endswith(".db"):
        raise click.ClickException("Database file should end with .db")
    if pathlib.Path(db_filename).exists():
        if not recreate:
            raise click.ClickException(
                f"{db_filename} already exists, use --recreate to reset it"
            )
        else:
            pathlib.Path(db_filename).unlink()
    write_fixture_database(db_filename)
    print(f"Test tables written to {db_filename}")
    if metadata:
        with open(metadata, "w") as fp:
            fp.write(json.dumps(METADATA, indent=4))
        print(f"- metadata written to {metadata}")
    if config:
        with open(config, "w") as fp:
            fp.write(json.dumps(CONFIG, indent=4))
        print(f"- config written to {config}")
    if plugins_path:
        path = pathlib.Path(plugins_path)
        if not path.exists():
            path.mkdir()
        test_plugins = pathlib.Path(__file__).parent / "plugins"
        for filepath in test_plugins.glob("*.py"):
            newpath = path / filepath.name
            newpath.write_text(filepath.read_text())
            print(f"  Wrote plugin: {newpath}")
    if extra_db_filename:
        if pathlib.Path(extra_db_filename).exists():
            if not recreate:
                raise click.ClickException(
                    f"{extra_db_filename} already exists, use --recreate to reset it"
                )
            else:
                pathlib.Path(extra_db_filename).unlink()
        write_extra_database(extra_db_filename)
        print(f"Test tables written to {extra_db_filename}")


if __name__ == "__main__":
    cli()
