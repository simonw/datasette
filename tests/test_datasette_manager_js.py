import json
from pathlib import Path
import subprocess
import textwrap

STATIC_DIR = Path(__file__).resolve().parents[1] / "datasette" / "static"


def test_builtin_json_column_field_validation():
    script = textwrap.dedent("""
        const fs = require("fs");
        const vm = require("vm");
        const editToolsJs = __EDIT_TOOLS_JS__;

        class FakeEvent {
          constructor(type, options) {
            this.type = type;
            this.bubbles = !!(options && options.bubbles);
          }
        }

        class FakeElement {
          constructor(tagName = "div") {
            this.nodeName = tagName.toUpperCase();
            this.nodeType = 1;
            this.children = [];
            this.dataset = {};
            this.attributes = {};
            this.value = "";
            this.name = "";
            this.disabled = false;
            this.hidden = false;
            this.textContent = "";
            this.validationMessage = "";
            this.eventListeners = {};
            this.className = "";
          }
          appendChild(child) {
            this.children.push(child);
            child.parentNode = this;
            return child;
          }
          addEventListener(type, callback) {
            this.eventListeners[type] = this.eventListeners[type] || [];
            this.eventListeners[type].push(callback);
          }
          dispatchEvent(event) {
            event.target = event.target || this;
            for (const callback of this.eventListeners[event.type] || []) {
              callback(event);
            }
            return true;
          }
          setAttribute(name, value) {
            this.attributes[name] = String(value);
          }
          getAttribute(name) {
            return this.attributes[name] || null;
          }
          removeAttribute(name) {
            delete this.attributes[name];
          }
          setCustomValidity(message) {
            this.validationMessage = message;
          }
        }

        global.Event = FakeEvent;
        global.document = {
          addEventListener() {},
          createElement(tagName) {
            return new FakeElement(tagName);
          },
          createTextNode(text) {
            const node = new FakeElement("#text");
            node.textContent = text;
            return node;
          },
        };
        global.location = {
          href: "http://localhost/data/projects",
          pathname: "/data/projects",
          search: "",
        };
        global.window = {
          _datasetteTableData: {
            database: "data",
            table: "projects",
            tableUrl: "/data/projects",
          },
        };

        vm.runInThisContext(fs.readFileSync(editToolsJs, "utf8"), {
          filename: "edit-tools.js",
        });

        const plugins = [];
        registerBuiltinColumnFieldPlugins({
          registerPlugin(name, plugin) {
            plugins.push({ name, plugin });
          },
        });
        const jsonPlugin = plugins.find((entry) => entry.name === "datasette-json-column");
        if (!jsonPlugin) {
          throw new Error("datasette-json-column plugin was not registered");
        }
        const pluginControl = jsonPlugin.plugin.makeColumnField({
          column: "metadata",
          columnType: { type: "json", config: {} },
        });
        if (!pluginControl || pluginControl.useTextarea !== true) {
          throw new Error("JSON column plugin should request a textarea");
        }

        const context = columnFormControlContext(
          "metadata",
          false,
          { type: "json", config: {} },
          { mode: "edit" }
        );
        const control = new FakeElement("textarea");
        control.name = "metadata";
        control.value = '{"ok": true}';
        control.dataset.initialValue = '{"ok": true}';
        control.dataset.initialValueKind = "string";
        control.dataset.currentValueKind = "string";
        const meta = new FakeElement("span");

        const field = createColumnFieldApi({
          id: "row-edit-field-0",
          labelId: "row-edit-field-label-0",
          descriptionId: "row-edit-field-meta-0",
          control,
          meta,
          context,
        });
        renderColumnField(
          Object.assign({ pluginName: "datasette-json-column" }, pluginControl),
          field
        );

        if (control.validationMessage !== "") {
          throw new Error("Initial valid JSON should not be invalid");
        }
        if (control.dataset.initialValueKind !== "string") {
          throw new Error("JSON plugin should keep the original string value kind");
        }
        if (control.dataset.currentValueKind !== "string") {
          throw new Error("JSON plugin should keep the current string value kind");
        }
        if (!field.validationMessageElement || field.validationMessageElement.hidden !== true) {
          throw new Error("JSON validation message should start hidden");
        }

        control.value = "{";
        control.dispatchEvent(new Event("input", { bubbles: true }));
        if (!control.validationMessage.startsWith("Invalid JSON")) {
          throw new Error("Invalid JSON should set a custom validity message");
        }
        if (control.getAttribute("aria-invalid") !== "true") {
          throw new Error("Invalid JSON should set aria-invalid");
        }
        if (field.validationMessageElement.hidden) {
          throw new Error("Invalid JSON should show the validation message");
        }

        control.value = '{"ok": true}';
        control.dispatchEvent(new Event("input", { bubbles: true }));
        if (control.validationMessage !== "") {
          throw new Error("Valid JSON should clear the custom validity message");
        }
        if (control.getAttribute("aria-invalid") !== null) {
          throw new Error("Valid JSON should clear aria-invalid");
        }
        if (!field.validationMessageElement.hidden) {
          throw new Error("Valid JSON should hide the validation message");
        }

        control.dataset.initialValue = '{"ok":';
        control.value = '{"ok": true}';
        const values = collectRowFormValues({
          mode: "edit",
          fields: {
            querySelectorAll() {
              return [control];
            },
          },
        });
        if (values.metadata !== '{"ok": true}') {
          throw new Error("Corrected JSON should be submitted as a string value");
        }

        process.stdout.write("ok");
    """).replace("__EDIT_TOOLS_JS__", json.dumps(str(STATIC_DIR / "edit-tools.js")))
    result = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok"
