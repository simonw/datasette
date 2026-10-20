import pytest

from datasette.app import Datasette
from datasette.utils import escape_fts
from datasette.utils.sqlite import sqlite3


@pytest.mark.parametrize(
    "query,expected",
    [
        ("“Rebecca Sugar”", '"Rebecca Sugar"'),
        ('“Rebecca Sugar"', '"Rebecca Sugar"'),
        ('"Rebecca Sugar”', '"Rebecca Sugar"'),
        ("“Rebecca Sugar", '"Rebecca Sugar"'),
        ("”Rebecca Sugar“", '"Rebecca Sugar"'),
        ("“Rebecca Sugar” animation", '"Rebecca Sugar" "animation"'),
        ("“Rebecca Sugar” “Steven Universe”", '"Rebecca Sugar" "Steven Universe"'),
        ("“”", ""),
        ("", ""),
        ('"Rebecca Sugar"', '"Rebecca Sugar"'),
        ("Rebecca Sugar", '"Rebecca" "Sugar"'),
        ("Rebecca’s Sugar", '"Rebecca’s" "Sugar"'),
        ("‘Rebecca Sugar’", '"‘Rebecca" "Sugar’"'),
    ],
)
def test_escape_fts_curly_quotes(query, expected):
    assert escape_fts(query) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("fts_module", ["fts4", "fts5"])
@pytest.mark.parametrize("search_key", ["_search", "_search_title"])
@pytest.mark.parametrize(
    "table_mode,query_mode,expected_ids",
    [
        (None, None, [1]),
        (None, "escaped", [1]),
        (None, "raw", None),
        ("raw", None, None),
        ("raw", "escaped", [1]),
    ],
)
async def test_curly_quote_phrase_search(
    tmp_path, fts_module, search_key, table_mode, query_mode, expected_ids
):
    path = tmp_path / "quotes.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("create table documents (id integer primary key, title text)")
        conn.executemany(
            "insert into documents values (?, ?)",
            [(1, "Rebecca Sugar"), (2, "Rebecca likes Sugar"), (3, "Rebecca")],
        )
        conn.execute(
            f"create virtual table documents_fts using {fts_module}"
            '(title, content="documents")'
        )
        conn.execute("insert into documents_fts(documents_fts) values ('rebuild')")
        conn.commit()
        raw_ids = [
            row[0]
            for row in conn.execute(
                "select rowid from documents_fts where documents_fts match ? order by rowid",
                ["“Rebecca Sugar”"],
            )
        ]
    finally:
        conn.close()

    table_config = {"searchmode": table_mode} if table_mode else {}
    ds = Datasette(
        [str(path)],
        config={"databases": {"quotes": {"tables": {"documents": table_config}}}},
    )
    try:
        await ds.invoke_startup()
        if query_mode == "raw" or (table_mode == "raw" and query_mode is None):
            expected_ids = raw_ids
        params = {
            search_key: "“Rebecca Sugar”",
            "_shape": "objects",
            "_sort": "id",
            "_extra": "query",
        }
        if query_mode:
            params["_searchmode"] = query_mode
        response = await ds.client.get("/quotes/documents.json", params=params)
        assert response.status_code == 200, response.text
        data = response.json()
        assert [row["id"] for row in data["rows"]] == expected_ids
        # SQL parameters and display context retain the user's original input.
        assert "“Rebecca Sugar”" in data["query"]["params"].values()

        # Straight quotes keep their existing phrase behavior in every mode.
        params[search_key] = '"Rebecca Sugar"'
        response = await ds.client.get("/quotes/documents.json", params=params)
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["rows"]] == [1]
    finally:
        ds.close()
