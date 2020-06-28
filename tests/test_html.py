from bs4 import BeautifulSoup as Soup
from .fixtures import (  # noqa
    app_client,
    app_client_shorter_time_limit,
    app_client_two_attached_databases,
    app_client_with_hash,
    make_app_client,
    METADATA,
)
import json
import pathlib
import pytest
import re
import textwrap
import urllib.parse


def test_homepage(app_client_two_attached_databases):
    response = app_client_two_attached_databases.get("/")
    assert response.status == 200
    assert "text/html; charset=utf-8" == response.headers["content-type"]
    soup = Soup(response.body, "html.parser")
    assert "Datasette Fixtures" == soup.find("h1").text
    assert (
        "An example SQLite database demonstrating Datasette"
        == soup.select(".metadata-description")[0].text.strip()
    )
    # Should be two attached databases
    assert [
        {"href": "/fixtures", "text": "fixtures"},
        {"href": "/extra database", "text": "extra database"},
    ] == [{"href": a["href"], "text": a.text.strip()} for a in soup.select("h2 a")]
    # The first attached database should show count text and attached tables
    h2 = soup.select("h2")[1]
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
        {"href": "/extra database/searchable", "text": "searchable"},
        {"href": "/extra database/searchable_view", "text": "searchable_view"},
    ] == table_links


def test_http_head(app_client):
    response = app_client.get("/", method="HEAD")
    assert response.status == 200


def test_static(app_client):
    response = app_client.get("/-/static/app2.css")
    assert response.status == 404
    response = app_client.get("/-/static/app.css")
    assert response.status == 200
    assert "text/css" == response.headers["content-type"]


def test_static_mounts():
    with make_app_client(
        static_mounts=[("custom-static", str(pathlib.Path(__file__).parent))]
    ) as client:
        response = client.get("/custom-static/test_html.py")
        assert response.status == 200
        response = client.get("/custom-static/not_exists.py")
        assert response.status == 404
        response = client.get("/custom-static/../LICENSE")
        assert response.status == 404


def test_memory_database_page():
    with make_app_client(memory=True) as client:
        response = client.get("/:memory:")
        assert response.status == 200


def test_database_page_redirects_with_url_hash(app_client_with_hash):
    response = app_client_with_hash.get("/fixtures", allow_redirects=False)
    assert response.status == 302
    response = app_client_with_hash.get("/fixtures")
    assert "fixtures" in response.text


def test_database_page(app_client):
    response = app_client.get("/fixtures")
    soup = Soup(response.body, "html.parser")
    queries_ul = soup.find("h2", text="Queries").find_next_sibling("ul")
    assert queries_ul is not None
    assert [
        (
            "/fixtures/%F0%9D%90%9C%F0%9D%90%A2%F0%9D%90%AD%F0%9D%90%A2%F0%9D%90%9E%F0%9D%90%AC",
            "𝐜𝐢𝐭𝐢𝐞𝐬",
        ),
        ("/fixtures/from_async_hook", "from_async_hook"),
        ("/fixtures/from_hook", "from_hook"),
        ("/fixtures/magic_parameters", "magic_parameters"),
        ("/fixtures/neighborhood_search#fragment-goes-here", "Search neighborhoods"),
        ("/fixtures/pragma_cache_size", "pragma_cache_size"),
    ] == sorted(
        [(a["href"], a.text) for a in queries_ul.find_all("a")], key=lambda p: p[0]
    )


def test_invalid_custom_sql(app_client):
    response = app_client.get("/fixtures?sql=.schema")
    assert response.status == 400
    assert "Statement must be a SELECT" in response.text


def test_sql_time_limit(app_client_shorter_time_limit):
    response = app_client_shorter_time_limit.get("/fixtures?sql=select+sleep(0.5)")
    assert 400 == response.status
    expected_html_fragment = """
        <a href="https://datasette.readthedocs.io/en/stable/config.html#sql-time-limit-ms">sql_time_limit_ms</a>
    """.strip()
    assert expected_html_fragment in response.text


def test_row_redirects_with_url_hash(app_client_with_hash):
    response = app_client_with_hash.get(
        "/fixtures/simple_primary_key/1", allow_redirects=False
    )
    assert response.status == 302
    assert response.headers["Location"].endswith("/1")
    response = app_client_with_hash.get("/fixtures/simple_primary_key/1")
    assert response.status == 200


def test_row_strange_table_name_with_url_hash(app_client_with_hash):
    response = app_client_with_hash.get(
        "/fixtures/table%2Fwith%2Fslashes.csv/3", allow_redirects=False
    )
    assert response.status == 302
    assert response.headers["Location"].endswith("/table%2Fwith%2Fslashes.csv/3")
    response = app_client_with_hash.get("/fixtures/table%2Fwith%2Fslashes.csv/3")
    assert response.status == 200


@pytest.mark.parametrize(
    "path,expected_definition_sql",
    [
        (
            "/fixtures/facet_cities",
            """
CREATE TABLE facet_cities (
    id integer primary key,
    name text
);
        """.strip(),
        ),
        (
            "/fixtures/compound_three_primary_keys",
            """
CREATE TABLE compound_three_primary_keys (
  pk1 varchar(30),
  pk2 varchar(30),
  pk3 varchar(30),
  content text,
  PRIMARY KEY (pk1, pk2, pk3)
);
CREATE INDEX idx_compound_three_primary_keys_content ON compound_three_primary_keys(content);
            """.strip(),
        ),
    ],
)
def test_definition_sql(path, expected_definition_sql, app_client):
    response = app_client.get(path)
    pre = Soup(response.body, "html.parser").select_one("pre.wrapped-sql")
    assert expected_definition_sql == pre.string


def test_table_cell_truncation():
    with make_app_client(config={"truncate_cells_html": 5}) as client:
        response = client.get("/fixtures/facetable")
        assert response.status == 200
        table = Soup(response.body, "html.parser").find("table")
        assert table["class"] == ["rows-and-columns"]
        assert [
            "Missi…",
            "Dogpa…",
            "SOMA",
            "Tende…",
            "Berna…",
            "Hayes…",
            "Holly…",
            "Downt…",
            "Los F…",
            "Korea…",
            "Downt…",
            "Greek…",
            "Corkt…",
            "Mexic…",
            "Arcad…",
        ] == [td.string for td in table.findAll("td", {"class": "col-neighborhood"})]


def test_row_page_does_not_truncate():
    with make_app_client(config={"truncate_cells_html": 5}) as client:
        response = client.get("/fixtures/facetable/1")
        assert response.status == 200
        table = Soup(response.body, "html.parser").find("table")
        assert table["class"] == ["rows-and-columns"]
        assert ["Mission"] == [
            td.string for td in table.findAll("td", {"class": "col-neighborhood"})
        ]


def test_add_filter_redirects(app_client):
    filter_args = urllib.parse.urlencode(
        {"_filter_column": "content", "_filter_op": "startswith", "_filter_value": "x"}
    )
    path_base = "/fixtures/simple_primary_key"
    path = path_base + "?" + filter_args
    response = app_client.get(path, allow_redirects=False)
    assert response.status == 302
    assert response.headers["Location"].endswith("?content__startswith=x")

    # Adding a redirect to an existing querystring:
    path = path_base + "?foo=bar&" + filter_args
    response = app_client.get(path, allow_redirects=False)
    assert response.status == 302
    assert response.headers["Location"].endswith("?foo=bar&content__startswith=x")

    # Test that op with a __x suffix overrides the filter value
    path = (
        path_base
        + "?"
        + urllib.parse.urlencode(
            {
                "_filter_column": "content",
                "_filter_op": "isnull__5",
                "_filter_value": "x",
            }
        )
    )
    response = app_client.get(path, allow_redirects=False)
    assert response.status == 302
    assert response.headers["Location"].endswith("?content__isnull=5")


def test_existing_filter_redirects(app_client):
    filter_args = {
        "_filter_column_1": "name",
        "_filter_op_1": "contains",
        "_filter_value_1": "hello",
        "_filter_column_2": "age",
        "_filter_op_2": "gte",
        "_filter_value_2": "22",
        "_filter_column_3": "age",
        "_filter_op_3": "lt",
        "_filter_value_3": "30",
        "_filter_column_4": "name",
        "_filter_op_4": "contains",
        "_filter_value_4": "world",
    }
    path_base = "/fixtures/simple_primary_key"
    path = path_base + "?" + urllib.parse.urlencode(filter_args)
    response = app_client.get(path, allow_redirects=False)
    assert response.status == 302
    assert_querystring_equal(
        "name__contains=hello&age__gte=22&age__lt=30&name__contains=world",
        response.headers["Location"].split("?")[1],
    )

    # Setting _filter_column_3 to empty string should remove *_3 entirely
    filter_args["_filter_column_3"] = ""
    path = path_base + "?" + urllib.parse.urlencode(filter_args)
    response = app_client.get(path, allow_redirects=False)
    assert response.status == 302
    assert_querystring_equal(
        "name__contains=hello&age__gte=22&name__contains=world",
        response.headers["Location"].split("?")[1],
    )

    # ?_filter_op=exact should be removed if unaccompanied by _fiter_column
    response = app_client.get(path_base + "?_filter_op=exact", allow_redirects=False)
    assert response.status == 302
    assert "?" not in response.headers["Location"]


def test_empty_search_parameter_gets_removed(app_client):
    path_base = "/fixtures/simple_primary_key"
    path = (
        path_base
        + "?"
        + urllib.parse.urlencode(
            {
                "_search": "",
                "_filter_column": "name",
                "_filter_op": "exact",
                "_filter_value": "chidi",
            }
        )
    )
    response = app_client.get(path, allow_redirects=False)
    assert response.status == 302
    assert response.headers["Location"].endswith("?name__exact=chidi")


def test_searchable_view_persists_fts_table(app_client):
    # The search form should persist ?_fts_table as a hidden field
    response = app_client.get(
        "/fixtures/searchable_view?_fts_table=searchable_fts&_fts_pk=pk"
    )
    inputs = Soup(response.body, "html.parser").find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [("_fts_table", "searchable_fts"), ("_fts_pk", "pk")] == [
        (hidden["name"], hidden["value"]) for hidden in hiddens
    ]


def test_sort_by_desc_redirects(app_client):
    path_base = "/fixtures/sortable"
    path = (
        path_base
        + "?"
        + urllib.parse.urlencode({"_sort": "sortable", "_sort_by_desc": "1"})
    )
    response = app_client.get(path, allow_redirects=False)
    assert response.status == 302
    assert response.headers["Location"].endswith("?_sort_desc=sortable")


def test_sort_links(app_client):
    response = app_client.get("/fixtures/sortable?_sort=sortable")
    assert response.status == 200
    ths = Soup(response.body, "html.parser").findAll("th")
    attrs_and_link_attrs = [
        {
            "attrs": th.attrs,
            "a_href": (th.find("a")["href"].split("/")[-1] if th.find("a") else None),
        }
        for th in ths
    ]
    assert [
        {"attrs": {"class": ["col-Link"], "scope": "col"}, "a_href": None},
        {"attrs": {"class": ["col-pk1"], "scope": "col"}, "a_href": None},
        {"attrs": {"class": ["col-pk2"], "scope": "col"}, "a_href": None},
        {"attrs": {"class": ["col-content"], "scope": "col"}, "a_href": None},
        {
            "attrs": {"class": ["col-sortable"], "scope": "col"},
            "a_href": "sortable?_sort_desc=sortable",
        },
        {
            "attrs": {"class": ["col-sortable_with_nulls"], "scope": "col"},
            "a_href": "sortable?_sort=sortable_with_nulls",
        },
        {
            "attrs": {"class": ["col-sortable_with_nulls_2"], "scope": "col"},
            "a_href": "sortable?_sort=sortable_with_nulls_2",
        },
        {
            "attrs": {"class": ["col-text"], "scope": "col"},
            "a_href": "sortable?_sort=text",
        },
    ] == attrs_and_link_attrs


def test_facet_display(app_client):
    response = app_client.get(
        "/fixtures/facetable?_facet=planet_int&_facet=city_id&_facet=on_earth"
    )
    assert response.status == 200
    soup = Soup(response.body, "html.parser")
    divs = soup.find("div", {"class": "facet-results"}).findAll("div")
    actual = []
    for div in divs:
        actual.append(
            {
                "name": div.find("strong").text,
                "items": [
                    {
                        "name": a.text,
                        "qs": a["href"].split("?")[-1],
                        "count": int(str(a.parent).split("</a>")[1].split("<")[0]),
                    }
                    for a in div.find("ul").findAll("a")
                ],
            }
        )
    assert [
        {
            "name": "city_id",
            "items": [
                {
                    "name": "San Francisco",
                    "qs": "_facet=planet_int&_facet=city_id&_facet=on_earth&city_id=1",
                    "count": 6,
                },
                {
                    "name": "Los Angeles",
                    "qs": "_facet=planet_int&_facet=city_id&_facet=on_earth&city_id=2",
                    "count": 4,
                },
                {
                    "name": "Detroit",
                    "qs": "_facet=planet_int&_facet=city_id&_facet=on_earth&city_id=3",
                    "count": 4,
                },
                {
                    "name": "Memnonia",
                    "qs": "_facet=planet_int&_facet=city_id&_facet=on_earth&city_id=4",
                    "count": 1,
                },
            ],
        },
        {
            "name": "planet_int",
            "items": [
                {
                    "name": "1",
                    "qs": "_facet=planet_int&_facet=city_id&_facet=on_earth&planet_int=1",
                    "count": 14,
                },
                {
                    "name": "2",
                    "qs": "_facet=planet_int&_facet=city_id&_facet=on_earth&planet_int=2",
                    "count": 1,
                },
            ],
        },
        {
            "name": "on_earth",
            "items": [
                {
                    "name": "1",
                    "qs": "_facet=planet_int&_facet=city_id&_facet=on_earth&on_earth=1",
                    "count": 14,
                },
                {
                    "name": "0",
                    "qs": "_facet=planet_int&_facet=city_id&_facet=on_earth&on_earth=0",
                    "count": 1,
                },
            ],
        },
    ] == actual


def test_facets_persist_through_filter_form(app_client):
    response = app_client.get("/fixtures/facetable?_facet=planet_int&_facet=city_id")
    assert response.status == 200
    inputs = Soup(response.body, "html.parser").find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [("_facet", "city_id"), ("_facet", "planet_int")] == [
        (hidden["name"], hidden["value"]) for hidden in hiddens
    ]


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
            "/fixtures/table%2Fwith%2Fslashes.csv",
            ["table", "db-fixtures", "table-tablewithslashescsv-fa7563"],
        ),
        (
            "/fixtures/simple_primary_key/1",
            ["row", "db-fixtures", "table-simple_primary_key"],
        ),
    ],
)
def test_css_classes_on_body(app_client, path, expected_classes):
    response = app_client.get(path)
    assert response.status == 200
    classes = re.search(r'<body class="(.*)">', response.text).group(1).split()
    assert classes == expected_classes


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
            "/fixtures/table%2Fwith%2Fslashes.csv",
            "table-fixtures-tablewithslashescsv-fa7563.html, *table.html",
        ),
        (
            "/fixtures/simple_primary_key/1",
            "row-fixtures-simple_primary_key.html, *row.html",
        ),
    ],
)
def test_templates_considered(app_client, path, expected_considered):
    response = app_client.get(path)
    assert response.status == 200
    assert (
        "<!-- Templates considered: {} -->".format(expected_considered) in response.text
    )


def test_table_html_simple_primary_key(app_client):
    response = app_client.get("/fixtures/simple_primary_key?_size=3")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    assert table["class"] == ["rows-and-columns"]
    ths = table.findAll("th")
    assert "id\xa0▼" == ths[0].find("a").string.strip()
    for expected_col, th in zip(("content",), ths[1:]):
        a = th.find("a")
        assert expected_col == a.string
        assert a["href"].endswith(
            "/simple_primary_key?_size=3&_sort={}".format(expected_col)
        )
        assert ["nofollow"] == a["rel"]
    assert [
        [
            '<td class="col-id type-pk"><a href="/fixtures/simple_primary_key/1">1</a></td>',
            '<td class="col-content type-str">hello</td>',
        ],
        [
            '<td class="col-id type-pk"><a href="/fixtures/simple_primary_key/2">2</a></td>',
            '<td class="col-content type-str">world</td>',
        ],
        [
            '<td class="col-id type-pk"><a href="/fixtures/simple_primary_key/3">3</a></td>',
            '<td class="col-content type-str">\xa0</td>',
        ],
    ] == [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]


def test_table_csv_json_export_interface(app_client):
    response = app_client.get("/fixtures/simple_primary_key?id__gt=2")
    assert response.status == 200
    # The links at the top of the page
    links = (
        Soup(response.body, "html.parser")
        .find("p", {"class": "export-links"})
        .findAll("a")
    )
    actual = [l["href"].split("/")[-1] for l in links]
    expected = [
        "simple_primary_key.json?id__gt=2",
        "simple_primary_key.testall?id__gt=2",
        "simple_primary_key.testnone?id__gt=2",
        "simple_primary_key.csv?id__gt=2&_size=max",
        "#export",
    ]
    assert expected == actual
    # And the advaced export box at the bottom:
    div = Soup(response.body, "html.parser").find("div", {"class": "advanced-export"})
    json_links = [a["href"].split("/")[-1] for a in div.find("p").findAll("a")]
    assert [
        "simple_primary_key.json?id__gt=2",
        "simple_primary_key.json?id__gt=2&_shape=array",
        "simple_primary_key.json?id__gt=2&_shape=array&_nl=on",
        "simple_primary_key.json?id__gt=2&_shape=object",
    ] == json_links
    # And the CSV form
    form = div.find("form")
    assert form["action"].endswith("/simple_primary_key.csv")
    inputs = [str(input) for input in form.findAll("input")]
    assert [
        '<input name="_dl" type="checkbox"/>',
        '<input type="submit" value="Export CSV"/>',
        '<input name="id__gt" type="hidden" value="2"/>',
        '<input name="_size" type="hidden" value="max"/>',
    ] == inputs


def test_csv_json_export_links_include_labels_if_foreign_keys(app_client):
    response = app_client.get("/fixtures/facetable")
    assert response.status == 200
    links = (
        Soup(response.body, "html.parser")
        .find("p", {"class": "export-links"})
        .findAll("a")
    )
    actual = [l["href"].split("/")[-1] for l in links]
    expected = [
        "facetable.json?_labels=on",
        "facetable.testall?_labels=on",
        "facetable.testnone?_labels=on",
        "facetable.csv?_labels=on&_size=max",
        "#export",
    ]
    assert expected == actual


def test_row_html_simple_primary_key(app_client):
    response = app_client.get("/fixtures/simple_primary_key/1")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    assert ["id", "content"] == [th.string.strip() for th in table.select("thead th")]
    assert [
        [
            '<td class="col-id type-str">1</td>',
            '<td class="col-content type-str">hello</td>',
        ]
    ] == [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]


def test_table_not_exists(app_client):
    assert "Table not found: blah" in app_client.get("/fixtures/blah").text


def test_table_html_no_primary_key(app_client):
    response = app_client.get("/fixtures/no_primary_key")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    # We have disabled sorting for this table using metadata.json
    assert ["content", "a", "b", "c"] == [
        th.string.strip() for th in table.select("thead th")[2:]
    ]
    expected = [
        [
            '<td class="col-Link type-pk"><a href="/fixtures/no_primary_key/{}">{}</a></td>'.format(
                i, i
            ),
            '<td class="col-rowid type-int">{}</td>'.format(i),
            '<td class="col-content type-str">{}</td>'.format(i),
            '<td class="col-a type-str">a{}</td>'.format(i),
            '<td class="col-b type-str">b{}</td>'.format(i),
            '<td class="col-c type-str">c{}</td>'.format(i),
        ]
        for i in range(1, 51)
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


def test_rowid_sortable_no_primary_key(app_client):
    response = app_client.get("/fixtures/no_primary_key")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    assert table["class"] == ["rows-and-columns"]
    ths = table.findAll("th")
    assert "rowid\xa0▼" == ths[1].find("a").string.strip()


def test_row_html_no_primary_key(app_client):
    response = app_client.get("/fixtures/no_primary_key/1")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
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


def test_table_html_compound_primary_key(app_client):
    response = app_client.get("/fixtures/compound_primary_key")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    ths = table.findAll("th")
    assert "Link" == ths[0].string.strip()
    for expected_col, th in zip(("pk1", "pk2", "content"), ths[1:]):
        a = th.find("a")
        assert expected_col == a.string
        assert th["class"] == ["col-{}".format(expected_col)]
        assert a["href"].endswith("/compound_primary_key?_sort={}".format(expected_col))
    expected = [
        [
            '<td class="col-Link type-pk"><a href="/fixtures/compound_primary_key/a,b">a,b</a></td>',
            '<td class="col-pk1 type-str">a</td>',
            '<td class="col-pk2 type-str">b</td>',
            '<td class="col-content type-str">c</td>',
        ]
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


def test_table_html_foreign_key_links(app_client):
    response = app_client.get("/fixtures/foreign_key_references")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    expected = [
        [
            '<td class="col-pk type-pk"><a href="/fixtures/foreign_key_references/1">1</a></td>',
            '<td class="col-foreign_key_with_label type-str"><a href="/fixtures/simple_primary_key/1">hello</a>\xa0<em>1</em></td>',
            '<td class="col-foreign_key_with_no_label type-str"><a href="/fixtures/primary_key_multiple_columns/1">1</a></td>',
        ],
        [
            '<td class="col-pk type-pk"><a href="/fixtures/foreign_key_references/2">2</a></td>',
            '<td class="col-foreign_key_with_label type-none">\xa0</td>',
            '<td class="col-foreign_key_with_no_label type-none">\xa0</td>',
        ],
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


def test_table_html_disable_foreign_key_links_with_labels(app_client):
    response = app_client.get("/fixtures/foreign_key_references?_labels=off&_size=1")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    expected = [
        [
            '<td class="col-pk type-pk"><a href="/fixtures/foreign_key_references/1">1</a></td>',
            '<td class="col-foreign_key_with_label type-str">1</td>',
            '<td class="col-foreign_key_with_no_label type-str">1</td>',
        ]
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


def test_table_html_foreign_key_custom_label_column(app_client):
    response = app_client.get("/fixtures/custom_foreign_key_label")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    expected = [
        [
            '<td class="col-pk type-pk"><a href="/fixtures/custom_foreign_key_label/1">1</a></td>',
            '<td class="col-foreign_key_with_custom_label type-str"><a href="/fixtures/primary_key_multiple_columns_explicit_label/1">world2</a>\xa0<em>1</em></td>',
        ]
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


@pytest.mark.parametrize(
    "path,expected_column_options",
    [
        ("/fixtures/infinity", ["- column -", "rowid", "value"]),
        (
            "/fixtures/primary_key_multiple_columns",
            ["- column -", "id", "content", "content2"],
        ),
        ("/fixtures/compound_primary_key", ["- column -", "pk1", "pk2", "content"]),
    ],
)
def test_table_html_filter_form_column_options(
    path, expected_column_options, app_client
):
    response = app_client.get(path)
    assert response.status == 200
    form = Soup(response.body, "html.parser").find("form")
    column_options = [
        o.attrs.get("value") or o.string
        for o in form.select("select[name=_filter_column] option")
    ]
    assert expected_column_options == column_options


def test_row_html_compound_primary_key(app_client):
    response = app_client.get("/fixtures/compound_primary_key/a,b")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    assert ["pk1", "pk2", "content"] == [
        th.string.strip() for th in table.select("thead th")
    ]
    expected = [
        [
            '<td class="col-pk1 type-str">a</td>',
            '<td class="col-pk2 type-str">b</td>',
            '<td class="col-content type-str">c</td>',
        ]
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


def test_compound_primary_key_with_foreign_key_references(app_client):
    # e.g. a many-to-many table with a compound primary key on the two columns
    response = app_client.get("/fixtures/searchable_tags")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    expected = [
        [
            '<td class="col-Link type-pk"><a href="/fixtures/searchable_tags/1,feline">1,feline</a></td>',
            '<td class="col-searchable_id type-int"><a href="/fixtures/searchable/1">1</a>\xa0<em>1</em></td>',
            '<td class="col-tag type-str"><a href="/fixtures/tags/feline">feline</a></td>',
        ],
        [
            '<td class="col-Link type-pk"><a href="/fixtures/searchable_tags/2,canine">2,canine</a></td>',
            '<td class="col-searchable_id type-int"><a href="/fixtures/searchable/2">2</a>\xa0<em>2</em></td>',
            '<td class="col-tag type-str"><a href="/fixtures/tags/canine">canine</a></td>',
        ],
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


def test_view_html(app_client):
    response = app_client.get("/fixtures/simple_view?_size=3")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    ths = table.select("thead th")
    assert 2 == len(ths)
    assert ths[0].find("a") is not None
    assert ths[0].find("a")["href"].endswith("/simple_view?_size=3&_sort=content")
    assert ths[0].find("a").string.strip() == "content"
    assert ths[1].find("a") is None
    assert ths[1].string.strip() == "upper_content"
    expected = [
        [
            '<td class="col-content type-str">hello</td>',
            '<td class="col-upper_content type-str">HELLO</td>',
        ],
        [
            '<td class="col-content type-str">world</td>',
            '<td class="col-upper_content type-str">WORLD</td>',
        ],
        [
            '<td class="col-content type-str">\xa0</td>',
            '<td class="col-upper_content type-str">\xa0</td>',
        ],
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


def test_index_metadata(app_client):
    response = app_client.get("/")
    assert response.status == 200
    soup = Soup(response.body, "html.parser")
    assert "Datasette Fixtures" == soup.find("h1").text
    assert "An example SQLite database demonstrating Datasette" == inner_html(
        soup.find("div", {"class": "metadata-description"})
    )
    assert_footer_links(soup)


def test_database_metadata(app_client):
    response = app_client.get("/fixtures")
    assert response.status == 200
    soup = Soup(response.body, "html.parser")
    # Page title should be the default
    assert "fixtures" == soup.find("h1").text
    # Description should be custom
    assert "Test tables description" == inner_html(
        soup.find("div", {"class": "metadata-description"})
    )
    # The source/license should be inherited
    assert_footer_links(soup)


def test_database_metadata_with_custom_sql(app_client):
    response = app_client.get("/fixtures?sql=select+*+from+simple_primary_key")
    assert response.status == 200
    soup = Soup(response.body, "html.parser")
    # Page title should be the default
    assert "fixtures" == soup.find("h1").text
    # Description should be custom
    assert "Custom SQL query returning" in soup.find("h3").text
    # The source/license should be inherited
    assert_footer_links(soup)


def test_table_metadata(app_client):
    response = app_client.get("/fixtures/simple_primary_key")
    assert response.status == 200
    soup = Soup(response.body, "html.parser")
    # Page title should be custom and should be HTML escaped
    assert "This &lt;em&gt;HTML&lt;/em&gt; is escaped" == inner_html(soup.find("h1"))
    # Description should be custom and NOT escaped (we used description_html)
    assert "Simple <em>primary</em> key" == inner_html(
        soup.find("div", {"class": "metadata-description"})
    )
    # The source/license should be inherited
    assert_footer_links(soup)


def test_database_download_allowed_for_immutable():
    with make_app_client(is_immutable=True) as client:
        assert not client.ds.databases["fixtures"].is_mutable
        # Regular page should have a download link
        response = client.get("/fixtures")
        soup = Soup(response.body, "html.parser")
        assert len(soup.findAll("a", {"href": re.compile(r"\.db$")}))
        # Check we can actually download it
        assert 200 == client.get("/fixtures.db").status


def test_database_download_disallowed_for_mutable(app_client):
    response = app_client.get("/fixtures")
    soup = Soup(response.body, "html.parser")
    assert 0 == len(soup.findAll("a", {"href": re.compile(r"\.db$")}))
    assert 403 == app_client.get("/fixtures.db").status


def test_database_download_disallowed_for_memory():
    with make_app_client(memory=True) as client:
        # Memory page should NOT have a download link
        response = client.get("/:memory:")
        soup = Soup(response.body, "html.parser")
        assert 0 == len(soup.findAll("a", {"href": re.compile(r"\.db$")}))
        assert 404 == client.get("/:memory:.db").status


def test_allow_download_off():
    with make_app_client(is_immutable=True, config={"allow_download": False}) as client:
        response = client.get("/fixtures")
        soup = Soup(response.body, "html.parser")
        assert not len(soup.findAll("a", {"href": re.compile(r"\.db$")}))
        # Accessing URL directly should 403
        response = client.get("/fixtures.db")
        assert 403 == response.status


def test_allow_sql_off():
    with make_app_client(metadata={"allow_sql": {}}) as client:
        response = client.get("/fixtures")
        soup = Soup(response.body, "html.parser")
        assert not len(soup.findAll("textarea", {"name": "sql"}))
        # The table page should no longer show "View and edit SQL"
        response = client.get("/fixtures/sortable")
        assert b"View and edit SQL" not in response.body


def assert_querystring_equal(expected, actual):
    assert sorted(expected.split("&")) == sorted(actual.split("&"))


def assert_footer_links(soup):
    footer_links = soup.find("div", {"class": "ft"}).findAll("a")
    assert 4 == len(footer_links)
    datasette_link, license_link, source_link, about_link = footer_links
    assert "Datasette" == datasette_link.text.strip()
    assert "tests/fixtures.py" == source_link.text.strip()
    assert "Apache License 2.0" == license_link.text.strip()
    assert "About Datasette" == about_link.text.strip()
    assert "https://github.com/simonw/datasette" == datasette_link["href"]
    assert (
        "https://github.com/simonw/datasette/blob/master/tests/fixtures.py"
        == source_link["href"]
    )
    assert (
        "https://github.com/simonw/datasette/blob/master/LICENSE"
        == license_link["href"]
    )
    assert "https://github.com/simonw/datasette" == about_link["href"]


def inner_html(soup):
    html = str(soup)
    # This includes the parent tag - so remove that
    inner_html = html.split(">", 1)[1].rsplit("<", 1)[0]
    return inner_html.strip()


@pytest.mark.parametrize("path", ["/404", "/fixtures/404"])
def test_404(app_client, path):
    response = app_client.get(path)
    assert 404 == response.status
    assert (
        '<link rel="stylesheet" href="/-/static/app.css?{}'.format(
            app_client.ds.app_css_hash()
        )
        in response.text
    )


@pytest.mark.parametrize(
    "path,expected_redirect",
    [("/fixtures/", "/fixtures"), ("/fixtures/simple_view/", "/fixtures/simple_view")],
)
def test_404_trailing_slash_redirect(app_client, path, expected_redirect):
    response = app_client.get(path, allow_redirects=False)
    assert 302 == response.status
    assert expected_redirect == response.headers["Location"]


def test_404_content_type(app_client):
    response = app_client.get("/404")
    assert 404 == response.status
    assert "text/html; charset=utf-8" == response.headers["content-type"]


def test_canned_query_with_custom_metadata(app_client):
    response = app_client.get("/fixtures/neighborhood_search?text=town")
    assert response.status == 200
    soup = Soup(response.body, "html.parser")
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


@pytest.mark.parametrize(
    "path,has_object,has_stream,has_expand",
    [
        ("/fixtures/no_primary_key", False, True, False),
        ("/fixtures/complex_foreign_keys", True, False, True),
    ],
)
def test_advanced_export_box(app_client, path, has_object, has_stream, has_expand):
    response = app_client.get(path)
    assert response.status == 200
    soup = Soup(response.body, "html.parser")
    # JSON shape options
    expected_json_shapes = ["default", "array", "newline-delimited"]
    if has_object:
        expected_json_shapes.append("object")
    div = soup.find("div", {"class": "advanced-export"})
    assert expected_json_shapes == [a.text for a in div.find("p").findAll("a")]
    # "stream all rows" option
    if has_stream:
        assert "stream all rows" in str(div)
    # "expand labels" option
    if has_expand:
        assert "expand labels" in str(div)


def test_urlify_custom_queries(app_client):
    path = "/fixtures?" + urllib.parse.urlencode(
        {"sql": "select ('https://twitter.com/' || 'simonw') as user_url;"}
    )
    response = app_client.get(path)
    assert response.status == 200
    soup = Soup(response.body, "html.parser")
    assert (
        """<td class="col-user_url">
 <a href="https://twitter.com/simonw">
  https://twitter.com/simonw
 </a>
</td>"""
        == soup.find("td", {"class": "col-user_url"}).prettify().strip()
    )


def test_show_hide_sql_query(app_client):
    path = "/fixtures?" + urllib.parse.urlencode(
        {"sql": "select ('https://twitter.com/' || 'simonw') as user_url;"}
    )
    response = app_client.get(path)
    soup = Soup(response.body, "html.parser")
    span = soup.select(".show-hide-sql")[0]
    assert span.find("a")["href"].endswith("&_hide_sql=1")
    assert "(hide)" == span.getText()
    assert soup.find("textarea") is not None
    # Now follow the link to hide it
    response = app_client.get(span.find("a")["href"])
    soup = Soup(response.body, "html.parser")
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


def test_extra_where_clauses(app_client):
    response = app_client.get(
        "/fixtures/facetable?_where=neighborhood='Dogpatch'&_where=city_id=1"
    )
    soup = Soup(response.body, "html.parser")
    div = soup.select(".extra-wheres")[0]
    assert "2 extra where clauses" == div.find("h3").text
    hrefs = [a["href"] for a in div.findAll("a")]
    assert [
        "/fixtures/facetable?_where=city_id%3D1",
        "/fixtures/facetable?_where=neighborhood%3D%27Dogpatch%27",
    ] == hrefs
    # These should also be persisted as hidden fields
    inputs = soup.find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [("_where", "neighborhood='Dogpatch'"), ("_where", "city_id=1")] == [
        (hidden["name"], hidden["value"]) for hidden in hiddens
    ]


def test_binary_data_display(app_client):
    response = app_client.get("/fixtures/binary_data")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    expected_tds = [
        [
            '<td class="col-Link type-pk"><a href="/fixtures/binary_data/1">1</a></td>',
            '<td class="col-rowid type-int">1</td>',
            '<td class="col-data type-bytes">&lt;Binary\xa0data:\xa019\xa0bytes&gt;</td>',
        ]
    ]
    assert expected_tds == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


def test_metadata_json_html(app_client):
    response = app_client.get("/-/metadata")
    assert response.status == 200
    pre = Soup(response.body, "html.parser").find("pre")
    assert METADATA == json.loads(pre.text)


def test_custom_table_include():
    with make_app_client(
        template_dir=str(pathlib.Path(__file__).parent / "test_templates")
    ) as client:
        response = client.get("/fixtures/complex_foreign_keys")
        assert response.status == 200
        assert (
            '<div class="custom-table-row">'
            '1 - 2 - <a href="/fixtures/simple_primary_key/1">hello</a> <em>1</em>'
            "</div>"
        ) == str(Soup(response.text, "html.parser").select_one("div.custom-table-row"))


@pytest.mark.parametrize(
    "path",
    [
        "/fixtures?sql=select+*+from+[123_starts_with_digits]",
        "/fixtures/123_starts_with_digits",
    ],
)
def test_zero_results(app_client, path):
    response = app_client.get(path)
    soup = Soup(response.text, "html.parser")
    assert 0 == len(soup.select("table"))
    assert 1 == len(soup.select("p.zero-results"))


def test_config_template_debug_on():
    with make_app_client(config={"template_debug": True}) as client:
        response = client.get("/fixtures/facetable?_context=1")
        assert response.status == 200
        assert response.text.startswith("<pre>{")


def test_config_template_debug_off(app_client):
    response = app_client.get("/fixtures/facetable?_context=1")
    assert response.status == 200
    assert not response.text.startswith("<pre>{")


def test_debug_context_includes_extra_template_vars():
    # https://github.com/simonw/datasette/issues/693
    with make_app_client(config={"template_debug": True}) as client:
        response = client.get("/fixtures/facetable?_context=1")
        # scope_path is added by PLUGIN1
        assert "scope_path" in response.text


def test_metadata_sort(app_client):
    response = app_client.get("/fixtures/facet_cities")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    assert table["class"] == ["rows-and-columns"]
    ths = table.findAll("th")
    assert ["id", "name\xa0▼"] == [th.find("a").string.strip() for th in ths]
    rows = [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]
    expected = [
        [
            '<td class="col-id type-pk"><a href="/fixtures/facet_cities/3">3</a></td>',
            '<td class="col-name type-str">Detroit</td>',
        ],
        [
            '<td class="col-id type-pk"><a href="/fixtures/facet_cities/2">2</a></td>',
            '<td class="col-name type-str">Los Angeles</td>',
        ],
        [
            '<td class="col-id type-pk"><a href="/fixtures/facet_cities/4">4</a></td>',
            '<td class="col-name type-str">Memnonia</td>',
        ],
        [
            '<td class="col-id type-pk"><a href="/fixtures/facet_cities/1">1</a></td>',
            '<td class="col-name type-str">San Francisco</td>',
        ],
    ]
    assert expected == rows
    # Make sure you can reverse that sort order
    response = app_client.get("/fixtures/facet_cities?_sort_desc=name")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    rows = [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]
    assert list(reversed(expected)) == rows


def test_metadata_sort_desc(app_client):
    response = app_client.get("/fixtures/attraction_characteristic")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    assert table["class"] == ["rows-and-columns"]
    ths = table.findAll("th")
    assert ["pk\xa0▲", "name"] == [th.find("a").string.strip() for th in ths]
    rows = [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]
    expected = [
        [
            '<td class="col-pk type-pk"><a href="/fixtures/attraction_characteristic/2">2</a></td>',
            '<td class="col-name type-str">Paranormal</td>',
        ],
        [
            '<td class="col-pk type-pk"><a href="/fixtures/attraction_characteristic/1">1</a></td>',
            '<td class="col-name type-str">Museum</td>',
        ],
    ]
    assert expected == rows
    # Make sure you can reverse that sort order
    response = app_client.get("/fixtures/attraction_characteristic?_sort=pk")
    assert response.status == 200
    table = Soup(response.body, "html.parser").find("table")
    rows = [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]
    assert list(reversed(expected)) == rows


@pytest.mark.parametrize("base_url", ["/prefix/", "https://example.com/"])
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/fixtures",
        "/fixtures/compound_three_primary_keys",
        "/fixtures/compound_three_primary_keys/a,a,a",
        "/fixtures/paginated_view",
        "/fixtures/facetable",
    ],
)
def test_base_url_config(base_url, path):
    with make_app_client(config={"base_url": base_url}) as client:
        response = client.get(base_url + path.lstrip("/"))
        soup = Soup(response.body, "html.parser")
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
                    "https://github.com/simonw/datasette",
                    "https://github.com/simonw/datasette/blob/master/LICENSE",
                    "https://github.com/simonw/datasette/blob/master/tests/fixtures.py",
                }
                and not href.startswith("https://plugin-example.com/")
            ):
                # If this has been made absolute it may start http://localhost/
                if href.startswith("http://localhost/"):
                    href = href[len("http://localost/") :]
                assert href.startswith(base_url), {
                    "base_url": base_url,
                    "path": path,
                    "href_or_src": href,
                    "element_parent": str(el.parent),
                }
