import csv
import io
import json
import pathlib
import re
import shutil
import subprocess
import time
from urllib.parse import urlencode, urlsplit

import pytest
from bs4 import BeautifulSoup

from datasette import utils
from datasette.app import Datasette
from datasette.utils import sqlite3


@pytest.mark.parametrize(
    "value,column_type,encoded,decoded",
    [
        (b"0thei", "", "$blob:3074686569", b"0thei"),
        (b"", "BLOB", "$blob:", b""),
        (b"\x00\xff/,~+.%", "TEXT", "$blob:00ff2f2c7e2b2e25", b"\x00\xff/,~+.%"),
        ("$blob:61", "BLOB", "~24blob~3A61", "$blob:61"),
        ("$int:3", "", "~24int~3A3", "$int:3"),
        ("3", "", "3", "3"),
        (3, "", "$int:3", 3),
        (3, "BLOB", "$int:3", 3),
        (3, "ANY", "$int:3", 3),
        (3, "INTEGER", "3", "3"),
        (3, "BLOBINT", "3", "3"),
        (3.5, "", "$float:3~2E5", 3.5),
        (3.5, "REAL", "3~2E5", "3.5"),
        (-(2**63), "", "$int:-9223372036854775808", -(2**63)),
    ],
)
def test_typed_row_path(value, column_type, encoded, decoded):
    path = utils.path_from_row_pks(
        {"id": value}, ["id"], False, column_types={"id": column_type}
    )
    assert path == encoded
    actual = utils.decode_row_pks(path)[0]
    assert actual == decoded
    assert type(actual) is type(decoded)


def test_all_bytes_in_composite_row_path():
    value = bytes(range(256))
    path = utils.path_from_row_pks(
        {"a": value, "b": {"value": b"", "label": "empty"}}, ["a", "b"], False
    )
    assert utils.decode_row_pks(path) == [value, b""]


@pytest.fixture
def binary_ds(tmp_path):
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "create table original (segid, term, pgno, primary key(segid, term)) without rowid"
    )
    conn.executemany(
        "insert into original values (?, ?, ?)",
        [(3, b"0thei", 1), ("3", b"0thei", 2), (3.5, b"\xff\x00", 3)],
    )
    for name, column_type in [("untyped", ""), ("blobs", "BLOB"), ("texts", "TEXT")]:
        conn.execute(
            f"create table {name} (id {column_type} primary key, label text, sort_key blob)"
        )
        values = [
            b"",
            b"hello",
            b"\xff\x00/,~+.%",
            "hello",
            "$blob:68656c6c6f",
            "$int:3",
            "3",
        ]
        if name != "texts":
            values += [3, 3.5]
        conn.executemany(
            f"insert into {name} values (?, ?, ?)",
            [(value, f"row-{i}", bytes([i // 2])) for i, value in enumerate(values)],
        )
    conn.commit()
    conn.close()
    ds = Datasette([str(path)])
    ds.root_enabled = True
    yield ds
    ds.close()


def local_url(url):
    parts = urlsplit(url)
    return parts.path + ("?" + parts.query if parts.query else "")


async def row_links(ds, table):
    response = await ds.client.get(f"/data/{table}")
    assert response.status_code == 200, response.text
    soup = BeautifulSoup(response.text, "html.parser")
    return [row.select_one("td a")["href"] for row in soup.select("tbody tr[data-row]")]


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["original", "untyped", "blobs", "texts"])
async def test_generated_row_links_and_json_object_keys(binary_ds, table):
    links = await row_links(binary_ds, table)
    expected = (
        await binary_ds.get_database("data").execute(f"select * from {table}")
    ).rows
    assert len(links) == len(expected)
    assert len(set(links)) == len(expected)
    seen = set()
    for link in links:
        html = await binary_ds.client.get(link)
        assert html.status_code == 200, (link, html.text)
        response = await binary_ds.client.get(link + ".json?_extra=query")
        assert response.status_code == 200, (link, response.text)
        row = response.json()["rows"][0]
        seen.add(row.get("label", row.get("pgno")))
    assert len(seen) == len(expected)
    response = await binary_ds.client.get(f"/data/{table}.json?_shape=object")
    assert response.status_code == 200
    assert set(response.json()) == {link.rsplit("/", 1)[1] for link in links}


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["original", "untyped", "blobs", "texts"])
@pytest.mark.parametrize("sort", [None, "id", "-id", "sort_key", "-sort_key"])
async def test_binary_key_pagination(binary_ds, table, sort):
    if table == "original" and sort:
        sort = "-term" if sort.startswith("-") else "term"
    sort_query = ""
    order_by = "segid, term" if table == "original" else "id"
    if sort:
        column = sort.removeprefix("-")
        descending = sort.startswith("-")
        sort_query = "&" + urlencode({"_sort_desc" if descending else "_sort": column})
        order_by = f"{column} {'desc' if descending else 'asc'}, {order_by}"
    expected = (
        await binary_ds.get_database("data").execute(
            f"select * from {table} order by {order_by}"
        )
    ).rows
    expected_ids = [row["pgno" if table == "original" else "label"] for row in expected]
    path = f"/data/{table}.json?_size=1" + sort_query
    seen = []
    while path:
        response = await binary_ds.client.get(path)
        assert response.status_code == 200, response.text
        data = response.json()
        seen.extend(row.get("label", row.get("pgno")) for row in data["rows"])
        assert len(seen) <= len(expected_ids), "Pagination repeated rows"
        path = local_url(data["next_url"]) if data.get("next_url") else None
    assert seen == expected_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "$blob:0",
        "$blob:gg",
        "$blob:61+62",
        "$int:no",
        "$int:9223372036854775808",
        "$float:nan",
        "$float:abc",
    ],
)
async def test_invalid_typed_keys_are_bad_requests(binary_ds, path):
    for url in [
        f"/data/blobs/{path}.json",
        "/data/blobs.json?" + urlencode({"_next": path}),
    ]:
        response = await binary_ds.client.get(url)
        assert response.status_code == 400, (url, response.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["original", "blobs"])
async def test_binary_key_blob_downloads_and_csv(binary_ds, table):
    column = "term" if table == "original" else "id"
    path = "$int:3,$blob:3074686569" if table == "original" else "$blob:"
    expected = b"0thei" if table == "original" else b""
    response = await binary_ds.client.get(
        f"/data/{table}/{path}.blob?_blob_column={column}"
    )
    assert response.status_code == 200
    assert response.content == expected
    response = await binary_ds.client.get(f"/data/{table}.csv")
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    for row in rows:
        if row[column].startswith("http"):
            response = await binary_ds.client.get(local_url(row[column]))
            assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,path,column,value",
    [
        ("original", "$int:3,$blob:3074686569", "pgno", 10),
        ("blobs", "$blob:68656c6c6f", "label", "updated"),
        ("blobs", "$blob:", "label", "updated-empty"),
    ],
)
async def test_binary_key_update_delete_and_fragment(
    binary_ds, table, path, column, value
):
    token = "dstok_" + binary_ds.sign(
        {"a": "root", "token": "dstok", "t": int(time.time())}, namespace="token"
    )
    headers = {"Authorization": f"Bearer {token}"}
    url = f"/data/{table}/{path}"
    response = await binary_ds.client.post(
        url + "/-/update", json={"update": {column: value}}, headers=headers
    )
    assert response.status_code == 200, response.text
    response = await binary_ds.client.get(url + ".json")
    assert response.json()["rows"][0][column] == value
    response = await binary_ds.client.get(
        f"/data/{table}/-/fragment?" + urlencode({"_row": path})
    )
    assert response.status_code == 200, response.text
    soup = BeautifulSoup(response.text, "html.parser")
    assert [row["data-row"] for row in soup.select("tr[data-row]")] == [path]
    response = await binary_ds.client.post(url + "/-/delete", json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert (await binary_ds.client.get(url + ".json")).status_code == 404
    # The text key with the same contents must survive the BLOB operation.
    if table == "blobs" and path != "$blob:":
        assert (await binary_ds.client.get("/data/blobs/hello.json")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,path", [(b"hello", "$blob:68656c6c6f"), (3, "$int:3"), (3.5, "$float:3~2E5")]
)
async def test_binary_and_numeric_foreign_key_links(binary_ds, value, path):
    db = binary_ds.get_database("data")
    await db.execute_write(
        "create table children (id integer primary key, parent references blobs(id))"
    )
    await db.execute_write("insert into children values (1, ?)", [value])
    await db.execute_write("insert into children values (2, ?)", [str(value)])
    response = await binary_ds.client.get(
        f"/data/blobs/{path}.json?_extra=foreign_key_tables"
    )
    assert response.status_code == 200, response.text
    related = response.json()["foreign_key_tables"][0]
    assert related["count"] == 1
    response = await binary_ds.client.get(related["link"])
    assert response.status_code == 200, response.text
    soup = BeautifulSoup(response.text, "html.parser")
    assert len(soup.select("tbody tr[data-row]")) == 1
    response = await binary_ds.client.get("/data/children?_labels=on")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    link = soup.select_one("tbody tr[data-row='1'] td.col-parent a")
    assert link["href"] == f"/data/blobs/{path}"
    assert (await binary_ds.client.get(link["href"])).status_code == 200


@pytest.mark.asyncio
async def test_real_fts_shadow_table(binary_ds):
    db = binary_ds.get_database("data")
    await db.execute_write("create virtual table documents using fts5(body)")
    for i in range(3):
        await db.execute_write("insert into documents values (?)", [f"hello world {i}"])
    links = await row_links(binary_ds, "documents_idx")
    assert links
    for link in links:
        response = await binary_ds.client.get(link + ".json")
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_strict_any_primary_key(binary_ds):
    db = binary_ds.get_database("data")
    await db.execute_write("create table anything (id ANY primary key) strict")
    for value in [3, "3", b"3"]:
        await db.execute_write("insert into anything values (?)", [value])
    links = await row_links(binary_ds, "anything")
    assert len(set(links)) == 3
    for link in links:
        assert (await binary_ds.client.get(link + ".json")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.skipif(
    not shutil.which("node"), reason="Node.js is required for browser encoding tests"
)
async def test_browser_generated_row_paths(binary_ds):
    db = binary_ds.get_database("data")
    await db.execute_write("create table integers (id integer primary key)")
    await db.execute_write("insert into integers values (3)")
    examples = []
    for table in ["original", "blobs", "texts", "integers"]:
        rows = (await db.execute(f"select * from {table}")).dicts()
        page = await binary_ds.client.get(f"/data/{table}", actor={"id": "root"})
        assert page.status_code == 200
        metadata = json.loads(
            re.search(r"window\._datasetteTableData\s*=\s*({.*?});", page.text).group(1)
        )
        columns = metadata["insertRow"]["primaryKeyTypes"]
        pks = metadata["insertRow"]["primaryKeys"]
        paths = await row_links(binary_ds, table)
        for row in rows:
            examples.append(
                {
                    "row": row,
                    "pks": pks,
                    "columns": columns,
                    "table": table,
                    "paths": paths,
                }
            )
    script = (
        pathlib.Path(__file__).parent.parent / "datasette" / "static" / "edit-tools.js"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "node",
            "-e",
            """
const fs = require('node:fs');
const vm = require('node:vm');
const context = {document: {addEventListener() {}}, TextEncoder, atob, URL,
                 location: {href: 'http://localhost/'}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(c => ({
  path: context.rowPathFromRowData(c.row, c.pks, c.columns),
  fk: c.pks.length === 1 ? context.foreignKeyRowUrl(
    '/data/' + c.table + '/-/autocomplete', c.row[c.pks[0]]) : null
}))));
""",
            str(script),
        ],
        input=json.dumps(examples, cls=utils.CustomJSONEncoder),
        capture_output=True,
        text=True,
        check=True,
    )
    for example, encoded in zip(examples, json.loads(result.stdout)):
        path = f"/data/{example['table']}/{encoded['path']}"
        assert path in example["paths"]
        response = await binary_ds.client.get(path + ".json")
        assert response.status_code == 200, response.text
        if encoded["fk"]:
            response = await binary_ds.client.get(local_url(encoded["fk"]) + ".json")
            assert response.status_code == 200, response.text


@pytest.mark.skipif(
    not shutil.which("node"), reason="Node.js is required for browser encoding tests"
)
def test_browser_row_fragment_accepts_equivalent_identifier():
    # JavaScript and Python may spell equal numbers differently (3 versus 3.0,
    # or 1e-7 versus 1e-07). A fragment contains the server's canonical row URL.
    script = (
        pathlib.Path(__file__).parent.parent / "datasette" / "static" / "edit-tools.js"
    )
    result = subprocess.run(
        [
            "node",
            "-e",
            """
const fs = require('node:fs');
const vm = require('node:vm');
const canonicalRow = {getAttribute() { return '$float:1e-07'; }};
const doc = {querySelectorAll() { return [canonicalRow]; }};
const context = {
  document: {addEventListener() {}},
  fetch: async () => ({ok: true, text: async () => '<table></table>'}),
  DOMParser: class {parseFromString() { return doc; }}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context.fetchUpdatedRowElement({
  currentFragmentUrl: '/data/example/-/fragment?_row=$float:1e-7',
  currentRowId: '$float:1e-7'
}).then(row => {if (row !== canonicalRow) process.exit(1);});
""",
            str(script),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


import asyncio
