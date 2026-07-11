import base64
import json
import socket
import subprocess
import sys
import time

import httpx
import pytest

from datasette.fixtures import write_fixture_database
from datasette.utils.sqlite import sqlite3

PNG_1X1_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def find_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(process, url, timeout=30):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                "Datasette server exited early\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code < 500:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except httpx.HTTPError as ex:
            last_error = repr(ex)
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    else:
        stdout, stderr = process.communicate()
    raise AssertionError(
        f"Timed out waiting for {url}: {last_error}\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )


@pytest.fixture
def datasette_server(tmp_path):
    fixtures_db_path = tmp_path / "fixtures.db"
    write_fixture_database(str(fixtures_db_path))
    data_db_path = tmp_path / "data.db"
    write_playwright_database(str(data_db_path))
    config_path = tmp_path / "datasette.json"
    write_playwright_config(config_path)
    plugins_dir = tmp_path / "plugins"
    write_playwright_plugin(plugins_dir)
    port = find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "datasette",
            str(fixtures_db_path),
            str(data_db_path),
            "--config",
            str(config_path),
            "--plugins-dir",
            str(plugins_dir),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--setting",
            "num_sql_threads",
            "1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        wait_for_server(process, url)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def write_playwright_database(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
        create table projects (
            id integer primary key,
            title text not null,
            metadata text,
            logo text,
            notes text,
            score integer default 5
        );
        create table defaults_demo (
            id integer primary key,
            created_ms integer default (CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER))
        );
        create table binary_files (
            id integer primary key,
            name text not null,
            data blob
        );
        create table bulk_defaults (
            id integer primary key,
            title text not null,
            status text not null default 'todo',
            score integer default 5
        );
        create table upsert_items (
            id text primary key,
            title text,
            metadata text
        );
        insert into projects (title, metadata, logo, notes, score) values
            (
                'Build Datasette',
                '{"ok": true}',
                'asset-original',
                'Initial notes',
                5
            );
        insert into upsert_items (id, title, metadata) values
            ('existing', 'Existing title', '{"old": true}');
        """)
        conn.execute(
            "insert into binary_files (name, data) values (?, ?)",
            ("Raw bytes", b"\x00\x01\x02\x03"),
        )
        conn.execute(
            "insert into binary_files (name, data) values (?, ?)",
            ("PNG image", PNG_1X1_BYTES),
        )
        conn.execute(
            "insert into binary_files (name, data) values (?, ?)",
            ("Null bytes", None),
        )
        conn.commit()
    finally:
        conn.close()


def write_playwright_config(config_path):
    config_path.write_text(
        json.dumps(
            {
                "databases": {
                    "data": {
                        "permissions": {
                            "create-table": True,
                            "insert-row": True,
                            "set-column-type": True,
                        },
                        "tables": {
                            "projects": {
                                "label_column": "title",
                                "column_types": {
                                    "metadata": "json",
                                    "logo": "asset",
                                    "notes": "textarea",
                                },
                                "permissions": {
                                    "alter-table": True,
                                    "insert-row": True,
                                    "update-row": True,
                                    "delete-row": True,
                                },
                            },
                            "defaults_demo": {
                                "permissions": {
                                    "alter-table": True,
                                },
                            },
                            "binary_files": {
                                "label_column": "name",
                                "permissions": {
                                    "insert-row": True,
                                    "update-row": True,
                                    "delete-row": True,
                                },
                            },
                            "bulk_defaults": {
                                "permissions": {
                                    "insert-row": True,
                                },
                            },
                            "upsert_items": {
                                "permissions": {
                                    "insert-row": True,
                                    "update-row": True,
                                },
                            },
                        },
                    },
                },
            }
        ),
        "utf-8",
    )


def write_playwright_plugin(plugins_dir):
    plugins_dir.mkdir()
    (plugins_dir / "playwright_plugin.py").write_text(
        '''
from datasette import hookimpl
from datasette.column_types import ColumnType, SQLiteType


class AssetColumnType(ColumnType):
    name = "asset"
    description = "Demo asset picker"
    sqlite_types = (SQLiteType.TEXT,)


@hookimpl
def register_column_types(datasette):
    return [AssetColumnType]


@hookimpl
def extra_body_script():
    return {
        "module": True,
        "script": """
document.addEventListener("datasette_init", function (event) {
  event.detail.registerPlugin("playwright-jump-section", {
    version: "0.1",
    makeJumpSections() {
      return [
        {
          id: "agent-chat",
          render(node, context) {
            if (!context.navigationSearch || !context.input) {
              throw new Error("Expected navigation search context");
            }
            node.innerHTML = [
              '<section class="agent-jump-start">',
              '<button type="button" data-playwright-agent-chat>',
              'Start a new agent chat',
              '</button>',
              '</section>',
            ].join("");
            node.querySelector("button").addEventListener("click", function () {
              window.location.href = "/-/playwright-agent";
            });
          },
        },
      ];
    },
  });

  event.detail.registerPlugin("playwright-asset-field", {
    version: "0.1",
    makeColumnField(context) {
      if (!context.columnType || context.columnType.type !== "asset") {
        return;
      }
      return {
        render(field) {
          const wrapper = document.createElement("div");
          wrapper.className = "playwright-asset-picker";
          wrapper.dataset.column = field.context.column;
          wrapper.dataset.database = field.context.database || "";
          wrapper.dataset.table = field.context.table || "";
          wrapper.dataset.tableUrl = field.context.tableUrl || "";
          wrapper.dataset.mode = field.context.mode || "";
          wrapper.dataset.columnType = field.context.columnType.type;

          field.input.type = "hidden";
          const value = document.createElement("span");
          value.className = "playwright-asset-value";
          const button = document.createElement("button");
          button.type = "button";
          button.className = "playwright-asset-select";
          button.textContent = "Use demo asset";

          function sync() {
            value.textContent = field.getValue() || "No asset selected";
          }

          button.addEventListener("click", function () {
            field.setValue("asset-from-plugin");
            sync();
          });

          wrapper.appendChild(field.input);
          wrapper.appendChild(value);
          wrapper.appendChild(button);
          sync();
          return wrapper;
        },
        focus(field) {
          const button = field.root.querySelector(".playwright-asset-select");
          if (button) {
            button.focus();
          }
        },
      };
    },
  });
});
""",
    }
''',
        "utf-8",
    )


def project_rows(datasette_server, **filters):
    params = {
        "_shape": "objects",
        **{key: str(value) for key, value in filters.items()},
    }
    response = httpx.get(f"{datasette_server}data/projects.json", params=params)
    response.raise_for_status()
    return response.json()["rows"]


def project_row(datasette_server, pk):
    rows = project_rows(datasette_server, id=pk)
    assert len(rows) == 1
    return rows[0]


def binary_file_blob(datasette_server, pk):
    response = httpx.get(
        f"{datasette_server}data/binary_files/{pk}.blob",
        params={"_blob_column": "data"},
    )
    response.raise_for_status()
    return response.content


def wait_for_binary_control_file(binary_control, size, name=None):
    binary_control.locator(
        ".row-edit-binary-size", has_text=f"Binary: {size} bytes"
    ).wait_for()
    if name:
        binary_control.locator(".row-edit-binary-name", has_text=name).wait_for()


def bulk_default_rows(datasette_server, **filters):
    params = {
        "_shape": "objects",
        **{key: str(value) for key, value in filters.items()},
    }
    response = httpx.get(f"{datasette_server}data/bulk_defaults.json", params=params)
    response.raise_for_status()
    return response.json()["rows"]


def upsert_item_rows(datasette_server, **filters):
    params = {
        "_shape": "objects",
        **{key: str(value) for key, value in filters.items()},
    }
    response = httpx.get(f"{datasette_server}data/upsert_items.json", params=params)
    response.raise_for_status()
    return response.json()["rows"]


def upsert_item_row(datasette_server, pk):
    rows = upsert_item_rows(datasette_server, id=pk)
    assert len(rows) == 1
    return rows[0]


def open_bulk_insert_dialog(page, url):
    page.goto(url)
    page.locator('button[data-table-action="insert-row"]').click()
    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
    dialog.locator(".row-edit-bulk-insert").click()
    return dialog


def open_jump_menu(page):
    page.keyboard.press("/")
    page.locator("navigation-search .search-input").wait_for()


@pytest.mark.playwright
def test_datasette_homepage_contains_datasette(page, datasette_server):
    page.goto(datasette_server)
    assert "Datasette" in page.locator("body").inner_text()


@pytest.mark.playwright
def test_table_header_stays_visible_while_scrolling(page, datasette_server):
    page.set_viewport_size({"width": 600, "height": 400})
    page.goto(f"{datasette_server}fixtures/sortable?_size=100")

    wrapper = page.locator(".table-wrapper")
    header = wrapper.locator("table.rows-and-columns th").first
    assert wrapper.evaluate("node => node.scrollWidth > node.clientWidth")

    wrapper.evaluate("""node => window.scrollTo(
            0,
            node.getBoundingClientRect().top + window.scrollY + 100
        )""")
    page.wait_for_function("""() => Math.abs(document.querySelector(
            'table.rows-and-columns th'
        ).getBoundingClientRect().top) < 1""")
    assert abs(header.bounding_box()["y"]) < 1

    wrapper.evaluate("node => node.scrollLeft = 100")
    assert wrapper.evaluate("node => node.scrollLeft") == 100
    assert abs(header.bounding_box()["y"]) < 1


@pytest.mark.playwright
def test_create_table_flow(page, datasette_server):
    page.goto(f"{datasette_server}data")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-database-action="create-table"]').click()

    dialog = page.locator("#table-create-dialog")
    dialog.wait_for()
    assert dialog.locator(".modal-title").inner_text() == "Create a table in data"
    placeholder_select = dialog.locator(".table-create-custom-column-type").nth(0)
    assert placeholder_select.input_value() == ""
    assert (
        placeholder_select.locator("option:checked").inner_text() == "- custom type -"
    )
    assert "table-create-input-placeholder" in placeholder_select.get_attribute("class")
    assert (
        dialog.locator(".table-create-column-name").nth(0).get_attribute("placeholder")
        == "column name"
    )
    assert dialog.locator(".table-create-column-main").first.evaluate("""node => {
            const inputHeight = node.querySelector(
                ".table-create-column-name"
            ).getBoundingClientRect().height;
            const selectHeight = node.querySelector(
                ".table-create-column-type"
            ).getBoundingClientRect().height;
            return Math.abs(inputHeight - selectHeight) <= 1;
        }""")
    dialog.locator('input[name="table"]').fill("playwright_created")
    dialog.locator(".table-create-column-name").nth(1).fill("title")
    dialog.locator(".table-create-more-options").nth(1).click()
    dialog.locator(".table-create-not-null-input").nth(1).check()
    title_defaults = dialog.locator(".table-create-default-options").nth(1)
    assert title_defaults.locator("summary").inner_text() == "Set a default value"
    title_defaults.locator("summary").click()
    assert "or default to a specific value" in title_defaults.inner_text()
    title_default_expr = title_defaults.locator(".table-create-default-expr")
    title_default_input = title_defaults.locator(".table-create-default")
    assert (
        "Current timestamp in UTC, e.g. 2026-05-01 13:34:00"
        in title_default_expr.locator("option").nth(1).inner_text()
    )
    title_default_expr.select_option("current_timestamp")
    assert title_default_input.is_enabled()
    title_default_input.fill("Untitled")
    assert title_default_expr.input_value() == ""
    dialog.locator(".table-create-add-column").click()
    dialog.locator(".table-create-column-name").nth(2).fill("score")
    dialog.locator(".table-create-column-type").nth(2).select_option("integer")
    dialog.locator(".table-create-add-column").click()
    dialog.locator(".table-create-column-name").nth(3).fill("metadata")
    dialog.locator(".table-create-column-type").nth(3).select_option("integer")
    dialog.locator(".table-create-more-options").nth(3).click()
    dialog.locator(".table-create-custom-column-type").nth(3).select_option("json")
    assert dialog.locator(".table-create-column-type").nth(3).input_value() == "text"
    assert "table-create-input-placeholder" not in dialog.locator(
        ".table-create-custom-column-type"
    ).nth(3).get_attribute("class")

    dialog.locator(".table-create-save").click()
    page.wait_for_url("**/data/playwright_created")
    assert "playwright_created" in page.locator("h1").inner_text()

    response = httpx.get(
        f"{datasette_server}data/playwright_created.json?_extra=columns,column_types"
    )
    response.raise_for_status()
    data = response.json()
    assert data["columns"] == [
        "id",
        "title",
        "score",
        "metadata",
    ]
    assert data["column_types"] == {
        "metadata": {"type": "json", "config": None},
    }
    schema_response = httpx.get(
        f"{datasette_server}data/-/query.json",
        params={
            "sql": (
                "select sql from sqlite_master where type = 'table' "
                "and name = 'playwright_created'"
            )
        },
    )
    schema_response.raise_for_status()
    schema = schema_response.json()["rows"][0]["sql"]
    assert "title" in schema
    assert "NOT NULL DEFAULT 'Untitled'" in schema


@pytest.mark.playwright
def test_create_table_from_data_flow(page, datasette_server):
    page.goto(f"{datasette_server}data")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-database-action="create-table"]').click()

    dialog = page.locator("#table-create-dialog")
    dialog.wait_for()
    from_data = dialog.locator(".table-create-from-data")
    assert from_data.inner_text() == "Create table from data"
    from_data.click()

    assert dialog.locator(".table-create-columns").is_hidden()
    assert dialog.locator(".table-create-data").is_visible()
    assert dialog.locator(".table-create-save").inner_text() == "Preview rows"
    assert (
        dialog.locator(".table-create-data-note").inner_text()
        == "Paste TSV, CSV, or JSON. You can also open a file or drop it onto this textarea"
    )
    assert dialog.locator(".table-create-data-open-file").inner_text() == "open a file"
    assert (
        dialog.locator(".table-create-data-editor").evaluate(
            """node => Array.from(node.children)
            .filter((child) => !child.hidden)
            .map((child) => child.className)
            .join(" ")"""
        )
        == ("table-create-data-note table-create-input table-create-data-textarea")
    )
    assert dialog.locator(".table-create-manual").inner_text() == (
        "Create table manually"
    )
    assert (
        dialog.get_by_label("Paste TSV, CSV, or JSON").get_attribute("id")
        == "table-create-data-textarea"
    )

    textarea = dialog.locator(".table-create-data-textarea")
    dropped_value = textarea.evaluate("""node => new Promise((resolve) => {
            node.addEventListener("input", () => resolve(node.value), { once: true });
            const file = new File(["short_id,name\\nx,Ada"], "Repo Export 2026!!.CSV", { type: "text/csv" });
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            node.dispatchEvent(new DragEvent("dragenter", { bubbles: true, cancelable: true, dataTransfer }));
            node.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer }));
            node.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }));
        })""")
    assert dropped_value == "short_id,name\nx,Ada"
    assert dialog.locator(".table-create-table-name").input_value() == (
        "repo_export_2026"
    )

    dialog.locator(".table-create-table-name").fill("playwright_from_data")
    textarea.fill(
        json.dumps(
            {
                "metadata": [{"short_id": "a", "name": "Ignored", "score": 9}],
                "people": [
                    {"short_id": "b", "name": "Ada", "score": 1},
                    {"short_id": "c", "name": "Bob", "score": 2.5},
                ],
            }
        )
    )
    dialog.locator(".table-create-save").click()

    assert dialog.locator(".table-create-data-textarea").is_hidden()
    assert dialog.locator(".table-create-save").inner_text() == "Create table"
    assert dialog.locator(".table-create-cancel").inner_text() == "Back"
    assert dialog.locator(".table-create-data-preview-summary").inner_text() == (
        "Previewing 2 rows."
    )
    assert dialog.locator(".table-create-data-primary-key").input_value() == "short_id"
    preview_text = dialog.locator(".table-create-data-preview-table").inner_text()
    assert "short_id" in preview_text
    assert "Ada" in preview_text
    assert "2.5" in preview_text
    preview_cell_style = dialog.locator(
        ".table-create-data-preview-table td"
    ).first.evaluate(
        """node => ({
            overflowWrap: getComputedStyle(node).overflowWrap,
            whiteSpace: getComputedStyle(node).whiteSpace
        })"""
    )
    assert preview_cell_style == {
        "overflowWrap": "anywhere",
        "whiteSpace": "normal",
    }

    dialog.locator(".table-create-cancel").click()
    assert dialog.evaluate("node => node.open")
    assert dialog.locator(".table-create-data-textarea").is_visible()
    assert '"people"' in dialog.locator(".table-create-data-textarea").input_value()
    assert dialog.locator(".table-create-save").inner_text() == "Preview rows"

    dialog.locator(".table-create-save").click()
    assert dialog.locator(".table-create-save").inner_text() == "Create table"
    dialog.locator(".table-create-save").click()
    page.wait_for_url("**/data/playwright_from_data")

    response = httpx.get(
        f"{datasette_server}data/playwright_from_data.json?_shape=objects"
    )
    response.raise_for_status()
    data = response.json()
    assert data["rows"] == [
        {"short_id": "b", "name": "Ada", "score": 1},
        {"short_id": "c", "name": "Bob", "score": 2.5},
    ]


@pytest.mark.playwright
def test_create_table_from_csv_keeps_numeric_type_when_values_are_blank(
    page, datasette_server
):
    page.goto(f"{datasette_server}data")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-database-action="create-table"]').click()

    dialog = page.locator("#table-create-dialog")
    dialog.wait_for()
    dialog.locator(".table-create-from-data").click()
    dialog.locator(".table-create-table-name").fill("playwright_numeric_blanks")
    dialog.locator(".table-create-data-textarea").fill("name,score\nA,1\nB,")
    dialog.locator(".table-create-save").click()

    assert dialog.locator(".table-create-save").inner_text() == "Create table"
    preview_text = dialog.locator(".table-create-data-preview-table").inner_text()
    assert "A" in preview_text
    assert "1" in preview_text
    assert "B" in preview_text
    assert "null" in preview_text

    dialog.locator(".table-create-save").click()
    page.wait_for_url("**/data/playwright_numeric_blanks")

    response = httpx.get(
        f"{datasette_server}data/playwright_numeric_blanks.json?_shape=objects"
    )
    response.raise_for_status()
    assert response.json()["rows"] == [
        {"name": "A", "score": 1},
        {"name": "B", "score": None},
    ]

    schema_response = httpx.get(
        f"{datasette_server}data/-/query.json",
        params={
            "sql": (
                "select type from pragma_table_info('playwright_numeric_blanks') "
                "where name = 'score'"
            )
        },
    )
    schema_response.raise_for_status()
    assert schema_response.json()["rows"] == [{"type": "INTEGER"}]


@pytest.mark.playwright
def test_create_table_foreign_key_selection_updates_column_type(page, datasette_server):
    page.goto(f"{datasette_server}data")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-database-action="create-table"]').click()

    dialog = page.locator("#table-create-dialog")
    dialog.wait_for()
    dialog.locator(".table-create-more-options").nth(1).click()

    column_name = dialog.locator(".table-create-column-name").nth(1)
    type_select = dialog.locator(".table-create-column-type").nth(1)
    foreign_key_select = dialog.locator(".table-create-foreign-key-target").nth(1)
    assert column_name.input_value() == ""
    assert type_select.input_value() == "text"

    foreign_key_select.select_option("projects\u001fid")
    assert column_name.input_value() == "projects_id"
    assert type_select.input_value() == "integer"
    assert foreign_key_select.input_value() == "projects\u001fid"


@pytest.mark.playwright
def test_create_table_unix_default_expression_updates_column_type(
    page, datasette_server
):
    page.goto(f"{datasette_server}data")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-database-action="create-table"]').click()

    dialog = page.locator("#table-create-dialog")
    dialog.wait_for()
    row = dialog.locator(".table-create-column-row").nth(1)
    row.locator(".table-create-more-options").click()
    row.locator(".table-create-default-options summary").click()

    type_select = row.locator(".table-create-column-type")
    default_expr = row.locator(".table-create-default-expr")
    assert type_select.input_value() == "text"
    assert (
        "Current Unix time, integer milliseconds since the epoch"
        in default_expr.locator("option").last.inner_text()
    )

    default_expr.select_option("current_unixtime_ms")
    assert type_select.input_value() == "integer"


@pytest.mark.playwright
def test_alter_table_foreign_key_selection_updates_blank_column(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    dialog.locator(".table-alter-add-column").click()

    column_name = dialog.locator(".table-alter-column-name").last
    type_select = dialog.locator(".table-alter-column-type").last
    foreign_key_select = dialog.locator(".table-alter-foreign-key-target").last
    assert column_name.input_value() == ""
    assert type_select.input_value() == "text"

    foreign_key_select.select_option("projects\u001fid")
    assert column_name.input_value() == "projects_id"
    assert type_select.input_value() == "integer"
    assert foreign_key_select.input_value() == "projects\u001fid"


@pytest.mark.playwright
def test_alter_table_unix_default_expression_updates_column_type(
    page, datasette_server
):
    page.goto(f"{datasette_server}data/projects")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    dialog.locator(".table-alter-add-column").click()
    row = dialog.locator(".table-alter-column-row").last
    row.locator(".table-alter-default-options summary").click()

    type_select = row.locator(".table-alter-column-type")
    default_expr = row.locator(".table-alter-default-expr")
    assert type_select.input_value() == "text"
    assert (
        "Current Unix time, integer seconds since the epoch"
        in default_expr.locator("option").all_inner_texts()
    )

    default_expr.select_option("current_unixtime")
    assert type_select.input_value() == "integer"


@pytest.mark.playwright
def test_alter_table_existing_default_expression_populates_select(
    page, datasette_server
):
    page.goto(f"{datasette_server}data/defaults_demo")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    row = dialog.locator(".table-alter-column-row").nth(1)
    row.locator(".table-alter-more-options").click()
    row.locator(".table-alter-default-options summary").click()

    assert row.locator(".table-alter-default-expr").input_value() == (
        "current_unixtime_ms"
    )
    assert row.locator(".table-alter-default").input_value() == ""


@pytest.mark.playwright
def test_alter_table_flow(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    assert dialog.locator(".modal-title").inner_text() == "Alter table projects"
    assert dialog.locator(".table-alter-save").is_disabled()
    assert (
        dialog.locator(".table-alter-column-name").first.get_attribute("placeholder")
        == "column name"
    )
    assert dialog.locator(".table-alter-column-main").first.evaluate("""node => {
            const inputHeight = node.querySelector(
                ".table-alter-column-name"
            ).getBoundingClientRect().height;
            const selectHeight = node.querySelector(
                ".table-alter-column-type"
            ).getBoundingClientRect().height;
            return Math.abs(inputHeight - selectHeight) <= 1;
        }""")
    type_options = dialog.locator(".table-alter-column-type").first.locator("option")
    assert type_options.all_inner_texts() == [
        "text",
        "integer",
        "floating point number",
        "blob - binary data",
    ]
    first_more_options = dialog.locator(".table-alter-more-options").first
    assert first_more_options.inner_text() == "> Advanced options"
    first_more_options.click()
    assert first_more_options.inner_text() == "v Hide options"
    expanded_options_text = dialog.locator(
        ".table-alter-column-details"
    ).first.inner_text()
    assert dialog.locator(".table-alter-fields").evaluate(
        "node => node.scrollWidth <= node.clientWidth + 1"
    )
    assert "Not null" in expanded_options_text
    assert "This value cannot be left unset" in expanded_options_text
    assert "Set a default value" in expanded_options_text
    assert "Primary key" in expanded_options_text
    assert "This ID uniquely identifies the record" in expanded_options_text
    assert "Foreign key" in expanded_options_text
    first_defaults = dialog.locator(".table-alter-default-options").first
    first_defaults.locator("summary").click()
    assert "or default to a specific value" in first_defaults.inner_text()
    first_default_expr = first_defaults.locator(".table-alter-default-expr")
    first_default_input = first_defaults.locator(".table-alter-default")
    assert (
        "Current timestamp in UTC, e.g. 2026-05-01 13:34:00"
        in first_default_expr.locator("option").nth(1).inner_text()
    )
    first_default_expr.select_option("current_timestamp")
    assert first_default_input.is_enabled()
    first_default_input.fill("manual")
    assert first_default_expr.input_value() == ""

    dialog.locator(".table-alter-add-column").click()
    assert dialog.locator(".table-alter-save").is_enabled()
    dialog.locator(".table-alter-column-name").last.fill("status")
    dialog.locator(".table-alter-column-type").last.select_option("text")
    dialog.locator(".table-alter-default-options").last.locator("summary").click()
    dialog.locator(".table-alter-default").last.fill("planned")
    dialog.locator(".table-alter-save").click()
    review = dialog.locator(".table-alter-review")
    review.wait_for()
    assert not dialog.locator(".table-alter-column-list").is_visible()
    review_text = review.inner_text()
    assert "Add column status as text, with default value planned." in review_text
    assert "Set column order to" not in review_text
    assert dialog.locator(".table-alter-back").is_visible()
    assert dialog.locator(".table-alter-save").inner_text() == "Apply changes"
    dialog.locator(".table-alter-save").click()

    columns = []
    for _ in range(20):
        response = httpx.get(f"{datasette_server}data/projects.json?_extra=columns")
        response.raise_for_status()
        columns = response.json()["columns"]
        if "status" in columns:
            break
        time.sleep(0.1)
    assert "status" in columns


@pytest.mark.playwright
def test_alter_table_primary_key_columns_stay_at_top(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    rows = dialog.locator(".table-alter-column-row")
    assert rows.nth(0).locator(".table-alter-column-name").input_value() == "id"
    first_row_move_buttons = rows.nth(0).locator(".table-alter-move-controls button")
    for i in range(first_row_move_buttons.count()):
        assert first_row_move_buttons.nth(i).is_disabled()
        assert (
            first_row_move_buttons.nth(i).get_attribute("title")
            == "Primary key columns are always listed first"
        )

    assert rows.nth(1).locator(".table-alter-move-up").is_disabled()
    assert rows.nth(1).locator(".table-alter-move-top").get_attribute("title") == (
        "Primary key columns are always listed first"
    )
    assert rows.nth(1).locator(".table-alter-move-up").get_attribute("title") == (
        "Primary key columns are always listed first"
    )
    last_row = rows.nth(rows.count() - 1)
    assert last_row.locator(".table-alter-column-name").input_value() == "score"
    last_row.locator(".table-alter-move-top").click()
    assert rows.nth(0).locator(".table-alter-column-name").input_value() == "id"
    assert rows.nth(1).locator(".table-alter-column-name").input_value() == "score"


@pytest.mark.playwright
def test_alter_table_review_rename_primary_key_column(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    save = dialog.locator(".table-alter-save")
    assert save.is_disabled()
    dialog.locator(".table-alter-column-name").first.fill("id3")
    assert save.is_enabled()
    save.click()

    review = dialog.locator(".table-alter-review")
    review.wait_for()
    review_text = review.inner_text()
    assert "Rename column id to id3." in review_text
    assert "Set primary key to" not in review_text
    assert dialog.locator(".table-alter-review-name").all_inner_texts() == [
        "id",
        "id3",
    ]


@pytest.mark.playwright
def test_alter_table_review_rename_table(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    save = dialog.locator(".table-alter-save")
    rename_details = dialog.locator(".table-alter-table-options")
    assert rename_details.locator("summary").inner_text() == "Rename table"
    assert not dialog.locator(".table-alter-table-name").is_visible()
    assert save.is_disabled()

    rename_details.locator("summary").click()
    table_name = dialog.locator(".table-alter-table-name")
    assert table_name.input_value() == "projects"
    assert table_name.get_attribute("placeholder") == "table name"
    table_name.fill("projects_archive")
    assert save.is_enabled()
    save.click()

    review = dialog.locator(".table-alter-review")
    review.wait_for()
    assert "Rename table to projects_archive." in review.inner_text()
    assert dialog.locator(".table-alter-review-name").all_inner_texts() == [
        "projects_archive",
    ]


@pytest.mark.playwright
def test_alter_table_review_not_null_wording(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    dialog.locator(".table-alter-more-options").first.click()
    dialog.locator(".table-alter-not-null-input").first.check()
    dialog.locator(".table-alter-save").click()

    review = dialog.locator(".table-alter-review")
    review.wait_for()
    assert "Change column id: not null (require values)." in review.inner_text()


@pytest.mark.playwright
def test_alter_table_review_warns_when_dropping_column(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator("details.actions-menu-links summary").click()
    page.locator('button[data-table-action="alter-table"]').click()

    dialog = page.locator("#table-alter-dialog")
    dialog.wait_for()
    remove_buttons = dialog.locator(".table-alter-remove-column")
    remove_buttons.nth(remove_buttons.count() - 1).click()
    dialog.locator(".table-alter-save").click()

    review = dialog.locator(".table-alter-review")
    review.wait_for()
    assert not dialog.locator(".table-alter-column-list").is_visible()
    review_text = review.inner_text()
    assert "Warning: data in dropped columns will be permanently lost." in review_text
    assert "Drop column score." in review_text
    assert "Set column order to" not in review_text
    assert dialog.locator(".table-alter-review-damaging").inner_text() == (
        "Drop column score."
    )

    dialog.locator(".table-alter-back").click()
    assert dialog.locator(".table-alter-column-list").is_visible()
    assert dialog.locator(".table-alter-save").inner_text() == "Review changes"


@pytest.mark.playwright
def test_alter_table_cancel_skips_discard_prompt(page, datasette_server):
    def open_alter_dialog():
        page.locator("details.actions-menu-links").evaluate("node => node.open = true")
        page.locator('button[data-table-action="alter-table"]').click()
        dialog = page.locator("#table-alter-dialog")
        dialog.wait_for()
        return dialog

    page.goto(f"{datasette_server}data/projects")
    page.evaluate("""
        () => {
            window.__discardConfirmMessages = [];
            window.confirm = (message) => {
                window.__discardConfirmMessages.push(message);
                return false;
            };
        }
        """)

    dialog = open_alter_dialog()
    dialog.locator(".table-alter-add-column").click()
    dialog.locator(".table-alter-column-name").last.fill("cancel_me")
    dialog.locator(".table-alter-cancel").click()
    assert dialog.evaluate("node => node.open") is False
    assert page.evaluate("() => window.__discardConfirmMessages") == []

    dialog = open_alter_dialog()
    dialog.locator(".table-alter-add-column").click()
    dialog.locator(".table-alter-column-name").last.fill("escape_me")
    page.keyboard.press("Escape")
    assert page.evaluate("() => window.__discardConfirmMessages") == [
        "Discard table changes?"
    ]
    assert dialog.evaluate("node => node.open") is True

    page.evaluate("() => window.__discardConfirmMessages = []")
    dialog.evaluate(
        """node => node.dispatchEvent(new MouseEvent("click", {bubbles: true}))"""
    )
    assert page.evaluate("() => window.__discardConfirmMessages") == [
        "Discard table changes?"
    ]
    assert dialog.evaluate("node => node.open") is True


@pytest.mark.playwright
def test_navigation_search_tracks_and_renders_recent_items(page, datasette_server):
    page.goto(datasette_server)
    open_jump_menu(page)
    search = page.locator("navigation-search .search-input")
    search.fill("projects")
    result = page.locator("navigation-search .result-item", has_text="projects").first
    result.wait_for()
    result.click()
    page.wait_for_url("**/data/projects")

    page.goto(datasette_server)
    open_jump_menu(page)
    results = page.locator("navigation-search .results-container")
    results.locator(".results-heading", has_text="Recent").wait_for()
    assert "projects" in results.inner_text()

    page.locator("navigation-search [data-clear-recent-items]").click()
    page.locator("navigation-search .results-container", has_text="Recent").wait_for(
        state="detached"
    )


@pytest.mark.playwright
def test_navigation_search_renders_jump_sections_from_javascript_plugins(
    page, datasette_server
):
    page.goto(datasette_server)
    open_jump_menu(page)
    button = page.locator("navigation-search [data-playwright-agent-chat]")
    button.wait_for()
    assert button.inner_text() == "Start a new agent chat"
    button.click()
    page.wait_for_url("**/-/playwright-agent")


@pytest.mark.playwright
def test_insert_row_flow_uses_custom_column_field(page, datasette_server):
    page.add_init_script("""
        (() => {
            let clipboardText = "";
            Object.defineProperty(navigator, "clipboard", {
                configurable: true,
                get: () => ({
                    writeText: async (text) => {
                        clipboardText = String(text);
                    },
                    readText: async () => clipboardText,
                }),
            });
        })();
        """)
    page.goto(f"{datasette_server}data/projects")
    page.locator('button[data-table-action="insert-row"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
    bulk_insert = dialog.locator(".row-edit-bulk-insert")
    bulk_insert.wait_for()
    assert bulk_insert.inner_text() == "Insert multiple rows"
    current_url = page.url
    bulk_insert.click()
    assert page.url == current_url
    assert dialog.evaluate("node => node.open")
    assert dialog.locator(".row-edit-fields").is_hidden()
    assert dialog.locator(".row-edit-bulk").is_visible()
    assert dialog.locator(".row-edit-save").inner_text() == "Preview rows"
    assert (
        dialog.locator(".row-edit-bulk-note").inner_text()
        == "Paste TSV, CSV, or JSON. You can also open a file or drop it onto this textarea"
    )
    assert dialog.locator(".row-edit-bulk-open-file").inner_text() == "open a file"
    assert (
        dialog.locator(".row-edit-bulk-editor").evaluate(
            """node => Array.from(node.children)
            .filter((child) => !child.hidden)
            .map((child) => child.className)
            .join(" ")"""
        )
        == (
            "row-edit-bulk-note row-edit-input row-edit-bulk-textarea "
            "row-edit-bulk-actions"
        )
    )
    copy_template = dialog.locator(".row-edit-copy-template")
    assert copy_template.inner_text() == "Copy spreadsheet template"
    assert (
        dialog.get_by_label("Paste TSV, CSV, or JSON").get_attribute("id")
        == "row-edit-bulk-textarea"
    )
    assert dialog.locator(".row-edit-copy-template-label-narrow").text_content() == (
        "Copy template"
    )
    assert dialog.locator(".row-edit-bulk-template-note").inner_text() == (
        "You can paste the template into Google Sheets or Excel."
    )
    assert dialog.locator(".row-edit-bulk-template-note-narrow").text_content() == (
        "Paste into Google Sheets or Excel"
    )
    copy_template.click()
    page.wait_for_function(
        """() => document.querySelector(".row-edit-copy-template").textContent === "Copied" """
    )
    assert page.evaluate("navigator.clipboard.readText()") == (
        "title\tmetadata\tlogo\tnotes\tscore"
    )
    textarea = dialog.locator(".row-edit-bulk-textarea")
    textarea.fill("title\tmetadata\nFrom TSV\t{}")
    assert textarea.input_value() == "title\tmetadata\nFrom TSV\t{}"
    dropped_value = textarea.evaluate("""node => new Promise((resolve) => {
            node.addEventListener("input", () => resolve(node.value), { once: true });
            const file = new File(["\\n\\ntitle,metadata\\nFrom CSV,{}\\n,\\n  ,  \\n"], "rows.csv", { type: "text/csv" });
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            node.dispatchEvent(new DragEvent("dragenter", { bubbles: true, cancelable: true, dataTransfer }));
            node.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer }));
            node.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }));
        })""")
    assert dropped_value == "\n\ntitle,metadata\nFrom CSV,{}\n,\n  ,  \n"
    dialog.locator(".row-edit-save").click()
    assert dialog.evaluate("node => node.open")
    assert dialog.locator(".row-edit-bulk-textarea").is_hidden()
    assert dialog.locator(".row-edit-save").inner_text() == "Insert these rows"
    assert dialog.locator(".row-edit-bulk-preview-summary").inner_text() == (
        "Previewing 1 row."
    )
    preview_text = dialog.locator(".row-edit-bulk-preview-table").inner_text()
    assert "id" in preview_text
    assert "title" in preview_text
    assert "metadata" in preview_text
    assert "From CSV" in preview_text
    assert dialog.locator(".row-edit-bulk-preview-auto").first.text_content() == "auto"
    assert "null" not in preview_text
    assert "undefined" not in preview_text
    preview_cell_style = dialog.locator(
        ".row-edit-bulk-preview-table td"
    ).first.evaluate(
        """node => ({
            overflowWrap: getComputedStyle(node).overflowWrap,
            whiteSpace: getComputedStyle(node).whiteSpace
        })"""
    )
    assert preview_cell_style == {
        "overflowWrap": "anywhere",
        "whiteSpace": "normal",
    }
    assert dialog.locator(".row-edit-cancel").inner_text() == "Back"
    dialog.locator(".row-edit-cancel").click()
    assert dialog.evaluate("node => node.open")
    assert dialog.locator(".row-edit-bulk-textarea").is_visible()
    assert dialog.locator(".row-edit-bulk-textarea").input_value() == (
        "\n\ntitle,metadata\nFrom CSV,{}\n,\n  ,  \n"
    )
    assert dialog.locator(".row-edit-save").inner_text() == "Preview rows"
    single_insert = dialog.locator(".row-edit-single-insert")
    assert single_insert.inner_text() == "Insert single row"
    single_insert.click()
    assert dialog.locator(".row-edit-bulk").is_hidden()
    assert dialog.locator(".row-edit-fields").is_visible()
    assert dialog.locator(".row-edit-save").inner_text() == "Insert row"
    dialog.locator('input[name="title"]').fill("Launch Datasette Cloud")
    dialog.locator('textarea[name="metadata"]').fill(
        '{"ok": false, "source": "playwright"}'
    )
    dialog.locator('textarea[name="notes"]').fill("Inserted from Playwright")

    asset = dialog.locator(".playwright-asset-picker")
    asset.wait_for()
    assert asset.get_attribute("data-column") == "logo"
    assert asset.get_attribute("data-database") == "data"
    assert asset.get_attribute("data-table") == "projects"
    assert asset.get_attribute("data-mode") == "insert"
    asset.locator(".playwright-asset-select").click()
    assert asset.locator(".playwright-asset-value").inner_text() == "asset-from-plugin"

    dialog.locator(".row-edit-save").click()
    page.locator(".row-mutation-status", has_text="Inserted row 2").wait_for()
    row = page.locator('tr[data-row="2"]')
    row.wait_for()
    assert "Launch Datasette Cloud" in row.inner_text()

    data = project_row(datasette_server, 2)
    assert data["title"] == "Launch Datasette Cloud"
    assert data["metadata"] == '{"ok": false, "source": "playwright"}'
    assert data["logo"] == "asset-from-plugin"
    assert data["notes"] == "Inserted from Playwright"
    assert data["score"] == 5


@pytest.mark.playwright
def test_bulk_insert_preview_inserts_rows(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator('button[data-table-action="insert-row"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
    dialog.locator(".row-edit-bulk-insert").click()
    dialog.locator(".row-edit-bulk-textarea").fill(
        json.dumps(
            {
                "metadata": [{"title": "Ignored", "metadata": "{}"}],
                "projects": [
                    {"title": "Bulk one", "metadata": "{}"},
                    {"title": "Bulk two", "metadata": "{}"},
                ],
            }
        )
    )
    dialog.locator(".row-edit-save").click()
    assert dialog.locator(".row-edit-save").inner_text() == "Insert these rows"
    dialog.locator(".row-edit-save").click()
    dialog.locator(
        ".row-edit-bulk-progress-status", has_text="2 rows inserted."
    ).wait_for()
    assert dialog.locator(".row-edit-cancel").inner_text() == "Close and view table"
    dialog.locator(".row-edit-cancel").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url == f"{datasette_server}data/projects"

    assert project_rows(datasette_server, title="Bulk one")
    assert project_rows(datasette_server, title="Bulk two")


@pytest.mark.playwright
def test_bulk_insert_upsert_option_updates_existing_and_inserts_new(
    page, datasette_server
):
    dialog = open_bulk_insert_dialog(page, f"{datasette_server}data/upsert_items")
    textarea = dialog.locator(".row-edit-bulk-textarea")
    conflict_field = dialog.locator(".row-edit-bulk-conflict")
    conflict_select = dialog.locator(".row-edit-bulk-conflict-mode")

    assert conflict_field.is_hidden()
    textarea.fill("title\nNo primary key")
    assert conflict_field.is_hidden()

    textarea.fill(
        "id,title,metadata\nexisting,Updated by upsert,{}\nnew,Inserted by upsert,{}"
    )
    assert conflict_field.is_visible()
    assert (
        dialog.locator(".row-edit-bulk-conflict-label").inner_text()
        == "If the row exists already"
    )
    assert conflict_select.input_value() == "ignore"
    assert (
        conflict_select.evaluate("""node => Array.from(node.options)
        .filter((option) => !option.hidden)
        .map((option) => option.textContent.trim())""")
        == [
            "Stop with an error",
            "Skip existing rows",
            "Update existing and insert new",
        ]
    )

    conflict_select.select_option("upsert")
    assert conflict_select.input_value() == "upsert"
    dialog.locator(".row-edit-save").click()

    assert dialog.locator(".row-edit-save").inner_text() == "Update or insert rows"
    preview_text = dialog.locator(".row-edit-bulk-preview-table").inner_text()
    assert "Updated by upsert" in preview_text
    assert "Inserted by upsert" in preview_text

    dialog.locator(".row-edit-save").click()
    dialog.locator(
        ".row-edit-bulk-progress-status", has_text="2 rows upserted."
    ).wait_for()

    assert upsert_item_row(datasette_server, "existing")["title"] == (
        "Updated by upsert"
    )
    assert upsert_item_row(datasette_server, "new")["title"] == "Inserted by upsert"


@pytest.mark.playwright
def test_bulk_insert_conflicts_hide_upsert_without_update_permission(
    page, datasette_server
):
    dialog = open_bulk_insert_dialog(page, f"{datasette_server}data/bulk_defaults")
    textarea = dialog.locator(".row-edit-bulk-textarea")
    conflict_field = dialog.locator(".row-edit-bulk-conflict")
    conflict_select = dialog.locator(".row-edit-bulk-conflict-mode")

    textarea.fill("title\nOnly title")
    assert conflict_field.is_hidden()

    textarea.fill("id,title\n1,Only title")
    assert conflict_field.is_visible()
    assert conflict_select.input_value() == "ignore"
    assert (
        conflict_select.evaluate("""node => Array.from(node.options)
        .filter((option) => !option.hidden)
        .map((option) => option.textContent.trim())""")
        == [
            "Stop with an error",
            "Skip existing rows",
        ]
    )
    assert conflict_select.locator('option[value="upsert"]').evaluate(
        "node => node.hidden && node.disabled"
    )


@pytest.mark.playwright
def test_bulk_insert_live_validation_reports_unknown_columns(page, datasette_server):
    dialog = open_bulk_insert_dialog(page, f"{datasette_server}data/projects")
    textarea = dialog.locator(".row-edit-bulk-textarea")
    error = dialog.locator(".row-edit-error")
    save = dialog.locator(".row-edit-save")

    textarea.fill(json.dumps([{"id2": 1, "title": "Unknown column"}]))
    error.wait_for()
    assert error.inner_text() == "JSON row 1 has unknown column id2."
    assert save.is_disabled()
    assert textarea.evaluate("node => document.activeElement === node")

    textarea.fill("id2,title\n1,Unknown column")
    assert error.inner_text() == "Unknown column id2 in header row."
    assert save.is_disabled()

    textarea.fill("[")
    assert error.is_hidden()
    assert not save.is_disabled()

    textarea.fill(json.dumps([{"id": 1, "title": "Known column"}]))
    assert error.is_hidden()
    assert not save.is_disabled()
    assert dialog.locator(".row-edit-bulk-conflict").is_visible()
    assert dialog.locator(".row-edit-bulk-conflict-mode").input_value() == "ignore"


@pytest.mark.playwright
def test_bulk_insert_omits_columns_absent_from_pasted_input(page, datasette_server):
    page.goto(f"{datasette_server}data/bulk_defaults")
    page.locator('button[data-table-action="insert-row"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
    dialog.locator(".row-edit-bulk-insert").click()
    dialog.locator(".row-edit-bulk-textarea").fill("title\nOnly title")
    dialog.locator(".row-edit-save").click()

    assert dialog.locator(".row-edit-save").inner_text() == "Insert these rows"
    preview_text = dialog.locator(".row-edit-bulk-preview-table").inner_text()
    assert "Only title" in preview_text
    assert "undefined" not in preview_text
    assert dialog.locator(".row-edit-bulk-preview-auto").inner_text() == "auto"

    dialog.locator(".row-edit-save").click()
    dialog.locator(
        ".row-edit-bulk-progress-status", has_text="1 row inserted."
    ).wait_for()

    rows = bulk_default_rows(datasette_server, title="Only title")
    assert rows == [
        {
            "id": 1,
            "title": "Only title",
            "status": "todo",
            "score": 5,
        }
    ]


@pytest.mark.playwright
def test_bulk_insert_preview_accepts_single_column_input(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator('button[data-table-action="insert-row"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
    dialog.locator(".row-edit-bulk-insert").click()
    dialog.locator(".row-edit-bulk-textarea").fill("title\none\ntwo\nthree")
    dialog.locator(".row-edit-save").click()

    assert dialog.locator(".row-edit-save").inner_text() == "Insert these rows"
    assert dialog.locator(".row-edit-bulk-preview-summary").inner_text() == (
        "Previewing 3 rows."
    )
    preview_text = dialog.locator(".row-edit-bulk-preview-table").inner_text()
    assert "one" in preview_text
    assert "two" in preview_text
    assert "three" in preview_text
    assert "null" not in preview_text
    assert "undefined" not in preview_text


@pytest.mark.playwright
def test_edit_row_flow_validates_json_and_saves_changes(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator('tr[data-row="1"] button[data-row-action="edit"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
    assert dialog.locator(".row-edit-bulk-insert").is_hidden()
    assert dialog.locator(".row-edit-single-insert").is_hidden()
    assert dialog.locator(".row-edit-bulk").is_hidden()
    title = dialog.locator('input[name="title"]')
    title.wait_for()
    title.fill("Build Datasette, edited")

    metadata = dialog.locator('textarea[name="metadata"]')
    metadata.fill("{")
    dialog.locator(
        ".row-edit-field-validation-error", has_text="Invalid JSON"
    ).wait_for()
    dialog.locator(".row-edit-save").click()
    assert dialog.evaluate("node => node.open")
    assert project_row(datasette_server, 1)["title"] == "Build Datasette"

    metadata.fill('{"ok": true, "edited": true}')
    dialog.locator(
        ".row-edit-field-validation-error", has_text="Invalid JSON"
    ).wait_for(state="hidden")
    dialog.locator('textarea[name="notes"]').fill("Edited from Playwright")
    asset = dialog.locator(".playwright-asset-picker")
    asset.wait_for()
    assert asset.get_attribute("data-mode") == "edit"
    asset.locator(".playwright-asset-select").click()

    dialog.locator(".row-edit-save").click()
    page.locator(".row-mutation-status", has_text="Updated row 1").wait_for()
    row = page.locator('tr[data-row="1"]')
    assert "Build Datasette, edited" in row.inner_text()

    data = project_row(datasette_server, 1)
    assert data["title"] == "Build Datasette, edited"
    assert data["metadata"] == '{"ok": true, "edited": true}'
    assert data["logo"] == "asset-from-plugin"
    assert data["notes"] == "Edited from Playwright"


@pytest.mark.playwright
def test_edit_row_binary_control_shows_size_and_image_preview(page, datasette_server):
    page.goto(f"{datasette_server}data/binary_files")
    page.locator('tr[data-row="1"] button[data-row-action="edit"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
    raw_control = dialog.locator('.row-edit-binary-control[data-column="data"]')
    raw_control.wait_for()
    assert (
        raw_control.locator(".row-edit-binary-size").inner_text() == "Binary: 4 bytes"
    )
    assert raw_control.locator(".row-edit-binary-preview img").count() == 0
    assert dialog.locator('textarea[name="data"]').count() == 0
    assert dialog.locator('input[type="hidden"][name="data"]').count() == 1

    dialog.locator(".row-edit-cancel").click()
    page.locator('tr[data-row="2"] button[data-row-action="edit"]').click()

    image_control = dialog.locator('.row-edit-binary-control[data-column="data"]')
    image_control.wait_for()
    assert (
        image_control.locator(".row-edit-binary-size").inner_text()
        == f"Binary: {len(PNG_1X1_BYTES)} bytes"
    )
    image = image_control.locator(".row-edit-binary-preview img")
    image.wait_for()
    assert image.get_attribute("src").startswith("blob:")


@pytest.mark.playwright
def test_edit_row_binary_control_replaces_blob_from_file(page, datasette_server):
    replacement = b"Replacement \x00 bytes"

    page.goto(f"{datasette_server}data/binary_files")
    page.locator('tr[data-row="1"] button[data-row-action="edit"]').click()

    dialog = page.locator("#row-edit-dialog")
    binary_control = dialog.locator('.row-edit-binary-control[data-column="data"]')
    binary_control.wait_for()
    binary_control.locator('input[type="file"]').set_input_files(
        {
            "name": "replacement.bin",
            "mimeType": "application/octet-stream",
            "buffer": replacement,
        }
    )
    wait_for_binary_control_file(binary_control, len(replacement), "replacement.bin")

    dialog.locator(".row-edit-save").click()
    page.locator(".row-mutation-status", has_text="Updated row 1").wait_for()
    assert binary_file_blob(datasette_server, 1) == replacement


@pytest.mark.playwright
def test_edit_row_binary_control_handles_null_blob(page, datasette_server):
    replacement = b"From NULL"

    page.goto(f"{datasette_server}data/binary_files")
    page.locator('tr[data-row="3"] button[data-row-action="edit"]').click()

    dialog = page.locator("#row-edit-dialog")
    binary_control = dialog.locator('.row-edit-binary-control[data-column="data"]')
    binary_control.wait_for()
    assert (
        binary_control.locator(".row-edit-binary-size").inner_text() == "No binary data"
    )
    binary_control.locator('input[type="file"]').set_input_files(
        {
            "name": "from-null.bin",
            "mimeType": "application/octet-stream",
            "buffer": replacement,
        }
    )
    wait_for_binary_control_file(binary_control, len(replacement), "from-null.bin")

    dialog.locator(".row-edit-save").click()
    page.locator(".row-mutation-status", has_text="Updated row 3").wait_for()
    assert binary_file_blob(datasette_server, 3) == replacement


@pytest.mark.playwright
def test_insert_row_binary_control_accepts_pasted_file(page, datasette_server):
    pasted = b"Pasted \x00 bytes"

    page.goto(f"{datasette_server}data/binary_files")
    page.locator('button[data-table-action="insert-row"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
    dialog.locator('input[name="name"]').fill("Pasted bytes")
    binary_control = dialog.locator('.row-edit-binary-control[data-column="data"]')
    binary_control.wait_for()
    binary_control.evaluate(
        """(node, bytes) => {
          const transfer = new DataTransfer();
          transfer.items.add(
            new File([new Uint8Array(bytes)], "pasted.bin", {
              type: "application/octet-stream"
            })
          );
          const event = new Event("paste", { bubbles: true, cancelable: true });
          Object.defineProperty(event, "clipboardData", { value: transfer });
          node.dispatchEvent(event);
        }""",
        list(pasted),
    )
    wait_for_binary_control_file(binary_control, len(pasted), "pasted.bin")

    dialog.locator(".row-edit-save").click()
    page.locator(".row-mutation-status", has_text="Inserted row 4").wait_for()
    assert binary_file_blob(datasette_server, 4) == pasted


@pytest.mark.playwright
def test_delete_row_flow_removes_row(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator('tr[data-row="1"] button[data-row-action="delete"]').click()

    dialog = page.locator("#row-delete-dialog")
    dialog.wait_for()
    assert "Delete row 1" in dialog.inner_text()
    dialog.locator(".row-delete-confirm").click()

    page.locator(".row-mutation-status", has_text="Deleted row 1").wait_for()
    page.locator('tr[data-row="1"]').wait_for(state="detached")
    assert project_rows(datasette_server, id=1) == []
