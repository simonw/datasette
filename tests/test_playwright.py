import json
import socket
import subprocess
import sys
import time

import httpx
import pytest

from datasette.fixtures import write_fixture_database
from datasette.utils.sqlite import sqlite3


def find_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(process, url, timeout=10):
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
    raise AssertionError(f"Timed out waiting for {url}: {last_error}")


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
        insert into projects (title, metadata, logo, notes, score) values
            (
                'Build Datasette',
                '{"ok": true}',
                'asset-original',
                'Initial notes',
                5
            );
        """)
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
                                    "insert-row": True,
                                    "update-row": True,
                                    "delete-row": True,
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


def open_jump_menu(page):
    page.keyboard.press("/")
    page.locator("navigation-search .search-input").wait_for()


@pytest.mark.playwright
def test_datasette_homepage_contains_datasette(page, datasette_server):
    page.goto(datasette_server)
    assert "Datasette" in page.locator("body").inner_text()


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
    dialog.locator('input[name="table"]').fill("playwright_created")
    dialog.locator(".table-create-column-name").nth(1).fill("title")
    dialog.locator(".table-create-add-column").click()
    dialog.locator(".table-create-column-name").nth(2).fill("score")
    dialog.locator(".table-create-column-type").nth(2).select_option("integer")
    dialog.locator(".table-create-add-column").click()
    dialog.locator(".table-create-column-name").nth(3).fill("metadata")
    dialog.locator(".table-create-column-type").nth(3).select_option("integer")
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
    page.goto(f"{datasette_server}data/projects")
    page.locator('button[data-table-action="insert-row"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
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
def test_edit_row_flow_validates_json_and_saves_changes(page, datasette_server):
    page.goto(f"{datasette_server}data/projects")
    page.locator('tr[data-row="1"] button[data-row-action="edit"]').click()

    dialog = page.locator("#row-edit-dialog")
    dialog.wait_for()
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
