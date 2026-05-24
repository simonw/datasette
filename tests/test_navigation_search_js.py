import json
from pathlib import Path
import subprocess
import textwrap

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "datasette" / "static"


def test_navigation_search_tracks_and_renders_recent_items():
    script = textwrap.dedent("""
        const fs = require("fs");
        const vm = require("vm");
        const navigationSearchJs = __NAVIGATION_SEARCH_JS__;

        class FakeElement {
          constructor() {
            this.innerHTML = "";
            this.value = "";
            this.dataset = {};
            this.open = false;
          }
          addEventListener() {}
          close() { this.open = false; }
          focus() {}
          querySelector() {
            return { scrollIntoView() {} };
          }
          showModal() { this.open = true; }
        }

        class FakeShadowRoot {
          constructor() {
            this.innerHTML = "";
            this.dialog = new FakeElement();
            this.input = new FakeElement();
            this.results = new FakeElement();
          }
          querySelector(selector) {
            if (selector == "dialog") return this.dialog;
            if (selector == ".search-input") return this.input;
            if (selector == ".results-container") return this.results;
            return new FakeElement();
          }
        }

        global.HTMLElement = class {
          constructor() {
            this.attributes = {};
          }
          attachShadow() {
            this.shadowRoot = new FakeShadowRoot();
            return this.shadowRoot;
          }
          dispatchEvent() {}
          getAttribute(name) {
            return this.attributes[name] || null;
          }
          querySelector() {
            return null;
          }
          setAttribute(name, value) {
            this.attributes[name] = value;
          }
        };
        global.CustomEvent = class {
          constructor(name, options) {
            this.name = name;
            this.options = options;
          }
        };
        global.customElements = {
          registry: new Map(),
          define(name, cls) {
            this.registry.set(name, cls);
          },
        };
        global.document = {
          addEventListener() {},
          activeElement: null,
          createElement() {
            return {
              set textContent(value) {
                this.innerHTML = String(value)
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;");
              },
            };
          },
        };
        global.localStorage = {
          store: {},
          getItem(key) {
            return Object.prototype.hasOwnProperty.call(this.store, key)
              ? this.store[key]
              : null;
          },
          setItem(key, value) {
            this.store[key] = String(value);
          },
          removeItem(key) {
            delete this.store[key];
          },
        };
        global.window = { location: { href: "" } };

        vm.runInThisContext(
          fs.readFileSync(navigationSearchJs, "utf8"),
          { filename: "navigation-search.js" }
        );

        const Component = customElements.registry.get("navigation-search");
        const element = new Component();
        const items = Array.from({ length: 6 }, (_, index) => ({
          name: `Item ${index + 1}`,
          url: `/item-${index + 1}`,
          type: "table",
          description: "Table",
        }));
        items[5].name = "content: recent_datasette_releases";
        items[5].display_name = "Recent Datasette releases";

        for (const item of items) {
          element.matches = [item];
          element.renderedMatches = [item];
          element.selectedIndex = 0;
          element.selectCurrentItem();
        }

        const stored = JSON.parse(
          Object.values(localStorage.store).find((value) => value.includes("/item-6"))
        );
        if (stored.length !== 5) {
          throw new Error(`Expected 5 recent items, got ${stored.length}`);
        }
        if (stored[0].url !== "/item-6" || stored[4].url !== "/item-2") {
          throw new Error(`Unexpected recent order: ${JSON.stringify(stored)}`);
        }
        if (stored[0].display_name !== "Recent Datasette releases") {
          throw new Error(`Missing display_name in recent item: ${JSON.stringify(stored[0])}`);
        }

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
        element.shadowRoot.input.value = "";
        element.renderResults();

        const html = element.shadowRoot.results.innerHTML;
        if (!html.includes("Recent")) {
          throw new Error(`Missing Recent heading: ${html}`);
        }
        if (!html.includes("Recent Datasette releases") || !html.includes("Item 5")) {
          throw new Error(`Missing recent items: ${html}`);
        }
        if (!html.includes("content: recent_datasette_releases")) {
          throw new Error(`Missing canonical item name for display_name item: ${html}`);
        }
        if (!html.includes("Item 4") || !html.includes("Item 2")) {
          throw new Error(`Expected all stored recent items in empty state: ${html}`);
        }
        if (html.includes("Other")) {
          throw new Error(`Rendered non-recent item in empty state: ${html}`);
        }
        if (!html.includes("Clear recent")) {
          throw new Error(`Missing Clear recent control: ${html}`);
        }

        element.clearRecentItems();
        if (localStorage.getItem(element.recentItemsStorageKey()) !== null) {
          throw new Error("Expected recent items to be cleared");
        }
        element.renderResults();
        if (element.shadowRoot.results.innerHTML.includes("Clear recent")) {
          throw new Error("Clear recent should disappear after clearing");
        }

        process.stdout.write(JSON.stringify(stored));
        """).replace(
        "__NAVIGATION_SEARCH_JS__",
        json.dumps(str(STATIC_DIR / "navigation-search.js")),
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert [item["url"] for item in json.loads(result.stdout)] == [
        "/item-6",
        "/item-5",
        "/item-4",
        "/item-3",
        "/item-2",
    ]
    assert json.loads(result.stdout)[0]["display_name"] == "Recent Datasette releases"


def test_navigation_search_renders_jump_sections_from_javascript_plugins():
    script = (
        textwrap.dedent("""
        const fs = require("fs");
        const vm = require("vm");
        const datasetteManagerJs = __DATASETTE_MANAGER_JS__;
        const navigationSearchJs = __NAVIGATION_SEARCH_JS__;

        const documentListeners = {};

        class FakeElement {
          constructor(tagName = "div", parent = null) {
            this._innerHTML = "";
            this.value = "";
            this.dataset = {};
            this.open = false;
            this.parent = parent;
            this.tagName = tagName.toUpperCase();
          }
          set textContent(value) {
            this.innerHTML = String(value)
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;");
          }
          get innerHTML() {
            return this._innerHTML;
          }
          set innerHTML(value) {
            this._innerHTML = String(value);
            if (this.parent) {
              this.parent._innerHTML += this._innerHTML;
            }
          }
          addEventListener() {}
          appendChild(child) {
            this._innerHTML += child.innerHTML || "";
            return child;
          }
          close() { this.open = false; }
          focus() {}
          querySelector(selector) {
            if (selector.startsWith("[data-jump-section-index=")) {
              return new FakeElement("div", this);
            }
            return { scrollIntoView() {} };
          }
          showModal() { this.open = true; }
        }

        class FakeShadowRoot {
          constructor() {
            this.innerHTML = "";
            this.dialog = new FakeElement("dialog");
            this.input = new FakeElement("input");
            this.results = new FakeElement("div");
          }
          querySelector(selector) {
            if (selector == "dialog") return this.dialog;
            if (selector == ".search-input") return this.input;
            if (selector == ".results-container") return this.results;
            return new FakeElement();
          }
        }

        global.HTMLElement = class {
          constructor() {
            this.attributes = {};
          }
          attachShadow() {
            this.shadowRoot = new FakeShadowRoot();
            return this.shadowRoot;
          }
          dispatchEvent() {}
          getAttribute(name) {
            return this.attributes[name] || null;
          }
          querySelector() {
            return null;
          }
          setAttribute(name, value) {
            this.attributes[name] = value;
          }
        };
        global.CustomEvent = class {
          constructor(name, options) {
            this.name = name;
            this.type = name;
            this.detail = options ? options.detail : undefined;
          }
        };
        global.customElements = {
          registry: new Map(),
          define(name, cls) {
            this.registry.set(name, cls);
          },
        };
        global.document = {
          addEventListener(name, callback) {
            documentListeners[name] = documentListeners[name] || [];
            documentListeners[name].push(callback);
          },
          activeElement: null,
          createElement(tagName) {
            return new FakeElement(tagName);
          },
          dispatchEvent(event) {
            for (const callback of documentListeners[event.type] || []) {
              callback(event);
            }
          },
          querySelectorAll() {
            return [];
          },
        };
        global.localStorage = {
          getItem() { return null; },
          setItem() {},
          removeItem() {},
        };
        global.window = { datasetteVersion: "test", location: { href: "" } };

        vm.runInThisContext(
          fs.readFileSync(datasetteManagerJs, "utf8"),
          { filename: "datasette-manager.js" }
        );
        for (const callback of documentListeners.DOMContentLoaded || []) {
          callback();
        }
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
                  ].join('');
                },
              },
            ];
          },
        });

        vm.runInThisContext(
          fs.readFileSync(navigationSearchJs, "utf8"),
          { filename: "navigation-search.js" }
        );

        const Component = customElements.registry.get("navigation-search");
        const element = new Component();
        element.shadowRoot.input.value = "";
        element.renderResults();

        const html = element.shadowRoot.results.innerHTML;
        if (!html.includes("Start a new agent chat")) {
          throw new Error(`Missing jump section content: ${html}`);
        }
        process.stdout.write("ok");
        """)
        .replace(
            "__DATASETTE_MANAGER_JS__",
            json.dumps(str(STATIC_DIR / "datasette-manager.js")),
        )
        .replace(
            "__NAVIGATION_SEARCH_JS__",
            json.dumps(str(STATIC_DIR / "navigation-search.js")),
        )
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("ok")
