from bs4 import BeautifulSoup as Soup
from datasette.utils import allowed_pragmas
from .fixtures import (  # noqa
    app_client,
    app_client_base_url_prefix,
    app_client_shorter_time_limit,
    app_client_two_attached_databases,
    make_app_client,
    METADATA,
)
from .utils import assert_footer_links, inner_html
import copy
import json
import pathlib
import pytest
import re
import urllib.parse


def test_homepage(app_client_two_attached_databases):
    response = app_client_two_attached_databases.get("/")
    assert response.status_code == 200
    assert "text/html; charset=utf-8" == response.headers["content-type"]
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
    assert (
        "2 rows in 1 table, 5 rows in 4 hidden tables, 1 view" == counts_p.text.strip()
    )
    # We should only show visible, not hidden tables here:
    table_links = [
        {"href": a["href"], "text": a.text.strip()} for a in links_p.findAll("a")
    ]
    assert [
        {"href": r"/extra+database/searchable", "text": "searchable"},
        {"href": r"/extra+database/searchable_view", "text": "searchable_view"},
    ] == table_links


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
    assert response.headers["cache-control"] == "max-age=3600, immutable, public"
    assert int(response.headers["content-length"]) > 100
    assert response.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_static(ds_client):
    response = await ds_client.get("/-/static/app2.css")
    assert response.status_code == 404
    response = await ds_client.get("/-/static/app.css")
    assert response.status_code == 200
    assert "text/css" == response.headers["content-type"]


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
        '<h2 id="tables">Tables</h2>',
        '<h3><a href="/fixtures/sortable">sortable</a></h3>',
        "<p><em>pk, foreign_key_with_label, foreign_key_with_blank_label, ",
    ):
        assert fragment in response.text

    # And views
    views_ul = soup.find("h2", string="Views").find_next_sibling("ul")
    assert views_ul is not None
    assert [
        ("/fixtures/paginated_view", "paginated_view"),
        ("/fixtures/searchable_view", "searchable_view"),
        (
            "/fixtures/searchable_view_configured_by_metadata",
            "searchable_view_configured_by_metadata",
        ),
        ("/fixtures/simple_view", "simple_view"),
    ] == sorted([(a["href"], a.text) for a in views_ul.find_all("a")])

    # And a list of canned queries
    queries_ul = soup.find("h2", string="Queries").find_next_sibling("ul")
    assert queries_ul is not None
    assert [
        ("/fixtures/from_async_hook", "from_async_hook"),
        ("/fixtures/from_hook", "from_hook"),
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
    response = await ds_client.get("/fixtures?sql=.schema")
    assert response.status_code == 400
    assert "Statement must be a SELECT" in response.text


@pytest.mark.asyncio
async def test_disallowed_custom_sql_pragma(ds_client):
    response = await ds_client.get(
        "/fixtures?sql=SELECT+*+FROM+pragma_not_on_allow_list('idx52')"
    )
    assert response.status_code == 400
    pragmas = ", ".join("pragma_{}()".format(pragma) for pragma in allowed_pragmas)
    assert (
        "Statement contained a disallowed PRAGMA. Allowed pragma functions are {}".format(
            pragmas
        )
        in response.text
    )


def test_sql_time_limit(app_client_shorter_time_limit):
    response = app_client_shorter_time_limit.get("/fixtures?sql=select+sleep(0.5)")
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
        table = Soup(response.content, "html.parser").find("table")
        assert table["class"] == ["rows-and-columns"]
        assert ["Mission"] == [
            td.string
            for td in table.findAll("td", {"class": "col-neighborhood-b352a7"})
        ]


def test_query_page_truncates():
    with make_app_client(settings={"truncate_cells_html": 5}) as client:
        response = client.get(
            "/fixtures?"
            + urllib.parse.urlencode(
                {
                    "sql": "select 'this is longer than 5' as a, 'https://example.com/' as b"
                }
            )
        )
        assert response.status_code == 200
        table = Soup(response.content, "html.parser").find("table")
        tds = table.findAll("td")
        assert [str(td) for td in tds] == [
            '<td class="col-a">this …</td>',
            '<td class="col-b"><a href="https://example.com/">http…</a></td>',
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_classes",
    [
        ("/", ["index"]),
        ("/fixtures", ["db", "db-fixtures"]),
        ("/fixtures?sql=select+1", ["query", "db-fixtures"]),
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
    assert '<a href="/fixtures/simple_primary_key/1.json">json</a>' in response.text


@pytest.mark.asyncio
async def test_query_json_csv_export_links(ds_client):
    response = await ds_client.get("/fixtures?sql=select+1")
    assert response.status_code == 200
    assert '<a href="/fixtures.json?sql=select+1">json</a>' in response.text
    assert '<a href="/fixtures.csv?sql=select+1&amp;_size=max">CSV</a>' in response.text


@pytest.mark.asyncio
async def test_query_parameter_form_fields(ds_client):
    response = await ds_client.get("/fixtures?sql=select+:name")
    assert response.status_code == 200
    assert (
        '<label for="qp1">name</label> <input type="text" id="qp1" name="name" value="">'
        in response.text
    )
    response2 = await ds_client.get("/fixtures?sql=select+:name&name=hello")
    assert response2.status_code == 200
    assert (
        '<label for="qp1">name</label> <input type="text" id="qp1" name="name" value="hello">'
        in response2.text
    )


@pytest.mark.asyncio
async def test_row_html_simple_primary_key(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key/1")
    assert response.status_code == 200
    table = Soup(response.content, "html.parser").find("table")
    assert ["id", "content"] == [th.string.strip() for th in table.select("thead th")]
    assert [
        [
            '<td class="col-id type-str">1</td>',
            '<td class="col-content type-str">hello</td>',
        ]
    ] == [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]


@pytest.mark.asyncio
async def test_row_html_no_primary_key(ds_client):
    response = await ds_client.get("/fixtures/no_primary_key/1")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    assert ["rowid", "content", "a", "b", "c"] == [
        th.string.strip() for th in table.select("thead th")
    ]
    expected = [
        [
            '<td class="col-rowid type-int">1</td>',
            '<td class="col-content type-str">1</td>',
            '<td class="col-a type-str">a1</td>',
            '<td class="col-b type-str">b1</td>',
            '<td class="col-c type-str">c1</td>',
        ]
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_text,expected_link",
    (
        (
            "/fixtures/facet_cities/1",
            "6 rows from _city_id in facetable",
            "/fixtures/facetable?_city_id__exact=1",
        ),
        (
            "/fixtures/attraction_characteristic/2",
            "3 rows from characteristic_id in roadside_attraction_characteristics",
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
    h2 = soup.find("h2")
    assert h2.text == "Links from other tables"
    li = h2.findNext("ul").find("li")
    text = re.sub(r"\s+", " ", li.text.strip())
    assert text == expected_text
    link = li.find("a")["href"]
    assert link == expected_link


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    (
        (
            "/fixtures/compound_primary_key/a,b",
            [
                [
                    '<td class="col-pk1 type-str">a</td>',
                    '<td class="col-pk2 type-str">b</td>',
                    '<td class="col-content type-str">c</td>',
                ]
            ],
        ),
        (
            "/fixtures/compound_primary_key/a~2Fb,~2Ec~2Dd",
            [
                [
                    '<td class="col-pk1 type-str">a/b</td>',
                    '<td class="col-pk2 type-str">.c-d</td>',
                    '<td class="col-content type-str">c</td>',
                ]
            ],
        ),
    ),
)
async def test_row_html_compound_primary_key(ds_client, path, expected):
    response = await ds_client.get(path)
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    assert ["pk1", "pk2", "content"] == [
        th.string.strip() for th in table.select("thead th")
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
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
    assert_footer_links(soup)


@pytest.mark.asyncio
async def test_database_metadata_with_custom_sql(ds_client):
    response = await ds_client.get("/fixtures?sql=select+*+from+simple_primary_key")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    # Page title should be the default
    assert "fixtures" == soup.find("h1").text
    # Description should be custom
    assert "Custom SQL query returning" in soup.find("h3").text
    # The source/license should be inherited
    assert_footer_links(soup)


def test_database_download_for_immutable():
    with make_app_client(is_immutable=True) as client:
        assert not client.ds.databases["fixtures"].is_mutable
        # Regular page should have a download link
        response = client.get("/fixtures")
        soup = Soup(response.content, "html.parser")
        assert len(soup.findAll("a", {"href": re.compile(r"\.db$")}))
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
    assert len(soup.findAll("a", {"href": re.compile(r"\.db$")})) == 0
    assert app_client.get("/fixtures.db").status_code == 403


def test_database_download_disallowed_for_memory():
    with make_app_client(memory=True) as client:
        # Memory page should NOT have a download link
        response = client.get("/_memory")
        soup = Soup(response.content, "html.parser")
        assert 0 == len(soup.findAll("a", {"href": re.compile(r"\.db$")}))
        assert 404 == client.get("/_memory.db").status


def test_allow_download_off():
    with make_app_client(
        is_immutable=True, settings={"allow_download": False}
    ) as client:
        response = client.get("/fixtures")
        soup = Soup(response.content, "html.parser")
        assert not len(soup.findAll("a", {"href": re.compile(r"\.db$")}))
        # Accessing URL directly should 403
        response = client.get("/fixtures.db")
        assert 403 == response.status


def test_allow_sql_off():
    with make_app_client(config={"allow_sql": {}}) as client:
        response = client.get("/fixtures")
        soup = Soup(response.content, "html.parser")
        assert not len(soup.findAll("textarea", {"name": "sql"}))
        # The table page should no longer show "View and edit SQL"
        response = client.get("/fixtures/sortable")
        assert b"View and edit SQL" not in response.content


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/404", "/fixtures/404"])
async def test_404(ds_client, path):
    response = await ds_client.get(path)
    assert response.status_code == 404
    assert (
        f'<link rel="stylesheet" href="/-/static/app.css?{ds_client.ds.app_css_hash()}'
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
async def test_canned_query_default_title(ds_client):
    response = await ds_client.get("/fixtures/magic_parameters")
    assert response.status_code == 200
    soup = Soup(response.content, "html.parser")
    assert "fixtures: magic_parameters" == soup.find("h1").text


@pytest.mark.asyncio
async def test_canned_query_with_custom_metadata(ds_client):
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
    path = "/fixtures?" + urllib.parse.urlencode(
        {"sql": "select ('https://twitter.com/' || 'simonw') as user_url;"}
    )
    response = await ds_client.get(path)
    assert response.status_code == 200
    soup = Soup(response.content, "html.parser")
    assert (
        """<td class="col-user_url">
 <a href="https://twitter.com/simonw">
  https://twitter.com/simonw
 </a>
</td>"""
        == soup.find("td", {"class": "col-user_url"}).prettify().strip()
    )


@pytest.mark.asyncio
async def test_show_hide_sql_query(ds_client):
    path = "/fixtures?" + urllib.parse.urlencode(
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
async def test_canned_query_with_hide_has_no_hidden_sql(ds_client):
    # For a canned query the show/hide should NOT have a hidden SQL field
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
def test_canned_query_show_hide_metadata_option(
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
        expected_show_hide_fragment = '(<a href="{}">{}</a>)'.format(
            expected_show_hide_link, expected_show_hide_text
        )
        response = client.get("/_memory/one" + querystring)
        html = response.text
        show_hide_fragment = html.split('<span class="show-hide-sql">')[1].split(
            "</span>"
        )[0]
        assert show_hide_fragment == expected_show_hide_fragment
        if expected_hidden:
            assert (
                '<input type="hidden" name="{}" value="1">'.format(expected_hidden)
                in html
            )
        else:
            assert '<input type="hidden" ' not in html


@pytest.mark.asyncio
async def test_binary_data_display_in_query(ds_client):
    response = await ds_client.get("/fixtures?sql=select+*+from+binary_data")
    assert response.status_code == 200
    table = Soup(response.content, "html.parser").find("table")
    expected_tds = [
        [
            '<td class="col-data"><a class="blob-download" href="/fixtures.blob?sql=select+*+from+binary_data&amp;_blob_column=data&amp;_blob_hash=f3088978da8f9aea479ffc7f631370b968d2e855eeb172bea7f6c7a04262bb6d">&lt;Binary:\xa07\xa0bytes&gt;</a></td>'
        ],
        [
            '<td class="col-data"><a class="blob-download" href="/fixtures.blob?sql=select+*+from+binary_data&amp;_blob_column=data&amp;_blob_hash=b835b0483cedb86130b9a2c280880bf5fadc5318ddf8c18d0df5204d40df1724">&lt;Binary:\xa07\xa0bytes&gt;</a></td>'
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
            "/fixtures.blob?sql=select+*+from+binary_data&_blob_column=data&_blob_hash=f3088978da8f9aea479ffc7f631370b968d2e855eeb172bea7f6c7a04262bb6d",
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
async def test_metadata_json_html(ds_client):
    response = await ds_client.get("/-/metadata")
    assert response.status_code == 200
    pre = Soup(response.content, "html.parser").find("pre")
    assert ds_client.ds.metadata() == json.loads(pre.text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/fixtures?sql=select+*+from+[123_starts_with_digits]",
        "/fixtures/123_starts_with_digits",
    ],
)
async def test_zero_results(ds_client, path):
    response = await ds_client.get(path)
    soup = Soup(response.text, "html.parser")
    assert 0 == len(soup.select("table"))
    assert 1 == len(soup.select("p.zero-results"))


@pytest.mark.asyncio
async def test_query_error(ds_client):
    response = await ds_client.get("/fixtures?sql=select+*+from+notatable")
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
        "/fixtures?sql=select+1",
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
        assert form["action"].startswith("/prefix")
    for el in soup.findAll(["a", "link", "script"]):
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
                href = href[len("http://localost/") :]
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


def test_base_url_affects_filter_redirects(app_client_base_url_prefix):
    path = "/fixtures/binary_data?_filter_column=rowid&_filter_op=exact&_filter_value=1&_sort=rowid"
    response = app_client_base_url_prefix.get(path)
    assert response.status_code == 302
    assert (
        response.headers["location"]
        == "/prefix/fixtures/binary_data?_sort=rowid&rowid__exact=1"
    )


def test_base_url_affects_metadata_extra_css_urls(app_client_base_url_prefix):
    html = app_client_base_url_prefix.get("/").text
    assert '<link rel="stylesheet" href="/prefix/static/extra-css-urls.css">' in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    [
        (
            "/fixtures/neighborhood_search",
            "/fixtures?sql=%0Aselect+_neighborhood%2C+facet_cities.name%2C+state%0Afrom+facetable%0A++++join+facet_cities%0A++++++++on+facetable._city_id+%3D+facet_cities.id%0Awhere+_neighborhood+like+%27%25%27+%7C%7C+%3Atext+%7C%7C+%27%25%27%0Aorder+by+_neighborhood%3B%0A&amp;text=",
        ),
        (
            "/fixtures/neighborhood_search?text=ber",
            "/fixtures?sql=%0Aselect+_neighborhood%2C+facet_cities.name%2C+state%0Afrom+facetable%0A++++join+facet_cities%0A++++++++on+facetable._city_id+%3D+facet_cities.id%0Awhere+_neighborhood+like+%27%25%27+%7C%7C+%3Atext+%7C%7C+%27%25%27%0Aorder+by+_neighborhood%3B%0A&amp;text=ber",
        ),
        ("/fixtures/pragma_cache_size", None),
        (
            # /fixtures/𝐜𝐢𝐭𝐢𝐞𝐬
            "/fixtures/~F0~9D~90~9C~F0~9D~90~A2~F0~9D~90~AD~F0~9D~90~A2~F0~9D~90~9E~F0~9D~90~AC",
            "/fixtures?sql=select+id%2C+name+from+facet_cities+order+by+id+limit+1%3B",
        ),
        ("/fixtures/magic_parameters", None),
    ],
)
async def test_edit_sql_link_on_canned_queries(ds_client, path, expected):
    response = await ds_client.get(path)
    assert response.status_code == 200
    expected_link = f'<a href="{expected}" class="canned-query-edit-sql">Edit SQL</a>'
    if expected:
        assert expected_link in response.text
    else:
        assert "Edit SQL" not in response.text


@pytest.mark.parametrize("permission_allowed", [True, False])
def test_edit_sql_link_not_shown_if_user_lacks_permission(permission_allowed):
    with make_app_client(
        config={
            "allow_sql": None if permission_allowed else {"id": "not-you"},
            "databases": {"fixtures": {"queries": {"simple": "select 1 + 1"}}},
        }
    ) as client:
        response = client.get("/fixtures/simple")
        if permission_allowed:
            assert "Edit SQL" in response.text
        else:
            assert "Edit SQL" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_id,should_have_links,should_not_have_links",
    [
        (None, None, None),
        ("test", None, ["/-/permissions"]),
        ("root", ["/-/permissions", "/-/allow-debug", "/-/metadata"], None),
    ],
)
async def test_navigation_menu_links(
    ds_client, actor_id, should_have_links, should_not_have_links
):
    cookies = {}
    if actor_id:
        cookies = {"ds_actor": ds_client.actor_cookie({"id": actor_id})}
    html = (await ds_client.get("/", cookies=cookies)).text
    soup = Soup(html, "html.parser")
    details = soup.find("nav").find("details")
    if not actor_id:
        # Should not show a menu
        assert details is None
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
    response = await ds_client.get("/fixtures?sql=select+'<h1>Hello'&_trace=1")
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
            "/fixtures?sql=select+*+from+facetable",
            "http://localhost/fixtures.json?sql=select+*+from+facetable",
        ),
        # Canned query page
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
    assert link == '{}; rel="alternate"; type="application/json+datasette"'.format(
        expected
    )
    assert (
        '<link rel="alternate" type="application/json+datasette" href="{}">'.format(
            expected
        )
        in response.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ("/-/patterns", "/-/messages", "/-/allow-debug", "/fixtures.db"),
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
        ("/fixtures", {}, [("/", "home")]),
        ("/fixtures", {"allow": False, "databases": {"fixtures": {"allow": True}}}, []),
        (
            "/fixtures/facetable",
            {"allow": False, "databases": {"fixtures": {"allow": True}}},
            [("/fixtures", "fixtures")],
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
        # TODO: what
        # (
        #    "/fixtures/facetable/1",
        #    {
        #        "allow": False,
        #        "databases": {"fixtures": {"tables": {"facetable": {"allow": True}}}},
        #    },
        #    [("/fixtures/facetable", "facetable")],
        # ),
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
        "10px solid #{}".format(expected_color),
        "border-color: #{}".format(expected_color),
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
        result = any(fragment in response.text for fragment in expected_fragments)
        if not result:
            import pdb

            pdb.set_trace()
        assert any(fragment in response.text for fragment in expected_fragments)
