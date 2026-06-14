import json
from pathlib import Path
import subprocess
import textwrap

STATIC_DIR = Path(__file__).resolve().parents[1] / "datasette" / "static"


def test_datasette_manager_make_column_field():
    script = textwrap.dedent("""
        const fs = require("fs");
        const vm = require("vm");
        const datasetteManagerJs = __DATASETTE_MANAGER_JS__;

        const documentListeners = {};
        global.CustomEvent = class {
          constructor(name, options) {
            this.type = name;
            this.detail = options ? options.detail : undefined;
          }
        };
        global.document = {
          addEventListener(name, callback) {
            documentListeners[name] = documentListeners[name] || [];
            documentListeners[name].push(callback);
          },
          dispatchEvent(event) {
            for (const callback of documentListeners[event.type] || []) {
              callback(event);
            }
          },
        };
        global.window = { datasetteVersion: "test" };

        vm.runInThisContext(
          fs.readFileSync(datasetteManagerJs, "utf8"),
          { filename: "datasette-manager.js" }
        );
        for (const callback of documentListeners.DOMContentLoaded || []) {
          callback();
        }

        window.__DATASETTE__.registerPlugin("declines", {
          makeColumnField() {
            return null;
          },
        });
        window.__DATASETTE__.registerPlugin("handles", {
          makeColumnField(context) {
            if (context.columnType.type !== "demo") {
              return null;
            }
            return { inputType: "textarea" };
          },
        });

        const control = window.__DATASETTE__.makeColumnField({
          column: "body",
          columnType: { type: "demo", config: null },
        });
        console.log(JSON.stringify(control));
    """).replace(
        "__DATASETTE_MANAGER_JS__",
        json.dumps(str(STATIC_DIR / "datasette-manager.js")),
    )
    result = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "pluginName": "handles",
        "inputType": "textarea",
    }


def test_table_plugin_column_field_api():
    script = textwrap.dedent("""
        const fs = require("fs");
        const vm = require("vm");
        const tableJs = __TABLE_JS__;

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
            this.readOnly = false;
            this.dispatchedEvents = [];
            this.eventListeners = {};
            this.validationMessage = "";
            this.hidden = false;
            this.textContent = "";
            this.className = "";
            this.classList = {
              add: (...names) => {
                const classes = new Set(this.className.split(/\\s+/).filter(Boolean));
                for (const name of names) {
                  classes.add(name);
                }
                this.className = Array.from(classes).join(" ");
              },
              remove: (...names) => {
                const removeNames = new Set(names);
                this.className = this.className
                  .split(/\\s+/)
                  .filter((name) => name && !removeNames.has(name))
                  .join(" ");
              },
              contains: (name) => this.className.split(/\\s+/).includes(name),
            };
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
            this.dispatchedEvents.push(event.type);
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

        vm.runInThisContext(fs.readFileSync(tableJs, "utf8"), {
          filename: "table.js",
        });

        const context = columnFormControlContext(
          "logo",
          "df-old",
          false,
          { type: "file", config: null },
          {
            mode: "edit",
            hasSqliteDefault: true,
            sqliteDefaultExpression: "lower(hex(randomblob(4)))",
            useSqliteDefaultInitially: true,
          }
        );
        if ("defaultValue" in context) {
          throw new Error("context should not expose defaultValue");
        }
        if (!context.hasSqliteDefault) {
          throw new Error("context.hasSqliteDefault should be true");
        }
        if (context.sqliteDefaultExpression !== "lower(hex(randomblob(4)))") {
          throw new Error("context.sqliteDefaultExpression was not set");
        }
        if (JSON.stringify(context.columnType) !== '{"type":"file","config":{}}') {
          throw new Error("context.columnType should expose type and object config");
        }
        if ("value" in context || "valueType" in context) {
          throw new Error("context should not expose value state");
        }
        if ("initialValue" in context || "initialValueKind" in context) {
          throw new Error("context should not expose initial value state");
        }

        const control = new FakeElement("input");
        control.name = "logo";
        control.value = "df-old";
        control.dataset.initialValue = "df-old";
        control.dataset.initialValueKind = "string";
        control.dataset.currentValueKind = "string";
        control.dataset.useSqliteDefault = "1";
        control.disabled = true;

        const field = createColumnFieldApi({
          id: "row-edit-field-0",
          labelId: "row-edit-field-label-0",
          descriptionId: "row-edit-field-meta-0",
          control,
          meta: new FakeElement("span"),
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
              field.root.appendChild(document.createElement("button"));
            },
          },
          field
        );
        if (renderArgumentCount !== 1 || renderField !== field) {
          throw new Error("plugin render should receive the field object only");
        }
        if (field.root !== wrapper) {
          throw new Error("field.root should be the plugin wrapper");
        }

        field.setValue(null);
        if (field.getValue() !== null) {
          throw new Error("field.setValue(null) should round-trip as null");
        }
        if (field.isUsingSqliteDefault()) {
          throw new Error("field.setValue() should stop using the SQLite default");
        }
        if (control.dataset.currentValueKind !== "null") {
          throw new Error("null values should update currentValueKind");
        }

        field.setValue("df-new");
        if (field.getValue() !== "df-new") {
          throw new Error("field.setValue() should update the current value");
        }
        if (field.getInitialValue() !== "df-old") {
          throw new Error("field.getInitialValue() should remain stable");
        }
        if (!field.hasChanged()) {
          throw new Error("field.hasChanged() should notice plugin value changes");
        }
        if (control.dispatchedEvents.join(",") !== "input,change,input,change") {
          throw new Error(`Unexpected dispatched events: ${control.dispatchedEvents}`);
        }
        try {
          field.setValue({ id: "df-object" });
          throw new Error("field.setValue() should reject object values");
        } catch (error) {
          if (!String(error.message).includes("serialize objects")) {
            throw error;
          }
        }

        field.setValidity("Pick a file");
        if (control.validationMessage !== "Pick a file") {
          throw new Error("field.setValidity() should set custom validity");
        }
        if (control.getAttribute("aria-invalid") !== "true") {
          throw new Error("field.setValidity() should set aria-invalid");
        }
        if (!field.validationMessageElement || field.validationMessageElement.hidden) {
          throw new Error("field.setValidity() should show a field validation message");
        }
        field.clearValidity();
        if (control.validationMessage !== "" || control.getAttribute("aria-invalid") !== null) {
          throw new Error("field.clearValidity() should clear custom validity");
        }

        field.useSqliteDefault();
        if (!field.isUsingSqliteDefault() || !control.disabled) {
          throw new Error("field.useSqliteDefault() should mark and disable control");
        }

        process.stdout.write("ok");
    """).replace("__TABLE_JS__", json.dumps(str(STATIC_DIR / "table.js")))
    result = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok"


def test_builtin_json_column_field_validation():
    script = textwrap.dedent("""
        const fs = require("fs");
        const vm = require("vm");
        const tableJs = __TABLE_JS__;

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

        vm.runInThisContext(fs.readFileSync(tableJs, "utf8"), {
          filename: "table.js",
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
        if (!pluginControl || pluginControl.inputType !== "textarea") {
          throw new Error("JSON column plugin should request a textarea");
        }

        const context = columnFormControlContext(
          "metadata",
          '{"ok": true}',
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
    """).replace("__TABLE_JS__", json.dumps(str(STATIC_DIR / "table.js")))
    result = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok"
