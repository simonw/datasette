import socket
import subprocess
import sys
import time

import httpx
import pytest

from datasette.fixtures import write_fixture_database


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
    db_path = tmp_path / "fixtures.db"
    write_fixture_database(str(db_path))
    port = find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "datasette",
            str(db_path),
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


@pytest.mark.playwright
def test_datasette_homepage_contains_datasette(page, datasette_server):
    page.goto(datasette_server)
    assert "Datasette" in page.locator("body").inner_text()


def load_edit_tools(page, datasette_server):
    page.goto(datasette_server)
    page.evaluate("""
    () => {
      window._datasetteTableData = {
        database: "data",
        table: "projects",
        tableUrl: "/data/projects",
      };
    }
    """)
    page.add_script_tag(url=f"{datasette_server}-/static/edit-tools.js")


@pytest.mark.playwright
def test_navigation_search_tracks_and_renders_recent_items(page, datasette_server):
    page.goto(datasette_server)
    result = page.evaluate("""
    async () => {
      await customElements.whenDefined("navigation-search");
      const element = document.querySelector("navigation-search");
      const key = element.recentItemsStorageKey();
      localStorage.removeItem(key);

      const items = Array.from({ length: 6 }, (_, index) => ({
        name: `Item ${index + 1}`,
        url: `/item-${index + 1}`,
        type: "table",
        description: "Table",
      }));
      items[5].name = "content: recent_datasette_releases";
      items[5].display_name = "Recent Datasette releases";

      for (const item of items) {
        element.saveRecentItem(item);
      }

      const stored = JSON.parse(localStorage.getItem(key));
      element.matches = [
        items[5],
        items[4],
        {
          name: "Other",
          url: "/other",
          type: "database",
          description: "Database",
        },
      ];
      element.shadowRoot.querySelector(".search-input").value = "";
      element.renderResults();
      const html = element.shadowRoot.querySelector(".results-container").innerHTML;

      element.clearRecentItems();
      const clearedValue = localStorage.getItem(key);
      element.renderResults();
      const htmlAfterClear = element.shadowRoot
        .querySelector(".results-container")
        .innerHTML;

      return { stored, html, clearedValue, htmlAfterClear };
    }
    """)
    assert [item["url"] for item in result["stored"]] == [
        "/item-6",
        "/item-5",
        "/item-4",
        "/item-3",
        "/item-2",
    ]
    assert result["stored"][0]["display_name"] == "Recent Datasette releases"
    assert "Recent" in result["html"]
    assert "Recent Datasette releases" in result["html"]
    assert "Item 5" in result["html"]
    assert "content: recent_datasette_releases" in result["html"]
    assert "Item 4" in result["html"]
    assert "Item 2" in result["html"]
    assert "Other" not in result["html"]
    assert "Clear recent" in result["html"]
    assert result["clearedValue"] is None
    assert "Clear recent" not in result["htmlAfterClear"]


@pytest.mark.playwright
def test_navigation_search_renders_jump_sections_from_javascript_plugins(
    page, datasette_server
):
    page.goto(datasette_server)
    html = page.evaluate("""
    async () => {
      await customElements.whenDefined("navigation-search");
      window.__DATASETTE__.registerPlugin("agent", {
        version: "0.1",
        makeJumpSections() {
          return [
            {
              id: "agent-chat",
              render(node, context) {
                if (!context.navigationSearch) {
                  throw new Error("Expected navigationSearch in render context");
                }
                node.innerHTML = [
                  '<section class="agent-jump-start">',
                  '<button>Start a new agent chat</button>',
                  '</section>',
                ].join("");
              },
            },
          ];
        },
      });

      const element = document.querySelector("navigation-search");
      element.shadowRoot.querySelector(".search-input").value = "";
      element.renderResults();
      return element.shadowRoot.querySelector(".results-container").innerHTML;
    }
    """)
    assert "Start a new agent chat" in html


@pytest.mark.playwright
def test_datasette_manager_make_column_field(page, datasette_server):
    page.goto(datasette_server)
    control = page.evaluate("""
    () => {
      window.__DATASETTE__.registerPlugin("declines", {
        makeColumnField() {
          return;
        },
      });
      window.__DATASETTE__.registerPlugin("handles", {
        makeColumnField(context) {
          if (context.columnType.type !== "demo") {
            return;
          }
          return { useTextarea: true };
        },
      });
      return window.__DATASETTE__.makeColumnField({
        column: "body",
        columnType: { type: "demo", config: null },
      });
    }
    """)
    assert control == {
        "pluginName": "handles",
        "useTextarea": True,
    }


@pytest.mark.playwright
def test_table_plugin_column_field_api(page, datasette_server):
    load_edit_tools(page, datasette_server)
    page.evaluate("""
    () => {
      const assert = (condition, message) => {
        if (!condition) {
          throw new Error(message);
        }
      };

      const context = columnFormControlContext(
        "logo",
        true,
        { type: "file", config: null },
        {
          mode: "edit",
          defaultExpression: "lower(hex(randomblob(4)))",
          useSqliteDefault: true,
        },
      );
      const expectedContextKeys = [
        "mode",
        "database",
        "table",
        "tableUrl",
        "column",
        "columnType",
        "sqliteType",
        "notNull",
        "isPk",
        "defaultExpression",
        "form",
        "dialog",
      ].join(",");
      assert(
        Object.keys(context).join(",") === expectedContextKeys,
        `Unexpected context keys: ${Object.keys(context).join(",")}`,
      );
      assert(
        context.defaultExpression === "lower(hex(randomblob(4)))",
        "context.defaultExpression was not set",
      );
      assert(
        JSON.stringify(context.columnType) === '{"type":"file","config":{}}',
        "context.columnType should expose type and object config",
      );
      assert(
        context.isPk,
        "context.isPk should say whether the column is a primary key",
      );

      const control = document.createElement("input");
      control.name = "logo";
      control.value = "df-old";
      control.dataset.initialValue = "df-old";
      control.dataset.initialValueKind = "string";
      control.dataset.currentValueKind = "string";
      control.dataset.useSqliteDefault = "1";
      control.disabled = true;
      const dispatchedEvents = [];
      control.addEventListener("input", (event) =>
        dispatchedEvents.push(event.type),
      );
      control.addEventListener("change", (event) =>
        dispatchedEvents.push(event.type),
      );

      const field = createColumnFieldApi({
        id: "row-edit-field-0",
        labelId: "row-edit-field-label-0",
        descriptionId: "row-edit-field-meta-0",
        control,
        meta: document.createElement("span"),
        context,
      });

      let renderArgumentCount = null;
      let renderField = null;
      const wrapper = renderColumnField(
        {
          pluginName: "test-plugin",
          render(field) {
            renderArgumentCount = arguments.length;
            renderField = field;
            return document.createElement("button");
          },
        },
        field,
      );
      assert(
        renderArgumentCount === 1 && renderField === field,
        "plugin render should receive the field object only",
      );
      assert(field.root === wrapper, "field.root should be the plugin wrapper");
      assert(
        wrapper.children.length === 1 &&
          wrapper.children[0].nodeName === "BUTTON",
        "plugin render should append returned DOM nodes to field.root",
      );

      field.setValue(null);
      assert(field.getValue() === null, "field.setValue(null) should round-trip as null");
      assert(
        !field.isUsingSqliteDefault(),
        "field.setValue() should stop using the SQLite default",
      );
      assert(
        control.dataset.currentValueKind === "null",
        "null values should update currentValueKind",
      );

      field.setValue("df-new");
      assert(
        field.getValue() === "df-new",
        "field.setValue() should update the current value",
      );
      assert(
        field.getInitialValue() === "df-old",
        "field.getInitialValue() should remain stable",
      );
      assert(
        field.hasChanged(),
        "field.hasChanged() should notice plugin value changes",
      );
      assert(
        dispatchedEvents.length === 0,
        `field.setValue() should not dispatch events: ${dispatchedEvents}`,
      );

      const dirtyRowField = document.createElement("div");
      dirtyRowField.className = "row-edit-field";
      dirtyRowField._datasetteColumnFormField = field;
      const dirtyFields = document.createElement("div");
      dirtyFields.appendChild(dirtyRowField);
      const dirtyState = {
        hasLoaded: true,
        isLoading: false,
        isSaving: false,
        mode: "edit",
        fields: dirtyFields,
        dialog: {
          closeCalled: false,
          close() {
            this.closeCalled = true;
          },
        },
        shouldRestoreFocus: false,
      };
      const confirmMessages = [];
      window.confirm = (message) => {
        confirmMessages.push(message);
        return false;
      };
      assert(
        rowEditDialogHasChanges(dirtyState),
        "row edit dialog should notice changed field values",
      );
      assert(
        !closeRowEditDialogIfConfirmed(dirtyState),
        "dirty row edit dialog should stay open when discard is rejected",
      );
      assert(
        !dirtyState.dialog.closeCalled,
        "dirty row edit dialog should not close when discard is rejected",
      );
      assert(
        confirmMessages[0] === "Discard unsaved changes to this row?",
        `Unexpected discard confirmation: ${confirmMessages[0]}`,
      );
      dirtyState.mode = "insert";
      window.confirm = (message) => {
        confirmMessages.push(message);
        return true;
      };
      assert(
        closeRowEditDialogIfConfirmed(dirtyState),
        "dirty row edit dialog should close when discard is confirmed",
      );
      assert(
        dirtyState.dialog.closeCalled && dirtyState.shouldRestoreFocus,
        "confirmed dirty row edit dialog should close and restore focus",
      );
      assert(
        confirmMessages[1] === "Discard this new row?",
        `Unexpected insert discard confirmation: ${confirmMessages[1]}`,
      );

      const cleanContext = columnFormControlContext("title", false, null, {
        mode: "edit",
      });
      assert(
        cleanContext.defaultExpression === null,
        "context.defaultExpression should be null without a SQLite default",
      );
      const cleanControl = document.createElement("input");
      cleanControl.name = "title";
      cleanControl.value = "clean";
      cleanControl.dataset.initialValue = "clean";
      cleanControl.dataset.initialValueKind = "string";
      cleanControl.dataset.currentValueKind = "string";
      const cleanField = createColumnFieldApi({
        id: "row-edit-field-1",
        labelId: "row-edit-field-label-1",
        descriptionId: "row-edit-field-meta-1",
        control: cleanControl,
        meta: document.createElement("span"),
        context: cleanContext,
      });
      const cleanRowField = document.createElement("div");
      cleanRowField.className = "row-edit-field";
      cleanRowField._datasetteColumnFormField = cleanField;
      const cleanFields = document.createElement("div");
      cleanFields.appendChild(cleanRowField);
      const cleanState = {
        hasLoaded: true,
        isLoading: false,
        isSaving: false,
        mode: "edit",
        fields: cleanFields,
        dialog: {
          closeCalled: false,
          close() {
            this.closeCalled = true;
          },
        },
        shouldRestoreFocus: false,
      };
      confirmMessages.length = 0;
      window.confirm = (message) => {
        confirmMessages.push(message);
        return false;
      };
      assert(
        !rowEditDialogHasChanges(cleanState),
        "row edit dialog should ignore unchanged field values",
      );
      assert(
        closeRowEditDialogIfConfirmed(cleanState),
        "clean row edit dialog should close without confirmation",
      );
      assert(
        cleanState.dialog.closeCalled && cleanState.shouldRestoreFocus,
        "clean row edit dialog should close and restore focus",
      );
      assert(
        confirmMessages.length === 0,
        "clean row edit dialog should not ask for confirmation",
      );

      dirtyState.dialog.closeCalled = false;
      dirtyState.shouldRestoreFocus = false;
      confirmMessages.length = 0;
      field.setValue("<p></p>");
      field.markClean();
      assert(
        !field.hasChanged(),
        "field.markClean() should update the clean baseline",
      );
      assert(
        !rowEditDialogHasChanges(dirtyState),
        "row edit dialog should ignore clean plugin normalization",
      );
      assert(
        closeRowEditDialogIfConfirmed(dirtyState),
        "normalized row edit dialog should close without confirmation",
      );
      assert(
        confirmMessages.length === 0,
        "normalized row edit dialog should not ask for confirmation",
      );
      field.setValue("<p>Hello</p>");
      assert(
        field.hasChanged() && rowEditDialogHasChanges(dirtyState),
        "later plugin value changes should still count as dirty",
      );

      try {
        field.setValue({ id: "df-object" });
        throw new Error("field.setValue() should reject object values");
      } catch (error) {
        if (!String(error.message).includes("serialize objects")) {
          throw error;
        }
      }

      field.setValidity("Pick a file");
      assert(
        control.validationMessage === "Pick a file",
        "field.setValidity() should set custom validity",
      );
      assert(
        control.getAttribute("aria-invalid") === "true",
        "field.setValidity() should set aria-invalid",
      );
      assert(
        field.validationMessageElement && !field.validationMessageElement.hidden,
        "field.setValidity() should show a field validation message",
      );
      field.clearValidity();
      assert(
        control.validationMessage === "" &&
          control.getAttribute("aria-invalid") === null,
        "field.clearValidity() should clear custom validity",
      );

      field.useSqliteDefault();
      assert(
        field.isUsingSqliteDefault() && control.disabled,
        "field.useSqliteDefault() should mark and disable control",
      );
    }
    """)
