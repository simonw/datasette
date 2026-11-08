import copy
import hashlib
import json
import pathlib
import re
import urllib.parse

import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette
from datasette.utils import allowed_pragmas

from .fixtures import make_app_client
from .utils import assert_footer_links, inner_html


def test_homepage(app_client_two_attached_databases):
    response = app_client_two_attached_databases.get("/")
    assert response.status_code == 200
    assert "text/html; charset=utf-8" == response.headers["content-type"]
    # Should have a html lang="en" attribute
    assert '<html lang="en">' in response.text
    soup = Soup(response.content, "html.parser")
    assert "Datasette Fixtures" == soup.find("h1").text
    assert (
        "An example SQLite database demonstrating Datasette. Sign in as root user"
        == soup.select(".metadata-description")[0].text.strip()
    )
    # Should be two attached databases
    assert [
        {"href": "/extra+database", "text": "extra database"},
        {"href": "/fixtures", "text": "fixtures"},
    ] == [{"href": a["href"], "text": a.text.strip()} for a in soup.select("h2 a")]
    # Database should show count text and attached tables
    h2 = soup.select("h2")[0]
    assert "extra database" == h2.text.strip()
    counts_p, links_p = h2.find_all_next("p")[:2]
    # Shadow tables of the external-content index are denied, so they do not
    # contribute to the table or row totals.
    assert (
        "2 rows in 1 table, 2 rows in 1 hidden table, 1 view" == counts_p.text.strip()
    )
    # We should only show visible, not hidden tables here:
    table_links = [
        {"href": a["href"], "text": a.text.strip()} for a in links_p.find_all("a")
    ]
    assert [
        {"href": r"/extra+database/searchable", "text": "searchable"},
        {"href": r"/extra+database/searchable_view", "text": "searchable_view"},
    ] == table_links


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql,expected",
    (
        (["create view one as select 1 as n"], "0 tables, 1 view"),
        (
            ["create view one as select 1 as n", "create view two as select 2 as n"],
            "0 tables, 2 views",
        ),
        (
            ["create table t (id integer primary key)", "create view v as select 1"],
            "0 rows in 1 table, 1 view",
        ),
    ),
)
async def test_homepage_database_summary_separators(sql, expected):
    # https://github.com/simonw/datasette/issues/2012
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("summary_separators")
    for statement in sql:
        await db.execute_write(statement)
    response = await ds.client.get("/")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    h2 = next(h2 for h2 in soup.select("h2") if h2.text.strip() == "summary_separators")
    counts_p = h2.find_next("p")
    assert " ".join(counts_p.text.split()) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("/", "/-/"))
async def test_homepage_alternative_location(path, tmp_path_factory):
    template_dir = tmp_path_factory.mktemp("templates")
    (template_dir / "index.html").write_text("Custom homepage", "utf-8")
    datasette = Datasette(template_dir=str(template_dir))
    response = await datasette.client.get(path)
    assert response.status_code == 200
    html = response.text
    if path == "/":
        assert html == "Custom homepage"
    else:
        assert '<meta name="robots" content="noindex">' in html


@pytest.mark.asyncio
async def test_homepage_alternative_redirect(ds_client):
    response = await ds_client.get("/-")
    assert response.status_code == 301


@pytest.mark.asyncio
async def test_http_head(ds_client):
    response = await ds_client.head("/")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_homepage_options(ds_client):
    response = await ds_client.options("/")
    assert response.status_code == 200
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_favicon(ds_client):
    response = await ds_client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "max-age=3600, public"
    assert int(response.headers["content-length"]) > 100
    assert response.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_static(ds_client):
    response = await ds_client.get("/-/static/app2.css")
    assert response.status_code == 404
    response = await ds_client.get("/-/static/app.css")
    assert response.status_code == 200
    assert "text/css" == response.headers["content-type"]
    assert "etag" in response.headers
    etag = response.headers.get("etag")
    response = await ds_client.get("/-/static/app.css", headers={"if-none-match": etag})
    assert response.status_code == 304


@pytest.mark.asyncio
async def test_static_hash_cache_control(ds_client):
    hashed_url = ds_client.ds.static("app.css")
    response = await ds_client.get(hashed_url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "max-age=31536000, immutable, public"

    response = await ds_client.get(
        hashed_url, headers={"if-none-match": response.headers["etag"]}
    )
    assert response.status_code == 304
    assert response.headers["cache-control"] == "max-age=31536000, immutable, public"

    response = await ds_client.get("/-/static/app.css?_hash=incorrect")
    assert response.status_code == 200
    assert "cache-control" not in response.headers


def test_static_mounts():
    with make_app_client(
        static_mounts=[("custom-static", str(pathlib.Path(__file__).parent))]
    ) as client:
        response = client.get("/custom-static/test_html.py")
        assert response.status_code == 200
        response = client.get("/custom-static/not_exists.py")
        assert response.status_code == 404
        response = client.get("/custom-static/../LICENSE")
        assert response.status_code == 404


def test_static_mounts_hash_cache_control():
    mount_path = pathlib.Path(__file__).parent
    with make_app_client(static_mounts=[("custom-static", str(mount_path))]) as client:
        response = client.get(client.ds.static("test_html.py", mount="custom-static"))
        assert response.status_code == 200
        assert (
            response.headers["cache-control"] == "max-age=31536000, immutable, public"
        )

        incorrect_hash = hashlib.sha256(b"incorrect").hexdigest()[:12]
        response = client.get(f"/custom-static/test_html.py?_hash={incorrect_hash}")
        assert response.status_code == 200
        assert "cache-control" not in response.headers


def test_memory_database_page():
    with make_app_client(memory=True) as client:
        response = client.get("/_memory")
        assert response.status_code == 200


def test_not_allowed_methods():
    with make_app_client(memory=True) as client:
        for method in ("post", "put", "patch", "delete"):
            response = client.request(path="/_memory", method=method.upper())
            assert response.status_code == 405


@pytest.mark.asyncio
async def test_database_page(ds_client):
    response = await ds_client.get("/fixtures")
    soup = Soup(response.text, "html.parser")
    # Should have a <textarea> for executing SQL
    assert "<textarea" in response.text

    # And a list of tables
    for fragment in (
        '<h2 id="tables">Tables',
        '<h3><a href="/fixtures/sortable">sortable</a></h3>',
        "<p><em>pk, foreign_key_with_label, foreign_key_with_blank_label, ",
    ):
        assert fragment in response.text

    # And views
    views_ul = soup.find("h2", string="Views").find_next_sibling("ul")
    assert views_ul is not None
    assert [
        ("/fixtures/binary_data_view", "binary_data_view"),
        ("/fixtures/paginated_view", "paginated_view"),
        ("/fixtures/searchable_view", "searchable_view"),
        (
            "/fixtures/searchable_view_configured_by_metadata",
            "searchable_view_configured_by_metadata",
        ),
        ("/fixtures/simple_view", "simple_view"),
    ] == sorted([(a["href"], a.text) for a in views_ul.find_all("a")])

    # And a list of stored queries
    queries_ul = soup.find("h2", string="Queries").find_next_sibling("ul")
    assert queries_ul is not None
    assert [
        ("/fixtures/magic_parameters", "magic_parameters"),
        ("/fixtures/neighborhood_search#fragment-goes-here", "Search neighborhoods"),
        ("/fixtures/pragma_cache_size", "pragma_cache_size"),
        (
            "/fixtures/~F0~9D~90~9C~F0~9D~90~A2~F0~9D~90~AD~F0~9D~90~A2~F0~9D~90~9E~F0~9D~90~AC",
            "𝐜𝐢𝐭𝐢𝐞𝐬",
        ),
    ] == sorted(
        [(a["href"], a.text) for a in queries_ul.find_all("a")], key=lambda p: p[0]
    )


@pytest.mark.asyncio
async def test_invalid_custom_sql(ds_client):
    response = await ds_client.get("/fixtures/-/query?sql=.schema")
    assert response.status_code == 400
    assert "Statement must be a SELECT" in response.text


@pytest.mark.asyncio
async def test_disallowed_custom_sql_pragma(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query?sql=SELECT+*+FROM+pragma_not_on_allow_list('idx52')"
    )
    assert response.status_code == 400
    pragmas = ", ".join(f"pragma_{pragma}()" for pragma in allowed_pragmas)
    assert (
        f"Statement contained a disallowed PRAGMA. Allowed pragma functions are {pragmas}"
        in response.text
    )


@pytest.mark.xfail(reason="Sometimes flaky in CI due to timing issues")
def test_sql_time_limit(app_client_shorter_time_limit):
    response = app_client_shorter_time_limit.get(
        "/fixtures/-/query?sql=select+sleep(0.5)"
    )
    assert 400 == response.status
    expected_html_fragments = [
        """
        <a href="https://docs.datasette.io/en/stable/settings.html#sql-time-limit-ms">sql_time_limit_ms</a>
    """.strip(),
        '<textarea style="width: 90%">select sleep(0.5)</textarea>',
    ]
    for expected_html_fragment in expected_html_fragments:
        assert expected_html_fragment in response.text


def test_row_page_does_not_truncate():
    with make_app_client(settings={"truncate_cells_html": 5}) as client:
        response = client.get("/fixtures/facetable/1")
        assert response.status_code == 200
        value = Soup(response.content, "html.parser").select_one(
            ".row-field.col-neighborhood-b352a7 .row-field-value"
        )
        assert value.string == "Mission"


def test_query_page_truncates():
    with make_app_client(settings={"truncate_cells_html": 5}) as client:
        response = client.get(
            "/fixtures/-/query?"
            + urllib.parse.urlencode(
                {
                    "sql": "select 'this is longer than 5' as a, 'https://example.com/' as b"
                }
            )
        )
        assert response.status_code == 200
        table = Soup(response.content, "html.parser").find("table")
        tds = table.find_all("td")
        assert [str(td) for td in tds] == [
            '<td class="col-a">this …</td>',
            '<td class="col-b"><a href="https://example.com/">http…</a></td>',
        ]


@pytest.mark.asyncio
async def test_query_page_with_no_sql(ds_client):
    # https://github.com/simonw/datasette/issues/2743
    response = await ds_client.get("/fixtures/-/query")
    assert response.status_code == 200
    assert '<textarea id="sql-editor" name="sql"' in response.text
    assert 'class="rows-and-columns"' not in response.text


@pytest.mark.asyncio
async def test_query_csv_with_no_sql_is_400(ds_client):
    # https://github.com/simonw/datasette/issues/2743
    response = await ds_client.get("/fixtures/-/query.csv")
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_classes",
    [
        ("/", ["index"]),
        ("/fixtures", ["db", "db-fixtures"]),
        ("/fixtures/-/query?sql=select+1", ["query", "db-fixtures"]),
        (
            "/fixtures/simple_primary_key",
            ["table", "db-fixtures", "table-simple_primary_key"],
        ),
        (
            "/fixtures/neighborhood_search",
            ["query", "db-fixtures", "query-neighborhood_search"],
        ),
        (
            "/fixtures/table~2Fwith~2Fslashes~2Ecsv",
            ["table", "db-fixtures", "table-tablewithslashescsv-fa7563"],
        ),
        (
            "/fixtures/simple_primary_key/1",
            ["row", "db-fixtures", "table-simple_primary_key"],
        ),
    ],
)
async def test_css_classes_on_body(ds_client, path, expected_classes):
    response = await ds_client.get(path)
    assert response.status_code == 200
    classes = re.search(r'<body class="(.*)">', response.text).group(1).split()
    assert classes == expected_classes


templates_considered_re = re.compile(r"<!-- Templates considered: (.*?) -->")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_considered",
    [
        ("/", "*index.html"),
        ("/fixtures", "database-fixtures.html, *database.html"),
        (
            "/fixtures/simple_primary_key",
            "table-fixtures-simple_primary_key.html, *table.html",
        ),
        (
            "/fixtures/table~2Fwith~2Fslashes~2Ecsv",
            "table-fixtures-tablewithslashescsv-fa7563.html, *table.html",
        ),
        (
            "/fixtures/simple_primary_key/1",
            "row-fixtures-simple_primary_key.html, *row.html",
        ),
    ],
)
async def test_templates_considered(ds_client, path, expected_considered):
    response = await ds_client.get(path)
    assert response.status_code == 200
    match = templates_considered_re.search(response.text)
    assert match, "No templates considered comment found"
    actual_considered = match.group(1)
    assert actual_considered == expected_considered


@pytest.mark.asyncio
async def test_row_json_export_link(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key/1")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    view = soup.select_one(
        '#export-dialog [data-export-format="json"] a[data-export-action="view"]'
    )
    assert view["href"] == "/fixtures/simple_primary_key/1.json"


@pytest.mark.asyncio
async def test_query_export_dialog(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query?sql=select+:name+as+n&name=hello&_hide_sql=1"
    )
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert "This data as" not in response.text
    button = soup.select_one(".table-tools button.export-data-button")
    assert button["commandfor"] == "export-dialog"
    dialog = soup.select_one("datasette-modal > dialog#export-dialog")
    assert dialog.select_one(".modal-meta").text == "fixtures"
    assert dialog.select_one(".export-scope").get_text() == (
        'Query results from this SQL query with name = "hello"'
    )
    # Queries are not paginated, so every format returns these rows
    assert {el.text for el in dialog.select("[data-export-rows]")} == {"All 1 row"}
    assert not dialog.has_attr("data-count-url")
    # No object shape: queries have no primary keys
    assert [
        (
            row.select_one(".export-option-name").text,
            row.select_one('[data-export-action="view"]').get("href")
            or row.select_one('[data-export-action="view"]').get("formaction"),
        )
        for row in dialog.select(".export-option")
        if not row["data-export-format"].startswith("format-")
    ] == [
        # TSV and CSV submit the form below, with TSV sent to .tsv instead
        ("TSV", "/fixtures/-/query.tsv"),
        ("CSV", None),
        (
            "JSON",
            "/fixtures/-/query.json?sql=select+:name+as+n&name=hello&_hide_sql=1",
        ),
        (
            "JSON array",
            "/fixtures/-/query.json?sql=select+:name+as+n&name=hello&_hide_sql=1&_shape=array",
        ),
        (
            "Newline-delimited JSON",
            "/fixtures/-/query.json?sql=select+:name+as+n&name=hello&_hide_sql=1&_shape=array&_nl=on",
        ),
    ]
    # Downloads are named after the database for arbitrary SQL. The TSV and
    # CSV downloads are form submits with _dl=on, named by Content-Disposition.
    assert [el["download"] for el in dialog.select('a[data-export-action="download"]')][
        :3
    ] == ["fixtures.json", "fixtures.json", "fixtures.jsonl"]
    # No TSV and CSV options: labels and streaming do not apply to queries
    form = dialog.select_one("form.export-csv-form")
    assert form["action"] == "/fixtures/-/query.csv"
    assert [str(input) for input in form.find_all("input")] == [
        '<input name="sql" type="hidden" value="select :name as n"/>',
        '<input name="name" type="hidden" value="hello"/>',
        '<input name="_hide_sql" type="hidden" value="1"/>',
        '<input name="_size" type="hidden" value="max"/>',
    ]
    # export-dialog.js is loaded when the dialog first opens, not up front
    assert not soup.find("script", src=re.compile(r"export-dialog\.js"))
    assert re.search(
        r'const src = "/-/static/export-dialog\.js\?_hash=\w+";', response.text
    )


@pytest.mark.asyncio
async def test_query_export_dialog_truncated(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query?sql=select+*+from+compound_three_primary_keys"
    )
    soup = Soup(response.text, "html.parser")
    # max_returned_rows is 100 for these tests
    assert "Custom SQL query returning more than 100 rows" in response.text
    dialog = soup.select_one("dialog#export-dialog")
    assert {
        (el.text, el["data-partial"]) for el in dialog.select("[data-export-rows]")
    } == {("First 100 rows", "true")}


@pytest.mark.asyncio
async def test_stored_query_export_scope(ds_client):
    response = await ds_client.get("/fixtures/neighborhood_search?text=town")
    soup = Soup(response.text, "html.parser")
    assert soup.select_one(".export-scope").get_text() == (
        'Query results from the neighborhood_search query with text = "town"'
    )


@pytest.mark.asyncio
async def test_query_export_dialog_keeps_blank_parameters(ds_client):
    response = await ds_client.get("/fixtures/-/query?sql=select+:name+as+n&name=")
    soup = Soup(response.text, "html.parser")
    hidden = soup.select("form.export-csv-form input[type=hidden]")
    assert [(i["name"], i["value"]) for i in hidden] == [
        ("sql", "select :name as n"),
        ("name", ""),
        ("_size", "max"),
    ]


@pytest.mark.asyncio
async def test_query_export_dialog_not_shown_without_rows(ds_client):
    response = await ds_client.get("/fixtures/-/query?sql=select+1+where+0")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert soup.select_one("#export-dialog") is None
    assert soup.select_one(".export-data-button") is None


@pytest.mark.asyncio
async def test_query_parameter_form_fields(ds_client):
    response = await ds_client.get("/fixtures/-/query?sql=select+:name")
    assert response.status_code == 200
    assert (
        '<label for="qp1">name</label> <input type="text" id="qp1" name="name" value="" data-parameter-control data-parameter-name="name">'
        in response.text
    )
    assert 'data-parameters-url="/fixtures/-/query/parameters"' in response.text
    assert 'id="sql-parameters-section"' in response.text
    assert "setupSqlParameterRefresh" in response.text
    response2 = await ds_client.get("/fixtures/-/query?sql=select+:name&name=hello")
    assert response2.status_code == 200
    assert (
        '<label for="qp1">name</label> <input type="text" id="qp1" name="name" value="hello" data-parameter-control data-parameter-name="name">'
        in response2.text
    )


@pytest.mark.asyncio
async def test_database_page_sql_parameter_refresh_markup(ds_client):
    response = await ds_client.get("/fixtures")
    assert response.status_code == 200
    assert 'data-parameters-url="/fixtures/-/query/parameters"' in response.text
    assert 'id="sql-parameters-section"' in response.text
    assert "setupSqlParameterRefresh" in response.text


def row_fields(html):
    "Return [(column, field classes, value inner HTML)] for a row page"
    soup = Soup(html, "html.parser")
    fields = []
    for field in soup.select("dl.row-fields > .row-field"):
        value = field.select_one(".row-field-value")
        empty = field.select_one(".row-field-empty")
        fields.append(
            (
                field["data-column"],
                " ".join(field["class"]),
                value.decode_contents().strip() if value else empty["data-label"],
            )
        )
    return fields


@pytest.mark.asyncio
async def test_row_html_simple_primary_key(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key/1")
    assert response.status_code == 200
    assert row_fields(response.text) == [
        ("id", "row-field col-id type-int", "<strong>1</strong>"),
        ("content", "row-field col-content type-str", "hello"),
    ]


@pytest.mark.asyncio
async def test_row_html_no_primary_key(ds_client):
    response = await ds_client.get("/fixtures/no_primary_key/1")
    assert response.status_code == 200
    assert row_fields(response.text) == [
        ("rowid", "row-field col-rowid type-int", "<strong>1</strong>"),
        ("content", "row-field col-content type-str", "1"),
        ("a", "row-field col-a type-str", "a1"),
        ("b", "row-field col-b type-str", "b1"),
        ("c", "row-field col-c type-str", "c1"),
    ]


@pytest.mark.asyncio
async def test_row_html_copy_buttons(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key/1")
    soup = Soup(response.text, "html.parser")
    buttons = soup.select(".row-field .row-field-copy")
    assert [(b["aria-label"], b["data-copy-value"], b["title"]) for b in buttons] == [
        ("Copy id", "1", "Copy to clipboard"),
        ("Copy content", "hello", "Copy to clipboard"),
    ]


@pytest.mark.asyncio
async def test_row_html_long_value_toggle():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("test_row_html_long_value_toggle")
    await db.execute_write(
        "create table t (id integer primary key, short text, long text)"
    )
    await db.execute_write("insert into t values (1, 'hi', ?)", ["word " * 1000])
    response = await ds.client.get("/test_row_html_long_value_toggle/t/1")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert soup.select_one(".row-field.col-short .row-field-expand") is None
    button = soup.select_one(".row-field.col-long .row-field-expand")
    # The label comes from CSS so it is left out when the page is copied
    assert button["data-label"] == "Show all 5,000 characters"
    assert button["aria-label"] == "Show all 5,000 characters"
    assert button.text == ""
    assert button["aria-expanded"] == "false"


@pytest.mark.asyncio
async def test_row_html_null_values_have_no_copy_button(ds_client):
    # Row 2 has a primary key and nulls in every other column
    response = await ds_client.get("/fixtures/foreign_key_references/2")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    fields = soup.select(".row-field")
    assert [f["data-column"] for f in fields if f.select_one(".row-field-copy")] == [
        "pk"
    ]
    nulls = [f.select_one(".row-field-empty") for f in fields[1:]]
    # The label comes from CSS so it is left out when the page is copied
    assert [(n["data-label"], n.text) for n in nulls] == [("null", "")] * (
        len(fields) - 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_text,expected_link",
    (
        (
            "/fixtures/facet_cities/1",
            "6 facetable rows via _city_id",
            "/fixtures/facetable?_city_id__exact=1",
        ),
        (
            "/fixtures/attraction_characteristic/2",
            "3 roadside_attraction_characteristics rows via characteristic_id",
            "/fixtures/roadside_attraction_characteristics?characteristic_id=2",
        ),
    ),
)
async def test_row_links_from_other_tables(
    ds_client, path, expected_text, expected_link
):
    response = await ds_client.get(path)
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    li = soup.select_one(".row-referenced-by li")
    text = re.sub(r"\s+", " ", li.text.strip())
    assert text == expected_text
    link = li.find("a")["href"]
    assert link == expected_link


@pytest.mark.asyncio
async def test_row_foreign_key_links(ds_client):
    # Row detail page should render foreign key values as hyperlinks
    response = await ds_client.get("/fixtures/foreign_key_references/1")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    # foreign_key_with_label=1 references simple_primary_key(id=1, content="hello")
    td = soup.select_one(".row-field.col-foreign_key_with_label .row-field-value")
    a = td.find("a")
    assert a is not None, "Expected foreign key value to be a hyperlink"
    assert a["href"] == "/fixtures/simple_primary_key/1"
    assert a.text == "hello"
    # Primary key column should be first and bold
    first_field = soup.select_one("dl.row-fields > .row-field")
    assert first_field["data-column"] == "pk"
    assert first_field.find("strong") is not None, "PK value should be bold"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    (
        ("/fixtures/compound_primary_key/a,b", ("a", "b")),
        ("/fixtures/compound_primary_key/a~2Fb,~2Ec~2Dd", ("a/b", ".c-d")),
    ),
)
async def test_row_html_compound_primary_key(ds_client, path, expected):
    response = await ds_client.get(path)
    assert response.status_code == 200
    pk1, pk2 = expected
    assert row_fields(response.text) == [
        ("pk1", "row-field col-pk1 type-str", f"<strong>{pk1}</strong>"),
        ("pk2", "row-field col-pk2 type-str", f"<strong>{pk2}</strong>"),
        ("content", "row-field col-content type-str", "c"),
    ]


@pytest.mark.asyncio
async def test_index_metadata(ds_client):
    response = await ds_client.get("/")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert "Datasette Fixtures" == soup.find("h1").text
    assert (
        'An example SQLite database demonstrating Datasette. <a href="/login-as-root">Sign in as root user</a>'
        == inner_html(soup.find("div", {"class": "metadata-description"}))
    )
    assert_footer_links(soup)


@pytest.mark.asyncio
async def test_database_metadata(ds_client):
    response = await ds_client.get("/fixtures")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    # Page title should be the default
    assert "fixtures" == soup.find("h1").text
    # Description should be custom
    assert "Test tables description" == inner_html(
        soup.find("div", {"class": "metadata-description"})
    )
    # The source/license should be inherited
    # assert_footer_links(soup) TODO(alex) ensure


@pytest.mark.asyncio
async def test_database_metadata_with_custom_sql(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query?sql=select+*+from+simple_primary_key"
    )
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    # Page title should be the default
    assert "fixtures" == soup.find("h1").text
    # Description should be custom
    assert "Custom SQL query returning" in soup.find("h3").text
    # The source/license should be inherited
    # assert_footer_links(soup)TODO(alex) ensure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,metadata,expected_text,expected_href",
    (
        ("/", {"about": "Instance about"}, "About: Instance about", None),
        (
            "/",
            {"about_url": "https://example.com/"},
            "About: https://example.com/",
            "https://example.com/",
        ),
        (
            "/data",
            {"databases": {"data": {"about": "Database about"}}},
            "About: Database about",
            None,
        ),
        (
            "/data/t",
            {"databases": {"data": {"tables": {"t": {"about": "Table about"}}}}},
            "About: Table about",
            None,
        ),
    ),
)
async def test_about_without_source_or_license(
    path, metadata, expected_text, expected_href
):
    # https://github.com/simonw/datasette/issues/512
    ds = Datasette(metadata=metadata)
    db = ds.add_memory_database("test_about_without_source_or_license", name="data")
    await db.execute_write("create table if not exists t (id integer primary key)")
    response = await ds.client.get(path)
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    about_p = soup.select_one("section.content p:-soup-contains('About:')")
    assert about_p is not None
    # No leading separator, since there is no license or source before it
    assert " ".join(about_p.text.split()) == expected_text
    links = about_p.find_all("a")
    if expected_href:
        assert [a["href"] for a in links] == [expected_href]
    else:
        assert links == []


def test_database_download_for_immutable():
    with make_app_client(is_immutable=True) as client:
        assert not client.ds.databases["fixtures"].is_mutable
        # Regular page should have a download link
        response = client.get("/fixtures")
        soup = Soup(response.content, "html.parser")
        assert len(soup.find_all("a", {"href": re.compile(r"\.db$")}))
        # Check we can actually download it
        download_response = client.get("/fixtures.db")
        assert download_response.status_code == 200
        # Check the content-length header exists
        assert "content-length" in download_response.headers
        content_length = download_response.headers["content-length"]
        assert content_length.isdigit()
        assert int(content_length) > 100
        assert "content-disposition" in download_response.headers
        assert (
            download_response.headers["content-disposition"]
            == 'attachment; filename="fixtures.db"'
        )
        assert download_response.headers["transfer-encoding"] == "chunked"
        # ETag header should be present and match db.hash
        assert "etag" in download_response.headers
        etag = download_response.headers["etag"]
        assert etag == '"{}"'.format(client.ds.databases["fixtures"].hash)
        # Try a second download with If-None-Match: current-etag
        download_response2 = client.get("/fixtures.db", if_none_match=etag)
        assert download_response2.body == b""
        assert download_response2.status == 304


def test_database_download_disallowed_for_mutable(app_client):
    # Use app_client because we need a file database, not in-memory
    response = app_client.get("/fixtures")
    soup = Soup(response.content, "html.parser")
    assert len(soup.find_all("a", {"href": re.compile(r"\.db$")})) == 0
    assert app_client.get("/fixtures.db").status_code == 403


def test_database_download_disallowed_for_memory():
    with make_app_client(memory=True) as client:
        # Memory page should NOT have a download link
        response = client.get("/_memory")
        soup = Soup(response.content, "html.parser")
        assert 0 == len(soup.find_all("a", {"href": re.compile(r"\.db$")}))
        assert 404 == client.get("/_memory.db").status


def test_allow_download_off():
    with make_app_client(
        is_immutable=True, settings={"allow_download": False}
    ) as client:
        response = client.get("/fixtures")
        soup = Soup(response.content, "html.parser")
        assert not len(soup.find_all("a", {"href": re.compile(r"\.db$")}))
        # Accessing URL directly should 403
        response = client.get("/fixtures.db")
        assert 403 == response.status


def test_allow_sql_off():
    with make_app_client(config={"allow_sql": {}}) as client:
        response = client.get("/fixtures")
        soup = Soup(response.content, "html.parser")
        assert not len(soup.find_all("textarea", {"name": "sql"}))
        # The table page should no longer show "View and edit SQL"
        response = client.get("/fixtures/sortable")
        assert b"View and edit SQL" not in response.content


def query_page_data(response):
    match = re.search(
        r"window\._datasetteQueryData\s*=\s*({.*?});", response.text, re.DOTALL
    )
    return json.loads(match.group(1)) if match else None


def test_query_page_column_statistics_data(app_client):
    # The column statistics dialog wraps the query in a CTE, so duplicate
    # column names (compared case-insensitively) are made unique and the
    # storage type seen in the returned rows is included
    sql = (
        "select id, id as ID, content from simple_primary_key"
        " where content != :skip order by id"
    )
    response = app_client.get(
        "/fixtures/-/query?" + urllib.parse.urlencode({"sql": sql, "skip": "world"})
    )
    assert response.status == 200
    assert "/column-stats.js" in response.text
    assert "/query-column-actions.js" in response.text
    assert query_page_data(response) == {
        "database": "fixtures",
        "executeSql": {
            "queryUrl": "/fixtures/-/query.json",
            "editorUrl": "/fixtures/-/query",
            "query": {"sql": sql, "params": {"skip": "world"}, "shownRows": 4},
            "columns": [
                {"name": "id", "type": "integer"},
                {"name": "ID_2", "type": "integer"},
                {"name": "content", "type": "text"},
            ],
        },
    }


def test_stored_query_page_column_statistics_data(app_client):
    response = app_client.get("/fixtures/neighborhood_search?text=ber")
    assert response.status == 200
    execute_sql = query_page_data(response)["executeSql"]
    assert "where _neighborhood like '%' || :text || '%'" in (
        execute_sql["query"]["sql"]
    )
    assert execute_sql["query"]["params"] == {"text": "ber"}
    assert [column["name"] for column in execute_sql["columns"]] == [
        "_neighborhood",
        "name",
        "state",
    ]


@pytest.mark.parametrize(
    "path",
    (
        # Magic parameter values can't be passed back in
        "/fixtures/magic_parameters",
        # Not a select query
        "/fixtures/pragma_cache_size",
        # No rows, so no results table
        "/fixtures/-/query?sql=select+*+from+simple_primary_key+where+0",
    ),
)
def test_query_page_column_statistics_not_available(app_client, path):
    response = app_client.get(path)
    assert response.status == 200
    assert query_page_data(response) is None
    assert "/column-stats.js" not in response.text
    assert "/query-column-actions.js" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/404", "/fixtures/404"])
async def test_404(ds_client, path):
    response = await ds_client.get(path)
    assert response.status_code == 404
    assert (
        '<link rel="stylesheet" href="{}"'.format(ds_client.ds.static("app.css"))
        in response.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_redirect",
    [("/fixtures/", "/fixtures"), ("/fixtures/simple_view/", "/fixtures/simple_view")],
)
async def test_404_trailing_slash_redirect(ds_client, path, expected_redirect):
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert response.headers["Location"] == expected_redirect


@pytest.mark.asyncio
async def test_404_content_type(ds_client):
    response = await ds_client.get("/404")
    assert response.status_code == 404
    assert "text/html; charset=utf-8" == response.headers["content-type"]


@pytest.mark.asyncio
async def test_stored_query_default_title(ds_client):
    response = await ds_client.get("/fixtures/magic_parameters")
    assert response.status_code == 200
    soup = Soup(response.content, "html.parser")
    assert "fixtures: magic_parameters" == soup.find("h1").text


@pytest.mark.asyncio
async def test_stored_query_with_custom_metadata(ds_client):
    response = await ds_client.get("/fixtures/neighborhood_search?text=town")
    assert response.status_code == 200
    soup = Soup(response.content, "html.parser")
    assert "Search neighborhoods" == soup.find("h1").text
    assert (
        """
<div class="metadata-description">
 <b>
  Demonstrating
 </b>
 simple like search
</div>""".strip()
        == soup.find("div", {"class": "metadata-description"}).prettify().strip()
    )


@pytest.mark.asyncio
async def test_urlify_custom_queries(ds_client):
    path = "/fixtures/-/query?" + urllib.parse.urlencode(
        {"sql": "select ('https://twitter.com/' || 'simonw') as user_url;"}
    )
    response = await ds_client.get(path)
    assert response.status_code == 200
    soup = Soup(response.content, "html.parser")
    assert """<td class="col-user_url">
 <a href="https://twitter.com/simonw">
  https://twitter.com/simonw
 </a>
</td>""" == soup.find("td", {"class": "col-user_url"}).prettify().strip()


@pytest.mark.asyncio
async def test_show_hide_sql_query(ds_client):
    path = "/fixtures/-/query?" + urllib.parse.urlencode(
        {"sql": "select ('https://twitter.com/' || 'simonw') as user_url;"}
    )
    response = await ds_client.get(path)
    soup = Soup(response.content, "html.parser")
    span = soup.select(".show-hide-sql")[0]
    assert span.find("a")["href"].endswith("&_hide_sql=1")
    assert "(hide)" == span.getText()
    assert soup.find("textarea") is not None
    # Now follow the link to hide it
    response = await ds_client.get(span.find("a")["href"])
    soup = Soup(response.content, "html.parser")
    span = soup.select(".show-hide-sql")[0]
    assert not span.find("a")["href"].endswith("&_hide_sql=1")
    assert "(show)" == span.getText()
    assert soup.find("textarea") is None
    # The SQL should still be there in a hidden form field
    hiddens = soup.find("form").select("input[type=hidden]")
    assert [
        ("sql", "select ('https://twitter.com/' || 'simonw') as user_url;"),
        ("_hide_sql", "1"),
    ] == [(hidden["name"], hidden["value"]) for hidden in hiddens]


@pytest.mark.asyncio
async def test_stored_query_with_hide_has_no_hidden_sql(ds_client):
    # For a stored query the show/hide should NOT have a hidden SQL field
    # https://github.com/simonw/datasette/issues/1411
    response = await ds_client.get("/fixtures/pragma_cache_size?_hide_sql=1")
    soup = Soup(response.content, "html.parser")
    hiddens = soup.find("form").select("input[type=hidden]")
    assert [
        ("_hide_sql", "1"),
    ] == [(hidden["name"], hidden["value"]) for hidden in hiddens]


@pytest.mark.parametrize(
    "hide_sql,querystring,expected_hidden,expected_show_hide_link,expected_show_hide_text",
    (
        (False, "", None, "/_memory/one?_hide_sql=1", "hide"),
        (False, "?_hide_sql=1", "_hide_sql", "/_memory/one", "show"),
        (True, "", None, "/_memory/one?_show_sql=1", "show"),
        (True, "?_show_sql=1", "_show_sql", "/_memory/one", "hide"),
    ),
)
def test_stored_query_show_hide_metadata_option(
    hide_sql,
    querystring,
    expected_hidden,
    expected_show_hide_link,
    expected_show_hide_text,
):
    with make_app_client(
        config={
            "databases": {
                "_memory": {
                    "queries": {
                        "one": {
                            "sql": "select 1 + 1",
                            "hide_sql": hide_sql,
                        }
                    }
                }
            }
        },
        memory=True,
    ) as client:
        expected_show_hide_fragment = (
            f'(<a href="{expected_show_hide_link}">{expected_show_hide_text}</a>)'
        )
        response = client.get("/_memory/one" + querystring)
        html = response.text
        show_hide_fragment = html.split('<span class="show-hide-sql">')[1].split(
            "</span>"
        )[0]
        assert show_hide_fragment == expected_show_hide_fragment
        # The export dialog's CSV form has hidden inputs too, so only look
        # at the SQL form
        sql_form = html.split('<form class="sql core"')[1].split("</form>")[0]
        if expected_hidden:
            assert (
                f'<input type="hidden" name="{expected_hidden}" value="1">' in sql_form
            )
        else:
            assert '<input type="hidden" ' not in sql_form


@pytest.mark.asyncio
async def test_binary_data_display_in_query(ds_client):
    response = await ds_client.get("/fixtures/-/query?sql=select+*+from+binary_data")
    assert response.status_code == 200
    table = Soup(response.content, "html.parser").find("table")
    expected_tds = [
        [
            '<td class="col-data"><a class="blob-download" href="/fixtures/-/query.blob?sql=select+*+from+binary_data&amp;_blob_column=data&amp;_blob_hash=f3088978da8f9aea479ffc7f631370b968d2e855eeb172bea7f6c7a04262bb6d">&lt;Binary:\xa07\xa0bytes&gt;</a></td>'
        ],
        [
            '<td class="col-data"><a class="blob-download" href="/fixtures/-/query.blob?sql=select+*+from+binary_data&amp;_blob_column=data&amp;_blob_hash=b835b0483cedb86130b9a2c280880bf5fadc5318ddf8c18d0df5204d40df1724">&lt;Binary:\xa07\xa0bytes&gt;</a></td>'
        ],
        ['<td class="col-data">\xa0</td>'],
    ]
    assert expected_tds == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_filename",
    [
        ("/fixtures/binary_data/1.blob?_blob_column=data", "binary_data-1-data.blob"),
        (
            "/fixtures/-/query.blob?sql=select+*+from+binary_data&_blob_column=data&_blob_hash=f3088978da8f9aea479ffc7f631370b968d2e855eeb172bea7f6c7a04262bb6d",
            "data-f30889.blob",
        ),
    ],
)
async def test_blob_download(ds_client, path, expected_filename):
    response = await ds_client.get(path)
    assert response.status_code == 200
    assert response.content == b"\x15\x1c\x02\xc7\xad\x05\xfe"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="{expected_filename}"'
    )
    assert response.headers["content-type"] == "application/binary"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_message",
    [
        ("/fixtures/binary_data/1.blob", "?_blob_column= is required"),
        ("/fixtures/binary_data/1.blob?_blob_column=foo", "foo is not a valid column"),
        (
            "/fixtures/binary_data/1.blob?_blob_column=data&_blob_hash=x",
            "Link has expired - the requested binary content has changed or could not be found.",
        ),
    ],
)
async def test_blob_download_invalid_messages(ds_client, path, expected_message):
    response = await ds_client.get(path)
    assert response.status_code == 400
    assert expected_message in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/fixtures/-/query?sql=select+*+from+[123_starts_with_digits]",
        "/fixtures/123_starts_with_digits",
    ],
)
async def test_zero_results(ds_client, path):
    response = await ds_client.get(path)
    soup = Soup(response.text, "html.parser")
    assert 0 == len(soup.select("table tbody tr"))
    assert 1 == len(soup.select("p.zero-results"))


@pytest.mark.asyncio
async def test_query_error(ds_client):
    response = await ds_client.get("/fixtures/-/query?sql=select+*+from+notatable")
    html = response.text
    assert '<p class="message-error">no such table: notatable</p>' in html
    assert '<textarea id="sql-editor" name="sql" style="height: 3em' in html
    assert ">select * from notatable</textarea>" in html
    assert "0 results" not in html


def test_config_template_debug_on():
    with make_app_client(settings={"template_debug": True}) as client:
        response = client.get("/fixtures/facetable?_context=1")
        assert response.status_code == 200
        assert response.text.startswith("<pre>{")


@pytest.mark.asyncio
async def test_config_template_debug_off(ds_client):
    response = await ds_client.get("/fixtures/facetable?_context=1")
    assert response.status_code == 200
    assert not response.text.startswith("<pre>{")


def test_debug_context_includes_extra_template_vars():
    # https://github.com/simonw/datasette/issues/693
    with make_app_client(settings={"template_debug": True}) as client:
        response = client.get("/fixtures/facetable?_context=1")
        # scope_path is added by PLUGIN1
        assert "scope_path" in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/fixtures",
        "/fixtures/compound_three_primary_keys",
        "/fixtures/compound_three_primary_keys/a,a,a",
        "/fixtures/paginated_view",
        "/fixtures/facetable",
        "/fixtures/facetable?_facet=state",
        "/fixtures/-/query?sql=select+1",
        "/-/api",
        "/-/patterns",
        "/-/patterns/menus",
    ],
)
@pytest.mark.parametrize("use_prefix", (True, False))
def test_base_url_config(app_client_base_url_prefix, path, use_prefix):
    client = app_client_base_url_prefix
    path_to_get = path
    if use_prefix:
        path_to_get = "/prefix/" + path.lstrip("/")
    response = client.get(path_to_get)
    soup = Soup(response.content, "html.parser")
    for form in soup.select("form"):
        action = form.get("action")
        if action is None:
            assert form.get("method") == "dialog", json.dumps(
                {
                    "path": path,
                    "path_to_get": path_to_get,
                    "form": str(form),
                },
                indent=4,
                default=repr,
            )
            continue
        assert action.startswith("/prefix"), json.dumps(
            {
                "path": path,
                "path_to_get": path_to_get,
                "action": action,
                "form": str(form),
            },
            indent=4,
            default=repr,
        )
    for el in soup.find_all(["a", "link", "script"]):
        if "href" in el.attrs:
            href = el["href"]
        elif "src" in el.attrs:
            href = el["src"]
        else:
            continue  # Could be a <script>...</script>
        if (
            not href.startswith("#")
            and href
            not in {
                "https://datasette.io/",
                "https://github.com/simonw/datasette",
                "https://github.com/simonw/datasette/blob/main/LICENSE",
                "https://github.com/simonw/datasette/blob/main/tests/fixtures.py",
                "/login-as-root",  # Only used for the latest.datasette.io demo
            }
            and not href.startswith("https://plugin-example.datasette.io/")
        ):
            # If this has been made absolute it may start http://localhost/
            if href.startswith("http://localhost/"):
                href = href[len("http://localhost") :]
            elif href.startswith(("http://", "https://")):
                continue
            assert href.startswith("/prefix/"), json.dumps(
                {
                    "path": path,
                    "path_to_get": path_to_get,
                    "href_or_src": href,
                    "element_parent": str(el.parent),
                },
                indent=4,
                default=repr,
            )
    for el in soup.find_all("navigation-search"):
        assert el["url"] == "/prefix/-/jump", json.dumps(
            {
                "path": path,
                "path_to_get": path_to_get,
                "url": el["url"],
                "element": str(el),
            },
            indent=4,
            default=repr,
        )


def test_base_url_affects_filter_redirects(app_client_base_url_prefix):
    path = "/fixtures/binary_data?_filter_column=rowid&_filter_op=exact&_filter_value=1&_sort=rowid"
    response = app_client_base_url_prefix.get(path)
    assert response.status_code == 302
    assert (
        response.headers["location"]
        == "/prefix/fixtures/binary_data?_sort=rowid&rowid__exact=1"
    )


def test_base_url_affects_database_sql_redirect(app_client_base_url_prefix):
    response = app_client_base_url_prefix.get(
        "/prefix/fixtures?sql=select+1", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/prefix/fixtures/-/query?sql=select+1"


def test_base_url_affects_permanent_redirects():
    with make_app_client(memory=True, settings={"base_url": "/prefix/"}) as client:
        response = client.get("/prefix/-", follow_redirects=False)
        assert response.status_code == 301
        assert response.headers["location"] == "/prefix/-/"

        response2 = client.get("/prefix/:memory:", follow_redirects=False)
        assert response2.status_code == 301
        assert response2.headers["location"] == "/prefix/_memory"


def test_base_url_affects_metadata_extra_css_urls(app_client_base_url_prefix):
    html = app_client_base_url_prefix.get("/").text
    assert '<link rel="stylesheet" href="/prefix/static/extra-css-urls.css">' in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    [
        (
            "/fixtures/neighborhood_search",
            "/fixtures/-/query?sql=%0Aselect+_neighborhood%2C+facet_cities.name%2C+state%0Afrom+facetable%0A++++join+facet_cities%0A++++++++on+facetable._city_id+%3D+facet_cities.id%0Awhere+_neighborhood+like+%27%25%27+%7C%7C+%3Atext+%7C%7C+%27%25%27%0Aorder+by+_neighborhood%3B%0A&amp;text=",
        ),
        (
            "/fixtures/neighborhood_search?text=ber",
            "/fixtures/-/query?sql=%0Aselect+_neighborhood%2C+facet_cities.name%2C+state%0Afrom+facetable%0A++++join+facet_cities%0A++++++++on+facetable._city_id+%3D+facet_cities.id%0Awhere+_neighborhood+like+%27%25%27+%7C%7C+%3Atext+%7C%7C+%27%25%27%0Aorder+by+_neighborhood%3B%0A&amp;text=ber",
        ),
        ("/fixtures/pragma_cache_size", None),
        (
            # /fixtures/𝐜𝐢𝐭𝐢𝐞𝐬
            "/fixtures/~F0~9D~90~9C~F0~9D~90~A2~F0~9D~90~AD~F0~9D~90~A2~F0~9D~90~9E~F0~9D~90~AC",
            "/fixtures/-/query?sql=select+id%2C+name+from+facet_cities+order+by+id+limit+1%3B",
        ),
        ("/fixtures/magic_parameters", None),
    ],
)
async def test_edit_sql_link_on_stored_queries(ds_client, path, expected):
    response = await ds_client.get(path)
    assert response.status_code == 200
    expected_link = f'<a href="{expected}" class="stored-query-edit-sql">Edit SQL</a>'
    if expected:
        assert expected_link in response.text
    else:
        assert "Edit SQL" not in response.text


@pytest.mark.parametrize(
    "has_permission",
    [
        pytest.param(
            True,
        ),
        False,
    ],
)
def test_edit_sql_link_not_shown_if_user_lacks_permission(has_permission):
    with make_app_client(
        config={
            "allow_sql": None if has_permission else {"id": "not-you"},
            "databases": {"fixtures": {"queries": {"simple": "select 1 + 1"}}},
        }
    ) as client:
        response = client.get("/fixtures/simple")
        if has_permission:
            assert "Edit SQL" in response.text
        else:
            assert "Edit SQL" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_id,should_have_links,should_not_have_links",
    [
        (None, None, None),
        ("test", None, ["/-/permissions"]),
        ("root", None, ["/-/permissions", "/-/allow-debug"]),
    ],
)
async def test_navigation_menu_links(
    ds_client, actor_id, should_have_links, should_not_have_links
):
    # Enable root user if testing with root actor
    if actor_id == "root":
        ds_client.ds.root_enabled = True
    kwargs = {}
    if actor_id:
        kwargs["actor"] = {"id": actor_id}
    html = (await ds_client.get("/", **kwargs)).text
    soup = Soup(html, "html.parser")
    details = soup.find("nav").find("details", {"class": "nav-menu"})
    assert details is not None
    search_button = details.find("button", {"data-navigation-search-open": True})
    assert search_button is not None
    assert search_button.text.strip() == "Jump to... /"
    assert search_button.find("kbd", {"class": "keyboard-shortcut"}).text == "/"
    assert search_button.find("kbd")["aria-hidden"] == "true"
    assert (
        search_button.find("kbd")["title"]
        == "Keyboard shortcut: press / to open Jump to"
    )
    navigation_search_script = soup.find(
        "script", {"src": re.compile(r"navigation-search\.js")}
    )
    assert navigation_search_script["src"] == ds_client.ds.static(
        "navigation-search.js"
    )
    assert details.find("li").find("button") == search_button
    if not actor_id:
        # The app menu is always visible, but anonymous users do not see logout
        # or debug links.
        assert details.find("form") is None
        return
    # They are logged in: should show a menu
    assert details is not None
    # And a logout form
    assert details.find("form") is not None
    if should_have_links:
        for link in should_have_links:
            assert (
                details.find("a", {"href": link}) is not None
            ), f"{link} expected but missing from nav menu"

    if should_not_have_links:
        for link in should_not_have_links:
            assert (
                details.find("a", {"href": link}) is None
            ), f"{link} found but should not have been in nav menu"


@pytest.mark.asyncio
async def test_trace_correctly_escaped(ds_client):
    response = await ds_client.get("/fixtures/-/query?sql=select+'<h1>Hello'&_trace=1")
    assert "select '<h1>Hello" not in response.text
    assert "select &#39;&lt;h1&gt;Hello" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    (
        # Instance index page
        ("/", "http://localhost/.json"),
        # Table page
        ("/fixtures/facetable", "http://localhost/fixtures/facetable.json"),
        (
            "/fixtures/table~2Fwith~2Fslashes~2Ecsv",
            "http://localhost/fixtures/table~2Fwith~2Fslashes~2Ecsv.json",
        ),
        # Row page
        (
            "/fixtures/no_primary_key/1",
            "http://localhost/fixtures/no_primary_key/1.json",
        ),
        # Database index page
        (
            "/fixtures",
            "http://localhost/fixtures.json",
        ),
        # Custom query page
        (
            "/fixtures/-/query?sql=select+*+from+facetable",
            "http://localhost/fixtures/-/query.json?sql=select+*+from+facetable",
        ),
        # Stored query page
        (
            "/fixtures/neighborhood_search?text=town",
            "http://localhost/fixtures/neighborhood_search.json?text=town",
        ),
        # /-/ pages
        (
            "/-/plugins",
            "http://localhost/-/plugins.json",
        ),
    ),
)
async def test_alternate_url_json(ds_client, path, expected):
    response = await ds_client.get(path)
    assert response.status_code == 200
    link = response.headers["link"]
    assert link == f'<{expected}>; rel="alternate"; type="application/json+datasette"'
    assert (
        f'<link rel="alternate" type="application/json+datasette" href="{expected}">'
        in response.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/-/patterns",
        "/-/patterns/menus",
        "/-/messages",
        "/-/allow-debug",
        "/fixtures.db",
    ),
)
async def test_no_alternate_url_json(ds_client, path):
    response = await ds_client.get(path)
    assert "link" not in response.headers
    assert (
        '<link rel="alternate" type="application/json+datasette"' not in response.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    (
        (
            "/fivethirtyeight/twitter-ratio%2Fsenators",
            "/fivethirtyeight/twitter-ratio~2Fsenators",
        ),
        (
            "/fixtures/table%2Fwith%2Fslashes.csv",
            "/fixtures/table~2Fwith~2Fslashes~2Ecsv",
        ),
        # query string should be preserved
        ("/foo/bar%2Fbaz?id=5", "/foo/bar~2Fbaz?id=5"),
    ),
)
async def test_redirect_percent_encoding_to_tilde_encoding(ds_client, path, expected):
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert response.headers["location"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,config,expected_links",
    (
        ("/fixtures", {}, [("/", "home"), ("/fixtures", "fixtures")]),
        (
            "/fixtures",
            {"allow": False, "databases": {"fixtures": {"allow": True}}},
            [("/fixtures", "fixtures")],
        ),
        (
            "/fixtures/facetable",
            {"allow": False, "databases": {"fixtures": {"allow": True}}},
            [("/fixtures", "fixtures"), ("/fixtures/facetable", "facetable")],
        ),
        (
            "/fixtures/facetable/1",
            {},
            [
                ("/", "home"),
                ("/fixtures", "fixtures"),
                ("/fixtures/facetable", "facetable"),
            ],
        ),
        (
            "/fixtures/facetable/1",
            {"allow": False, "databases": {"fixtures": {"allow": True}}},
            [("/fixtures", "fixtures"), ("/fixtures/facetable", "facetable")],
        ),
    ),
)
async def test_breadcrumbs_respect_permissions(ds_client, path, config, expected_links):
    previous_config = ds_client.ds.config
    updated_config = copy.deepcopy(previous_config)
    updated_config.update(config)
    ds_client.ds.config = updated_config

    try:
        response = await ds_client.ds.client.get(path)
        soup = Soup(response.text, "html.parser")
        breadcrumbs = soup.select("p.crumbs a")
        actual = [(a["href"], a.text) for a in breadcrumbs]
        assert actual == expected_links
    finally:
        ds_client.ds.config = previous_config


@pytest.mark.asyncio
async def test_database_color(ds_client):
    expected_color = ds_client.ds.get_database("fixtures").color
    # Should be something like #9403e5
    expected_fragments = (
        f"10px solid #{expected_color}",
        f"border-color: #{expected_color}",
    )
    assert len(expected_color) == 6
    for path in (
        "/",
        "/fixtures",
        "/fixtures/facetable",
        "/fixtures/paginated_view",
        "/fixtures/pragma_cache_size",
    ):
        response = await ds_client.get(path)
        assert any(
            fragment in response.text for fragment in expected_fragments
        ), f"Color fragments not found in {path}. Expected: {expected_fragments}"


@pytest.mark.asyncio
async def test_custom_csrf_error(ds_client):
    response = await ds_client.post(
        "/-/messages",
        data={
            "message": "A message",
        },
        headers={"sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "Reason:" in response.text
    assert "cross-site" in response.text


@pytest.mark.asyncio
async def test_actions_page(ds_client):
    original_root_enabled = ds_client.ds.root_enabled
    try:
        ds_client.ds.root_enabled = True
        response = await ds_client.get("/-/actions", actor={"id": "root"})
        assert response.status_code == 200
        assert "Registered actions" in response.text
        assert "<th>Name</th>" in response.text
        assert "view-instance" in response.text
        assert "view-database" in response.text
    finally:
        ds_client.ds.root_enabled = original_root_enabled


@pytest.mark.asyncio
async def test_actions_page_does_not_display_none_string(ds_client):
    """Ensure the Resource column doesn't display the string 'None' for null values."""
    # https://github.com/simonw/datasette/issues/2599
    original_root_enabled = ds_client.ds.root_enabled
    try:
        ds_client.ds.root_enabled = True
        response = await ds_client.get("/-/actions", actor={"id": "root"})
        assert response.status_code == 200
        assert "<code>None</code>" not in response.text
    finally:
        ds_client.ds.root_enabled = original_root_enabled


@pytest.mark.asyncio
async def test_permission_debug_tabs_with_query_string(ds_client):
    """Test that navigation tabs persist query strings across Check, Allowed, and Rules pages"""
    original_root_enabled = ds_client.ds.root_enabled
    try:
        ds_client.ds.root_enabled = True
        actor = {"id": "root"}

        # Test /-/allowed with query string
        response = await ds_client.get(
            "/-/allowed?action=view-table&_size=50", actor=actor
        )
        assert response.status_code == 200
        # Check that Rules and Check tabs have the query string
        assert 'href="/-/rules?action=view-table&amp;_size=50"' in response.text
        assert 'href="/-/check?action=view-table&amp;_size=50"' in response.text
        # Playground and Actions should not have query string
        assert 'href="/-/permissions"' in response.text
        assert 'href="/-/actions"' in response.text

        # Test /-/rules with query string
        response = await ds_client.get(
            "/-/rules?action=view-database&parent=test", actor=actor
        )
        assert response.status_code == 200
        # Check that Allowed and Check tabs have the query string
        assert 'href="/-/allowed?action=view-database&amp;parent=test"' in response.text
        assert 'href="/-/check?action=view-database&amp;parent=test"' in response.text

        # Test /-/check with query string
        response = await ds_client.get("/-/check?action=execute-sql", actor=actor)
        assert response.status_code == 200
        # Check that Allowed and Rules tabs have the query string
        assert 'href="/-/allowed?action=execute-sql"' in response.text
        assert 'href="/-/rules?action=execute-sql"' in response.text
    finally:
        ds_client.ds.root_enabled = original_root_enabled
