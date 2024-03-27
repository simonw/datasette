from datasette.app import Datasette
from bs4 import BeautifulSoup as Soup
from .fixtures import (  # noqa
    app_client,
    make_app_client,
)
import pathlib
import pytest
import urllib.parse
from .utils import assert_footer_links, inner_html


@pytest.mark.asyncio
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
async def test_table_definition_sql(path, expected_definition_sql, ds_client):
    response = await ds_client.get(path)
    pre = Soup(response.text, "html.parser").select_one("pre.wrapped-sql")
    assert expected_definition_sql == pre.string


def test_table_cell_truncation():
    with make_app_client(settings={"truncate_cells_html": 5}) as client:
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
        ] == [
            td.string
            for td in table.findAll("td", {"class": "col-neighborhood-b352a7"})
        ]
        # URLs should be truncated too
        response2 = client.get("/fixtures/roadside_attractions")
        assert response2.status == 200
        table = Soup(response2.body, "html.parser").find("table")
        tds = table.findAll("td", {"class": "col-url"})
        assert [str(td) for td in tds] == [
            '<td class="col-url type-str"><a href="https://www.mysteryspot.com/">http…</a></td>',
            '<td class="col-url type-str"><a href="https://winchestermysteryhouse.com/">http…</a></td>',
            '<td class="col-url type-none">\xa0</td>',
            '<td class="col-url type-str"><a href="https://www.bigfootdiscoveryproject.com/">http…</a></td>',
        ]


@pytest.mark.asyncio
async def test_add_filter_redirects(ds_client):
    filter_args = urllib.parse.urlencode(
        {"_filter_column": "content", "_filter_op": "startswith", "_filter_value": "x"}
    )
    path_base = "/fixtures/simple_primary_key"
    path = path_base + "?" + filter_args
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("?content__startswith=x")

    # Adding a redirect to an existing query string:
    path = path_base + "?foo=bar&" + filter_args
    response = await ds_client.get(path)
    assert response.status_code == 302
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
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("?content__isnull=5")


@pytest.mark.asyncio
async def test_existing_filter_redirects(ds_client):
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
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert_querystring_equal(
        "name__contains=hello&age__gte=22&age__lt=30&name__contains=world",
        response.headers["Location"].split("?")[1],
    )

    # Setting _filter_column_3 to empty string should remove *_3 entirely
    filter_args["_filter_column_3"] = ""
    path = path_base + "?" + urllib.parse.urlencode(filter_args)
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert_querystring_equal(
        "name__contains=hello&age__gte=22&name__contains=world",
        response.headers["Location"].split("?")[1],
    )

    # ?_filter_op=exact should be removed if unaccompanied by _fiter_column
    response = await ds_client.get(path_base + "?_filter_op=exact")
    assert response.status_code == 302
    assert "?" not in response.headers["Location"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qs,expected_hidden",
    (
        # Things that should be reflected in hidden form fields:
        ("_facet=_neighborhood", {"_facet": "_neighborhood"}),
        ("_where=1+=+1&_col=_city_id", {"_where": "1 = 1", "_col": "_city_id"}),
        # Things that should NOT be reflected in hidden form fields:
        (
            "_facet=_neighborhood&_neighborhood__exact=Downtown",
            {"_facet": "_neighborhood"},
        ),
        ("_facet=_neighborhood&_city_id__gt=1", {"_facet": "_neighborhood"}),
    ),
)
async def test_reflected_hidden_form_fields(ds_client, qs, expected_hidden):
    # https://github.com/simonw/datasette/issues/1527
    response = await ds_client.get("/fixtures/facetable?{}".format(qs))
    # In this case we should NOT have a hidden _neighborhood__exact=Downtown field
    form = Soup(response.text, "html.parser").find("form")
    hidden_inputs = {
        input["name"]: input["value"] for input in form.select("input[type=hidden]")
    }
    assert hidden_inputs == expected_hidden


@pytest.mark.asyncio
async def test_empty_search_parameter_gets_removed(ds_client):
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
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("?name__exact=chidi")


@pytest.mark.asyncio
async def test_searchable_view_persists_fts_table(ds_client):
    # The search form should persist ?_fts_table as a hidden field
    response = await ds_client.get(
        "/fixtures/searchable_view?_fts_table=searchable_fts&_fts_pk=pk"
    )
    inputs = Soup(response.text, "html.parser").find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [("_fts_table", "searchable_fts"), ("_fts_pk", "pk")] == [
        (hidden["name"], hidden["value"]) for hidden in hiddens
    ]


@pytest.mark.asyncio
async def test_sort_by_desc_redirects(ds_client):
    path_base = "/fixtures/sortable"
    path = (
        path_base
        + "?"
        + urllib.parse.urlencode({"_sort": "sortable", "_sort_by_desc": "1"})
    )
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("?_sort_desc=sortable")


@pytest.mark.asyncio
async def test_sort_links(ds_client):
    response = await ds_client.get("/fixtures/sortable?_sort=sortable")
    assert response.status_code == 200
    ths = Soup(response.text, "html.parser").findAll("th")
    attrs_and_link_attrs = [
        {
            "attrs": th.attrs,
            "a_href": (th.find("a")["href"] if th.find("a") else None),
        }
        for th in ths
    ]
    assert attrs_and_link_attrs == [
        {
            "attrs": {
                "class": ["col-Link"],
                "scope": "col",
                "data-column": "Link",
                "data-column-type": "",
                "data-column-not-null": "0",
                "data-is-pk": "0",
            },
            "a_href": None,
        },
        {
            "attrs": {
                "class": ["col-pk1"],
                "scope": "col",
                "data-column": "pk1",
                "data-column-type": "varchar(30)",
                "data-column-not-null": "0",
                "data-is-pk": "1",
            },
            "a_href": None,
        },
        {
            "attrs": {
                "class": ["col-pk2"],
                "scope": "col",
                "data-column": "pk2",
                "data-column-type": "varchar(30)",
                "data-column-not-null": "0",
                "data-is-pk": "1",
            },
            "a_href": None,
        },
        {
            "attrs": {
                "class": ["col-content"],
                "scope": "col",
                "data-column": "content",
                "data-column-type": "text",
                "data-column-not-null": "0",
                "data-is-pk": "0",
            },
            "a_href": None,
        },
        {
            "attrs": {
                "class": ["col-sortable"],
                "scope": "col",
                "data-column": "sortable",
                "data-column-type": "integer",
                "data-column-not-null": "0",
                "data-is-pk": "0",
            },
            "a_href": "/fixtures/sortable?_sort_desc=sortable",
        },
        {
            "attrs": {
                "class": ["col-sortable_with_nulls"],
                "scope": "col",
                "data-column": "sortable_with_nulls",
                "data-column-type": "real",
                "data-column-not-null": "0",
                "data-is-pk": "0",
            },
            "a_href": "/fixtures/sortable?_sort=sortable_with_nulls",
        },
        {
            "attrs": {
                "class": ["col-sortable_with_nulls_2"],
                "scope": "col",
                "data-column": "sortable_with_nulls_2",
                "data-column-type": "real",
                "data-column-not-null": "0",
                "data-is-pk": "0",
            },
            "a_href": "/fixtures/sortable?_sort=sortable_with_nulls_2",
        },
        {
            "attrs": {
                "class": ["col-text"],
                "scope": "col",
                "data-column": "text",
                "data-column-type": "text",
                "data-column-not-null": "0",
                "data-is-pk": "0",
            },
            "a_href": "/fixtures/sortable?_sort=text",
        },
    ]


@pytest.mark.asyncio
async def test_facet_display(ds_client):
    response = await ds_client.get(
        "/fixtures/facetable?_facet=planet_int&_facet=_city_id&_facet=on_earth"
    )
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    divs = soup.find("div", {"class": "facet-results"}).findAll("div")
    actual = []
    for div in divs:
        actual.append(
            {
                "name": div.find("strong").text.split()[0],
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
    assert actual == [
        {
            "name": "_city_id",
            "items": [
                {
                    "name": "San Francisco",
                    "qs": "_facet=planet_int&_facet=_city_id&_facet=on_earth&_city_id__exact=1",
                    "count": 6,
                },
                {
                    "name": "Los Angeles",
                    "qs": "_facet=planet_int&_facet=_city_id&_facet=on_earth&_city_id__exact=2",
                    "count": 4,
                },
                {
                    "name": "Detroit",
                    "qs": "_facet=planet_int&_facet=_city_id&_facet=on_earth&_city_id__exact=3",
                    "count": 4,
                },
                {
                    "name": "Memnonia",
                    "qs": "_facet=planet_int&_facet=_city_id&_facet=on_earth&_city_id__exact=4",
                    "count": 1,
                },
            ],
        },
        {
            "name": "planet_int",
            "items": [
                {
                    "name": "1",
                    "qs": "_facet=planet_int&_facet=_city_id&_facet=on_earth&planet_int=1",
                    "count": 14,
                },
                {
                    "name": "2",
                    "qs": "_facet=planet_int&_facet=_city_id&_facet=on_earth&planet_int=2",
                    "count": 1,
                },
            ],
        },
        {
            "name": "on_earth",
            "items": [
                {
                    "name": "1",
                    "qs": "_facet=planet_int&_facet=_city_id&_facet=on_earth&on_earth=1",
                    "count": 14,
                },
                {
                    "name": "0",
                    "qs": "_facet=planet_int&_facet=_city_id&_facet=on_earth&on_earth=0",
                    "count": 1,
                },
            ],
        },
    ]


@pytest.mark.asyncio
async def test_facets_persist_through_filter_form(ds_client):
    response = await ds_client.get(
        "/fixtures/facetable?_facet=planet_int&_facet=_city_id&_facet_array=tags"
    )
    assert response.status_code == 200
    inputs = Soup(response.text, "html.parser").find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [(hidden["name"], hidden["value"]) for hidden in hiddens] == [
        ("_facet", "planet_int"),
        ("_facet", "_city_id"),
        ("_facet_array", "tags"),
    ]


@pytest.mark.asyncio
async def test_next_does_not_persist_in_hidden_field(ds_client):
    response = await ds_client.get("/fixtures/searchable?_size=1&_next=1")
    assert response.status_code == 200
    inputs = Soup(response.text, "html.parser").find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [(hidden["name"], hidden["value"]) for hidden in hiddens] == [
        ("_size", "1"),
    ]


@pytest.mark.asyncio
async def test_table_html_simple_primary_key(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key?_size=3")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    assert table["class"] == ["rows-and-columns"]
    ths = table.findAll("th")
    assert "id\xa0▼" == ths[0].find("a").string.strip()
    for expected_col, th in zip(("content",), ths[1:]):
        a = th.find("a")
        assert expected_col == a.string
        assert a["href"].endswith(f"/simple_primary_key?_size=3&_sort={expected_col}")
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


@pytest.mark.asyncio
async def test_table_csv_json_export_interface(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key?id__gt=2")
    assert response.status_code == 200
    # The links at the top of the page
    links = (
        Soup(response.text, "html.parser")
        .find("p", {"class": "export-links"})
        .findAll("a")
    )
    actual = [link["href"] for link in links]
    expected = [
        "/fixtures/simple_primary_key.json?id__gt=2",
        "/fixtures/simple_primary_key.testall?id__gt=2",
        "/fixtures/simple_primary_key.testnone?id__gt=2",
        "/fixtures/simple_primary_key.testresponse?id__gt=2",
        "/fixtures/simple_primary_key.csv?id__gt=2&_size=max",
        "#export",
    ]
    assert expected == actual
    # And the advanced export box at the bottom:
    div = Soup(response.text, "html.parser").find("div", {"class": "advanced-export"})
    json_links = [a["href"] for a in div.find("p").findAll("a")]
    assert [
        "/fixtures/simple_primary_key.json?id__gt=2",
        "/fixtures/simple_primary_key.json?id__gt=2&_shape=array",
        "/fixtures/simple_primary_key.json?id__gt=2&_shape=array&_nl=on",
        "/fixtures/simple_primary_key.json?id__gt=2&_shape=object",
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


@pytest.mark.asyncio
async def test_csv_json_export_links_include_labels_if_foreign_keys(ds_client):
    response = await ds_client.get("/fixtures/facetable")
    assert response.status_code == 200
    links = (
        Soup(response.text, "html.parser")
        .find("p", {"class": "export-links"})
        .findAll("a")
    )
    actual = [link["href"] for link in links]
    expected = [
        "/fixtures/facetable.json?_labels=on",
        "/fixtures/facetable.testall?_labels=on",
        "/fixtures/facetable.testnone?_labels=on",
        "/fixtures/facetable.testresponse?_labels=on",
        "/fixtures/facetable.csv?_labels=on&_size=max",
        "#export",
    ]
    assert expected == actual


@pytest.mark.asyncio
async def test_table_not_exists(ds_client):
    assert "Table not found: blah" in (await ds_client.get("/fixtures/blah")).text


@pytest.mark.asyncio
async def test_table_html_no_primary_key(ds_client):
    response = await ds_client.get("/fixtures/no_primary_key")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    # We have disabled sorting for this table using metadata.json
    assert ["content", "a", "b", "c"] == [
        th.string.strip() for th in table.select("thead th")[2:]
    ]
    expected = [
        [
            '<td class="col-Link type-pk"><a href="/fixtures/no_primary_key/{}">{}</a></td>'.format(
                i, i
            ),
            f'<td class="col-rowid type-int">{i}</td>',
            f'<td class="col-content type-str">{i}</td>',
            f'<td class="col-a type-str">a{i}</td>',
            f'<td class="col-b type-str">b{i}</td>',
            f'<td class="col-c type-str">c{i}</td>',
        ]
        for i in range(1, 51)
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


@pytest.mark.asyncio
async def test_rowid_sortable_no_primary_key(ds_client):
    response = await ds_client.get("/fixtures/no_primary_key")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    assert table["class"] == ["rows-and-columns"]
    ths = table.findAll("th")
    assert "rowid\xa0▼" == ths[1].find("a").string.strip()


@pytest.mark.asyncio
async def test_table_html_compound_primary_key(ds_client):
    response = await ds_client.get("/fixtures/compound_primary_key")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    ths = table.findAll("th")
    assert "Link" == ths[0].string.strip()
    for expected_col, th in zip(("pk1", "pk2", "content"), ths[1:]):
        a = th.find("a")
        assert expected_col == a.string
        assert th["class"] == [f"col-{expected_col}"]
        assert a["href"].endswith(f"/compound_primary_key?_sort={expected_col}")
    expected = [
        [
            '<td class="col-Link type-pk"><a href="/fixtures/compound_primary_key/a,b">a,b</a></td>',
            '<td class="col-pk1 type-str">a</td>',
            '<td class="col-pk2 type-str">b</td>',
            '<td class="col-content type-str">c</td>',
        ],
        [
            '<td class="col-Link type-pk"><a href="/fixtures/compound_primary_key/a~2Fb,~2Ec-d">a/b,.c-d</a></td>',
            '<td class="col-pk1 type-str">a/b</td>',
            '<td class="col-pk2 type-str">.c-d</td>',
            '<td class="col-content type-str">c</td>',
        ],
    ]
    assert [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ] == expected


@pytest.mark.asyncio
async def test_table_html_foreign_key_links(ds_client):
    response = await ds_client.get("/fixtures/foreign_key_references")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    actual = [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]
    assert actual == [
        [
            '<td class="col-pk type-pk"><a href="/fixtures/foreign_key_references/1">1</a></td>',
            '<td class="col-foreign_key_with_label type-str"><a href="/fixtures/simple_primary_key/1">hello</a>\xa0<em>1</em></td>',
            '<td class="col-foreign_key_with_blank_label type-str"><a href="/fixtures/simple_primary_key/3">-</a>\xa0<em>3</em></td>',
            '<td class="col-foreign_key_with_no_label type-str"><a href="/fixtures/primary_key_multiple_columns/1">1</a></td>',
            '<td class="col-foreign_key_compound_pk1 type-str">a</td>',
            '<td class="col-foreign_key_compound_pk2 type-str">b</td>',
        ],
        [
            '<td class="col-pk type-pk"><a href="/fixtures/foreign_key_references/2">2</a></td>',
            '<td class="col-foreign_key_with_label type-none">\xa0</td>',
            '<td class="col-foreign_key_with_blank_label type-none">\xa0</td>',
            '<td class="col-foreign_key_with_no_label type-none">\xa0</td>',
            '<td class="col-foreign_key_compound_pk1 type-none">\xa0</td>',
            '<td class="col-foreign_key_compound_pk2 type-none">\xa0</td>',
        ],
    ]


@pytest.mark.asyncio
async def test_table_html_foreign_key_facets(ds_client):
    response = await ds_client.get(
        "/fixtures/foreign_key_references?_facet=foreign_key_with_blank_label"
    )
    assert response.status_code == 200
    assert (
        '<li><a href="http://localhost/fixtures/foreign_key_references?_facet=foreign_key_with_blank_label&amp;foreign_key_with_blank_label=3"'
        ' data-facet-value="3">-</a> 1</li>'
    ) in response.text


@pytest.mark.asyncio
async def test_table_html_disable_foreign_key_links_with_labels(ds_client):
    response = await ds_client.get(
        "/fixtures/foreign_key_references?_labels=off&_size=1"
    )
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    actual = [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]
    assert actual == [
        [
            '<td class="col-pk type-pk"><a href="/fixtures/foreign_key_references/1">1</a></td>',
            '<td class="col-foreign_key_with_label type-str">1</td>',
            '<td class="col-foreign_key_with_blank_label type-str">3</td>',
            '<td class="col-foreign_key_with_no_label type-str">1</td>',
            '<td class="col-foreign_key_compound_pk1 type-str">a</td>',
            '<td class="col-foreign_key_compound_pk2 type-str">b</td>',
        ]
    ]


@pytest.mark.asyncio
async def test_table_html_foreign_key_custom_label_column(ds_client):
    response = await ds_client.get("/fixtures/custom_foreign_key_label")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    expected = [
        [
            '<td class="col-pk type-pk"><a href="/fixtures/custom_foreign_key_label/1">1</a></td>',
            '<td class="col-foreign_key_with_custom_label type-str"><a href="/fixtures/primary_key_multiple_columns_explicit_label/1">world2</a>\xa0<em>1</em></td>',
        ]
    ]
    assert expected == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


@pytest.mark.asyncio
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
async def test_table_html_filter_form_column_options(
    path, expected_column_options, ds_client
):
    response = await ds_client.get(path)
    assert response.status_code == 200
    form = Soup(response.text, "html.parser").find("form")
    column_options = [
        o.attrs.get("value") or o.string
        for o in form.select("select[name=_filter_column] option")
    ]
    assert expected_column_options == column_options


@pytest.mark.asyncio
async def test_table_html_filter_form_still_shows_nocol_columns(ds_client):
    # https://github.com/simonw/datasette/issues/1503
    response = await ds_client.get("/fixtures/sortable?_nocol=sortable")
    assert response.status_code == 200
    form = Soup(response.text, "html.parser").find("form")
    assert [
        o.string
        for o in form.select("select[name='_filter_column']")[0].select("option")
    ] == [
        "- column -",
        "pk1",
        "pk2",
        "content",
        "sortable_with_nulls",
        "sortable_with_nulls_2",
        "text",
        # Moved to the end because it is no longer returned by the query:
        "sortable",
    ]


@pytest.mark.asyncio
async def test_compound_primary_key_with_foreign_key_references(ds_client):
    # e.g. a many-to-many table with a compound primary key on the two columns
    response = await ds_client.get("/fixtures/searchable_tags")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
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


@pytest.mark.asyncio
async def test_view_html(ds_client):
    response = await ds_client.get("/fixtures/simple_view?_size=3")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
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


@pytest.mark.asyncio
async def test_table_metadata(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    # Page title should be custom and should be HTML escaped
    assert "This &lt;em&gt;HTML&lt;/em&gt; is escaped" == inner_html(soup.find("h1"))
    # Description should be custom and NOT escaped (we used description_html)
    assert "Simple <em>primary</em> key" == inner_html(
        soup.find("div", {"class": "metadata-description"})
    )
    # The source/license should be inherited
    assert_footer_links(soup)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,has_object,has_stream,has_expand",
    [
        ("/fixtures/no_primary_key", False, True, False),
        ("/fixtures/complex_foreign_keys", True, False, True),
    ],
)
async def test_advanced_export_box(ds_client, path, has_object, has_stream, has_expand):
    response = await ds_client.get(path)
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
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


@pytest.mark.asyncio
async def test_extra_where_clauses(ds_client):
    response = await ds_client.get(
        "/fixtures/facetable?_where=_neighborhood='Dogpatch'&_where=_city_id=1"
    )
    soup = Soup(response.text, "html.parser")
    div = soup.select(".extra-wheres")[0]
    assert "2 extra where clauses" == div.find("h3").text
    hrefs = [a["href"] for a in div.findAll("a")]
    assert [
        "/fixtures/facetable?_where=_city_id%3D1",
        "/fixtures/facetable?_where=_neighborhood%3D%27Dogpatch%27",
    ] == hrefs
    # These should also be persisted as hidden fields
    inputs = soup.find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [("_where", "_neighborhood='Dogpatch'"), ("_where", "_city_id=1")] == [
        (hidden["name"], hidden["value"]) for hidden in hiddens
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_hidden",
    [
        ("/fixtures/facetable?_size=10", [("_size", "10")]),
        (
            "/fixtures/facetable?_size=10&_ignore=1&_ignore=2",
            [
                ("_size", "10"),
                ("_ignore", "1"),
                ("_ignore", "2"),
            ],
        ),
    ],
)
async def test_other_hidden_form_fields(ds_client, path, expected_hidden):
    response = await ds_client.get(path)
    soup = Soup(response.text, "html.parser")
    inputs = soup.find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [(hidden["name"], hidden["value"]) for hidden in hiddens] == expected_hidden


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_hidden",
    [
        ("/fixtures/searchable?_search=terry", []),
        ("/fixtures/searchable?_sort=text2", []),
        ("/fixtures/searchable?_sort_desc=text2", []),
        ("/fixtures/searchable?_sort=text2&_where=1", [("_where", "1")]),
    ],
)
async def test_search_and_sort_fields_not_duplicated(ds_client, path, expected_hidden):
    # https://github.com/simonw/datasette/issues/1214
    response = await ds_client.get(path)
    soup = Soup(response.text, "html.parser")
    inputs = soup.find("form").findAll("input")
    hiddens = [i for i in inputs if i["type"] == "hidden"]
    assert [(hidden["name"], hidden["value"]) for hidden in hiddens] == expected_hidden


@pytest.mark.asyncio
async def test_binary_data_display_in_table(ds_client):
    response = await ds_client.get("/fixtures/binary_data")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    expected_tds = [
        [
            '<td class="col-Link type-pk"><a href="/fixtures/binary_data/1">1</a></td>',
            '<td class="col-rowid type-int">1</td>',
            '<td class="col-data type-bytes"><a class="blob-download" href="/fixtures/binary_data/1.blob?_blob_column=data">&lt;Binary:\xa07\xa0bytes&gt;</a></td>',
        ],
        [
            '<td class="col-Link type-pk"><a href="/fixtures/binary_data/2">2</a></td>',
            '<td class="col-rowid type-int">2</td>',
            '<td class="col-data type-bytes"><a class="blob-download" href="/fixtures/binary_data/2.blob?_blob_column=data">&lt;Binary:\xa07\xa0bytes&gt;</a></td>',
        ],
        [
            '<td class="col-Link type-pk"><a href="/fixtures/binary_data/3">3</a></td>',
            '<td class="col-rowid type-int">3</td>',
            '<td class="col-data type-none">\xa0</td>',
        ],
    ]
    assert expected_tds == [
        [str(td) for td in tr.select("td")] for tr in table.select("tbody tr")
    ]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("json", (True, False))
@pytest.mark.parametrize(
    "params,error",
    (
        ("?_sort=bad", "Cannot sort table by bad"),
        ("?_sort_desc=bad", "Cannot sort table by bad"),
        (
            "?_sort=state&_sort_desc=state",
            "Cannot use _sort and _sort_desc at the same time",
        ),
    ),
)
async def test_sort_errors(ds_client, json, params, error):
    path = "/fixtures/facetable{}{}".format(
        ".json" if json else "",
        params,
    )
    response = await ds_client.get(path)
    assert response.status_code == 400
    if json:
        assert response.json() == {
            "ok": False,
            "error": error,
            "status": 400,
            "title": None,
        }
    else:
        assert error in response.text


@pytest.mark.asyncio
async def test_metadata_sort(ds_client):
    response = await ds_client.get("/fixtures/facet_cities")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
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
    response = await ds_client.get("/fixtures/facet_cities?_sort_desc=name")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    rows = [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]
    assert list(reversed(expected)) == rows


@pytest.mark.asyncio
async def test_metadata_sort_desc(ds_client):
    response = await ds_client.get("/fixtures/attraction_characteristic")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
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
    response = await ds_client.get("/fixtures/attraction_characteristic?_sort=pk")
    assert response.status_code == 200
    table = Soup(response.text, "html.parser").find("table")
    rows = [[str(td) for td in tr.select("td")] for tr in table.select("tbody tr")]
    assert list(reversed(expected)) == rows


@pytest.mark.parametrize(
    "max_returned_rows,path,expected_num_facets,expected_ellipses,expected_ellipses_url",
    (
        (
            5,
            # Default should show 2 facets
            "/fixtures/facetable?_facet=_neighborhood",
            2,
            True,
            "/fixtures/facetable?_facet=_neighborhood&_facet_size=max",
        ),
        # _facet_size above max_returned_rows should show max_returned_rows (5)
        (
            5,
            "/fixtures/facetable?_facet=_neighborhood&_facet_size=50",
            5,
            True,
            "/fixtures/facetable?_facet=_neighborhood&_facet_size=max",
        ),
        # If max_returned_rows is high enough, should return all
        (
            20,
            "/fixtures/facetable?_facet=_neighborhood&_facet_size=max",
            14,
            False,
            None,
        ),
        # If num facets > max_returned_rows, show ... without a link
        # _facet_size above max_returned_rows should show max_returned_rows (5)
        (
            5,
            "/fixtures/facetable?_facet=_neighborhood&_facet_size=max",
            5,
            True,
            None,
        ),
    ),
)
def test_facet_more_links(
    max_returned_rows,
    path,
    expected_num_facets,
    expected_ellipses,
    expected_ellipses_url,
):
    with make_app_client(
        settings={"max_returned_rows": max_returned_rows, "default_facet_size": 2}
    ) as client:
        response = client.get(path)
        soup = Soup(response.body, "html.parser")
        lis = soup.select("#facet-neighborhood-b352a7 ul li:not(.facet-truncated)")
        facet_truncated = soup.select_one(".facet-truncated")
        assert len(lis) == expected_num_facets
        if not expected_ellipses:
            assert facet_truncated is None
        else:
            if expected_ellipses_url:
                assert facet_truncated.find("a")["href"] == expected_ellipses_url
            else:
                assert facet_truncated.find("a") is None


def test_unavailable_table_does_not_break_sort_relationships():
    # https://github.com/simonw/datasette/issues/1305
    with make_app_client(
        config={
            "databases": {
                "fixtures": {"tables": {"foreign_key_references": {"allow": False}}}
            }
        }
    ) as client:
        response = client.get("/?_sort=relationships")
        assert response.status == 200


@pytest.mark.asyncio
async def test_column_metadata(ds_client):
    response = await ds_client.get("/fixtures/roadside_attractions")
    soup = Soup(response.text, "html.parser")
    dl = soup.find("dl")
    assert [(dt.text, dt.nextSibling.text) for dt in dl.findAll("dt")] == [
        ("name", "The name of the attraction"),
        ("address", "The street address for the attraction"),
    ]
    assert (
        soup.select("th[data-column=name]")[0]["data-column-description"]
        == "The name of the attraction"
    )
    assert (
        soup.select("th[data-column=address]")[0]["data-column-description"]
        == "The street address for the attraction"
    )


def test_facet_total():
    # https://github.com/simonw/datasette/issues/1423
    # https://github.com/simonw/datasette/issues/1556
    with make_app_client(settings={"max_returned_rows": 100}) as client:
        path = "/fixtures/sortable?_facet=content&_facet=pk1"
        response = client.get(path)
        assert response.status == 200
    fragments = (
        '<span class="facet-info-total">&gt;30</span>',
        '<span class="facet-info-total">8</span>',
    )
    for fragment in fragments:
        assert fragment in response.text


@pytest.mark.asyncio
async def test_sort_rowid_with_next(ds_client):
    # https://github.com/simonw/datasette/issues/1470
    response = await ds_client.get("/fixtures/binary_data?_size=1&_next=1&_sort=rowid")
    assert response.status_code == 200


def assert_querystring_equal(expected, actual):
    assert sorted(expected.split("&")) == sorted(actual.split("&"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    (
        (
            "/fixtures/facetable",
            "fixtures: facetable: 15 rows",
        ),
        (
            "/fixtures/facetable?on_earth__exact=1",
            "fixtures: facetable: 14 rows where on_earth = 1",
        ),
    ),
)
async def test_table_page_title(ds_client, path, expected):
    response = await ds_client.get(path)
    title = Soup(response.text, "html.parser").find("title").text
    assert title == expected


@pytest.mark.asyncio
async def test_table_post_method_not_allowed(ds_client):
    response = await ds_client.post("/fixtures/facetable")
    assert response.status_code == 405
    assert "Method not allowed" in response.text


@pytest.mark.parametrize("allow_facet", (True, False))
def test_allow_facet_off(allow_facet):
    with make_app_client(settings={"allow_facet": allow_facet}) as client:
        response = client.get("/fixtures/facetable")
        expected = "DATASETTE_ALLOW_FACET = {};".format(
            "true" if allow_facet else "false"
        )
        assert expected in response.text
        if allow_facet:
            assert "Suggested facets" in response.text
        else:
            assert "Suggested facets" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "size,title,length_bytes",
    (
        (2000, ' title="2.0 KB"', "2,000"),
        (20000, ' title="19.5 KB"', "20,000"),
        (20, "", "20"),
    ),
)
async def test_format_of_binary_links(size, title, length_bytes):
    ds = Datasette()
    db_name = "binary-links-{}".format(size)
    db = ds.add_memory_database(db_name)
    sql = "select zeroblob({}) as blob".format(size)
    await db.execute_write("create table blobs as {}".format(sql))
    response = await ds.client.get("/{}/blobs".format(db_name))
    assert response.status_code == 200
    expected = "{}>&lt;Binary:&nbsp;{}&nbsp;bytes&gt;</a>".format(title, length_bytes)
    assert expected in response.text
    # And test with arbitrary SQL query too
    sql_response = await ds.client.get("/{}".format(db_name), params={"sql": sql})
    assert sql_response.status_code == 200
    assert expected in sql_response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    (
        # Blocked at table level
        {
            "databases": {
                "foreign_key_labels": {
                    "tables": {
                        # Table a is only visible to root
                        "a": {"allow": {"id": "root"}},
                    }
                }
            }
        },
        # Blocked at database level
        {
            "databases": {
                "foreign_key_labels": {
                    # Only root can view this database
                    "allow": {"id": "root"},
                    "tables": {
                        # But table b is visible to everyone
                        "b": {"allow": True},
                    },
                }
            }
        },
        # Blocked at the instance level
        {
            "allow": {"id": "root"},
            "databases": {
                "foreign_key_labels": {
                    "tables": {
                        # Table b is visible to everyone
                        "b": {"allow": True},
                    }
                }
            },
        },
    ),
)
async def test_foreign_key_labels_obey_permissions(config):
    ds = Datasette(config=config)
    db = ds.add_memory_database("foreign_key_labels")
    await db.execute_write(
        "create table if not exists a(id integer primary key, name text)"
    )
    await db.execute_write("insert or replace into a (id, name) values (1, 'hello')")
    await db.execute_write(
        "create table if not exists b(id integer primary key, name text, a_id integer references a(id))"
    )
    await db.execute_write(
        "insert or replace into b (id, name, a_id) values (1, 'world', 1)"
    )
    # Anonymous user can see table b but not table a
    blah = await ds.client.get("/foreign_key_labels.json")
    anon_a = await ds.client.get("/foreign_key_labels/a.json?_labels=on")
    assert anon_a.status_code == 403
    anon_b = await ds.client.get("/foreign_key_labels/b.json?_labels=on")
    assert anon_b.status_code == 200
    # root user can see both
    cookies = {"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")}
    root_a = await ds.client.get(
        "/foreign_key_labels/a.json?_labels=on", cookies=cookies
    )
    assert root_a.status_code == 200
    root_b = await ds.client.get(
        "/foreign_key_labels/b.json?_labels=on", cookies=cookies
    )
    assert root_b.status_code == 200
    # Labels should have been expanded for root
    assert root_b.json() == {
        "ok": True,
        "next": None,
        "rows": [{"id": 1, "name": "world", "a_id": {"value": 1, "label": "hello"}}],
        "truncated": False,
    }
    # But not for anon
    assert anon_b.json() == {
        "ok": True,
        "next": None,
        "rows": [{"id": 1, "name": "world", "a_id": 1}],
        "truncated": False,
    }
