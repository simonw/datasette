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
