import json
from pathlib import Path
import subprocess
import textwrap

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "datasette" / "static"


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
