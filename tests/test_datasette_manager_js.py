import json
from pathlib import Path
import subprocess
import textwrap

STATIC_DIR = Path(__file__).resolve().parents[1] / "datasette" / "static"


def test_table_plugin_column_field_api():
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

        vm.runInThisContext(fs.readFileSync(editToolsJs, "utf8"), {
          filename: "edit-tools.js",
        });

        const context = columnFormControlContext(
          "logo",
          true,
          { type: "file", config: null },
          {
            mode: "edit",
            defaultExpression: "lower(hex(randomblob(4)))",
            useSqliteDefault: true,
          }
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
        if (Object.keys(context).join(",") !== expectedContextKeys) {
          throw new Error(`Unexpected context keys: ${Object.keys(context).join(",")}`);
        }
        if (context.defaultExpression !== "lower(hex(randomblob(4)))") {
          throw new Error("context.defaultExpression was not set");
        }
        if (JSON.stringify(context.columnType) !== '{"type":"file","config":{}}') {
          throw new Error("context.columnType should expose type and object config");
        }
        if (!context.isPk) {
          throw new Error("context.isPk should say whether the column is a primary key");
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
              return document.createElement("button");
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
        if (wrapper.children.length !== 1 || wrapper.children[0].nodeName !== "BUTTON") {
          throw new Error("plugin render should append returned DOM nodes to field.root");
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
        if (control.dispatchedEvents.length !== 0) {
          throw new Error(`field.setValue() should not dispatch events: ${control.dispatchedEvents}`);
        }

        const dirtyRowField = new FakeElement("div");
        dirtyRowField._datasetteColumnFormField = field;
        const dirtyState = {
          hasLoaded: true,
          isLoading: false,
          isSaving: false,
          mode: "edit",
          fields: {
            querySelectorAll(selector) {
              return selector === ".row-edit-field" ? [dirtyRowField] : [];
            },
          },
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
        if (!rowEditDialogHasChanges(dirtyState)) {
          throw new Error("row edit dialog should notice changed field values");
        }
        if (closeRowEditDialogIfConfirmed(dirtyState)) {
          throw new Error("dirty row edit dialog should stay open when discard is rejected");
        }
        if (dirtyState.dialog.closeCalled) {
          throw new Error("dirty row edit dialog should not close when discard is rejected");
        }
        if (confirmMessages[0] !== "Discard unsaved changes to this row?") {
          throw new Error(`Unexpected discard confirmation: ${confirmMessages[0]}`);
        }
        dirtyState.mode = "insert";
        window.confirm = (message) => {
          confirmMessages.push(message);
          return true;
        };
        if (!closeRowEditDialogIfConfirmed(dirtyState)) {
          throw new Error("dirty row edit dialog should close when discard is confirmed");
        }
        if (!dirtyState.dialog.closeCalled || !dirtyState.shouldRestoreFocus) {
          throw new Error("confirmed dirty row edit dialog should close and restore focus");
        }
        if (confirmMessages[1] !== "Discard this new row?") {
          throw new Error(`Unexpected insert discard confirmation: ${confirmMessages[1]}`);
        }

        const cleanContext = columnFormControlContext(
          "title",
          false,
          null,
          { mode: "edit" }
        );
        if (cleanContext.defaultExpression !== null) {
          throw new Error("context.defaultExpression should be null without a SQLite default");
        }
        const cleanControl = new FakeElement("input");
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
          meta: new FakeElement("span"),
          context: cleanContext,
        });
        const cleanRowField = new FakeElement("div");
        cleanRowField._datasetteColumnFormField = cleanField;
        const cleanState = {
          hasLoaded: true,
          isLoading: false,
          isSaving: false,
          mode: "edit",
          fields: {
            querySelectorAll(selector) {
              return selector === ".row-edit-field" ? [cleanRowField] : [];
            },
          },
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
        if (rowEditDialogHasChanges(cleanState)) {
          throw new Error("row edit dialog should ignore unchanged field values");
        }
        if (!closeRowEditDialogIfConfirmed(cleanState)) {
          throw new Error("clean row edit dialog should close without confirmation");
        }
        if (!cleanState.dialog.closeCalled || !cleanState.shouldRestoreFocus) {
          throw new Error("clean row edit dialog should close and restore focus");
        }
        if (confirmMessages.length !== 0) {
          throw new Error("clean row edit dialog should not ask for confirmation");
        }

        dirtyState.dialog.closeCalled = false;
        dirtyState.shouldRestoreFocus = false;
        confirmMessages.length = 0;
        field.setValue("<p></p>");
        field.markClean();
        if (field.hasChanged()) {
          throw new Error("field.markClean() should update the clean baseline");
        }
        if (rowEditDialogHasChanges(dirtyState)) {
          throw new Error("row edit dialog should ignore clean plugin normalization");
        }
        if (!closeRowEditDialogIfConfirmed(dirtyState)) {
          throw new Error("normalized row edit dialog should close without confirmation");
        }
        if (confirmMessages.length !== 0) {
          throw new Error("normalized row edit dialog should not ask for confirmation");
        }
        field.setValue("<p>Hello</p>");
        if (!field.hasChanged() || !rowEditDialogHasChanges(dirtyState)) {
          throw new Error("later plugin value changes should still count as dirty");
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
    """).replace("__EDIT_TOOLS_JS__", json.dumps(str(STATIC_DIR / "edit-tools.js")))
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
