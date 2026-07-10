// datasette-sql-editor: ESM primitives for embedding Datasette's SQL editor.
//
// This is the single source of truth for Datasette's CodeMirror setup. The IIFE
// entry point (cm-editor.js, served as cm-editor.bundle.js for Datasette's own
// pages) is a thin consumer of these primitives, and plugin authors can import
// this module directly from /-/static/datasette-sql-editor.js to get a SQL
// editor that shares ONE CodeMirror instance per page (no duplicate
// @codemirror/state bug).
//
// Built by rollup.config.mjs into datasette-sql-editor.bundle.js.

import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLineGutter,
  highlightSpecialChars,
  drawSelection,
  dropCursor,
  rectangularSelection,
  crosshairCursor,
  highlightActiveLine,
  tooltips,
} from "@codemirror/view";
import { EditorState, Compartment, Annotation, Prec } from "@codemirror/state";
import {
  foldGutter,
  indentOnInput,
  syntaxHighlighting,
  defaultHighlightStyle,
  bracketMatching,
  foldKeymap,
} from "@codemirror/language";
import { history, defaultKeymap, historyKeymap } from "@codemirror/commands";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import {
  closeBrackets,
  autocompletion,
  closeBracketsKeymap,
  completionKeymap,
} from "@codemirror/autocomplete";
import { lintKeymap } from "@codemirror/lint";
import { sql, SQLDialect } from "@codemirror/lang-sql";

// A curated variation of SQLite from lang-sql:
// https://github.com/codemirror/lang-sql/blob/ebf115fffdbe07f91465ccbd82868c587f8182bc/src/sql.ts#L231
export const SQLiteDialect = SQLDialect.define({
  // Based on https://www.sqlite.org/lang_keywords.html, restricted to likely
  // keywords used in select queries.
  // https://github.com/simonw/datasette/pull/1893#issuecomment-1316401895:
  keywords:
    "and as asc between by case cast count current_date current_time current_timestamp desc distinct each else escape except exists explain filter first for from full generated group having if in index inner intersect into isnull join last left like limit not null or order outer over pragma primary query raise range regexp right rollback row select set table then to union unique using values view virtual when where",
  // https://www.sqlite.org/datatype3.html
  types: "null integer real text blob",
  builtin: "",
  operatorChars: "*+-%<>!=&|/~",
  identifierQuotes: '`"',
  specialVar: "@:?$",
  caseInsensitiveIdentifiers: true,
});

// Annotation used to tag host-originated changes (e.g. a ProseMirror/collab host
// pushing edits into the editor, or the exported `value` setter). Changes tagged
// with this annotation do NOT re-fire onChange, so hosts can suppress the echo of
// their own edits. Mirrors datasette-paper's `fromPM` pattern.
export const hostChange = Annotation.define();

// Builds the sql() language extension from a {schema, defaultTable, defaultSchema}
// conf object. Undefined fields are fine - lang-sql ignores them.
function sqlExtension(conf = {}) {
  return sql({
    dialect: SQLiteDialect,
    schema: conf.schema,
    defaultTable: conf.defaultTable,
    defaultSchema: conf.defaultSchema,
  });
}

// Replicates codemirror's basicSetup (node_modules/codemirror/dist/index.js) as a
// plain array so we can drop the undo history when `withHistory` is false. When
// history is off we optionally forward Mod-z / Mod-y / Mod-Shift-z to the host so
// an external undo stack (ProseMirror, collab) can own undo/redo.
function baseSetup(withHistory, onHostUndo, onHostRedo) {
  const setup = [
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightSpecialChars(),
    foldGutter(),
    drawSelection(),
    dropCursor(),
    EditorState.allowMultipleSelections.of(true),
    indentOnInput(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    bracketMatching(),
    closeBrackets(),
    autocompletion(),
    rectangularSelection(),
    crosshairCursor(),
    highlightActiveLine(),
    highlightSelectionMatches(),
  ];
  const bindings = [
    ...closeBracketsKeymap,
    ...defaultKeymap,
    ...searchKeymap,
    ...foldKeymap,
    ...completionKeymap,
    ...lintKeymap,
  ];
  if (withHistory) {
    setup.push(history());
    bindings.push(...historyKeymap);
  } else {
    if (onHostUndo) {
      bindings.push({
        key: "Mod-z",
        preventDefault: true,
        run: () => {
          onHostUndo();
          return true;
        },
      });
    }
    if (onHostRedo) {
      bindings.push(
        {
          key: "Mod-y",
          mac: "Mod-Shift-z",
          preventDefault: true,
          run: () => {
            onHostRedo();
            return true;
          },
        },
        {
          key: "Mod-Shift-z",
          preventDefault: true,
          run: () => {
            onHostRedo();
            return true;
          },
        },
      );
    }
  }
  setup.push(keymap.of(bindings));
  return setup;
}

// createSqlEditor(parent, opts) -> handle
//
// opts:
//   doc            initial document string (default "")
//   schema         lang-sql SQLNamespace for autocomplete
//   defaultTable   unqualified-column default table
//   defaultSchema  default schema/attached-database name
//   history        include CM undo history (default true); false forwards
//                  undo/redo to onHostUndo/onHostRedo
//   onHostUndo     called on Mod-z when history is false
//   onHostRedo     called on Mod-y / Mod-Shift-z when history is false
//   extensions     extra CodeMirror extensions to append (default [])
//   fixedTooltips  use position:"fixed" tooltips for overflow-clipped containers
//   onChange       called (update) on user edits; host-annotated changes are
//                  suppressed
//   onSubmit       called (view) on Mod-Enter / Shift-Enter (highest precedence)
//   onEscape       called (view) on Escape
//   lineWrapping   soft-wrap long lines (default true)
//
// handle: {view, updateSchema(conf), destroy(), get value(), set value(v)}
export function createSqlEditor(parent, opts = {}) {
  const {
    doc = "",
    schema,
    defaultTable,
    defaultSchema,
    history: withHistory = true,
    onHostUndo,
    onHostRedo,
    extensions = [],
    fixedTooltips = false,
    onChange,
    onSubmit,
    onEscape,
    lineWrapping = true,
  } = opts;

  const sqlCompartment = new Compartment();

  // Highest-precedence keymap so submit/escape win over the basic keymap.
  const priorityBindings = [];
  if (onSubmit) {
    const runSubmit = () => {
      onSubmit(view);
      return true;
    };
    priorityBindings.push(
      { key: "Mod-Enter", run: runSubmit },
      { key: "Shift-Enter", run: runSubmit },
    );
  }
  if (onEscape) {
    priorityBindings.push({
      key: "Escape",
      run: () => {
        onEscape(view);
        return true;
      },
    });
  }

  const editorExtensions = [
    Prec.highest(keymap.of(priorityBindings)),
    ...baseSetup(withHistory, onHostUndo, onHostRedo),
    lineWrapping ? EditorView.lineWrapping : [],
    fixedTooltips ? tooltips({ position: "fixed" }) : [],
    sqlCompartment.of(sqlExtension({ schema, defaultTable, defaultSchema })),
    onChange
      ? EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          // Suppress echoes of host-originated changes.
          if (update.transactions.some((tr) => tr.annotation(hostChange))) {
            return;
          }
          onChange(update);
        })
      : [],
    ...extensions,
  ];

  let view = new EditorView({
    doc,
    extensions: editorExtensions,
    ...(parent ? { parent } : {}),
  });

  return {
    view,
    // Swap out the schema/defaultTable/defaultSchema used for autocomplete after
    // the editor has been created.
    // https://codemirror.net/examples/config/#dynamic-configuration
    updateSchema(conf) {
      view.dispatch({
        effects: sqlCompartment.reconfigure(sqlExtension(conf)),
      });
    },
    destroy() {
      view.destroy();
    },
    get value() {
      return view.state.doc.toString();
    },
    // Host-originated: tagged with hostChange so it does not re-fire onChange.
    set value(newValue) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: newValue },
        annotations: hostChange.of(true),
      });
    },
  };
}

// Maps ticket 05's neutral editor-schema shape
//   {tables: [{name, view: bool, columns: [{name, type}]}]}
// to a lang-sql SQLNamespace of Completion objects. Kept identical to
// _editor_schema() / _column_completion() in datasette/views/query_helpers.py so
// server-inlined and client-fetched schemas behave the same.
function columnCompletion(name, type) {
  const completion = { label: name, type: "property", boost: 10 };
  if (type) {
    completion.detail = type;
  }
  return completion;
}

export function schemaFromTables(tables) {
  const schema = {};
  for (const table of tables || []) {
    const completions = (table.columns || []).map((column) =>
      columnCompletion(column.name, column.type),
    );
    if (table.view) {
      schema[table.name] = {
        self: { label: table.name, type: "class", detail: "view" },
        children: completions,
      };
    } else {
      schema[table.name] = completions;
    }
  }
  return schema;
}

// datasetteSchema(baseUrl, database) -> Promise<SQLNamespace>
//
// Fetches GET {baseUrl}/{database}/-/editor-schema.json (ticket 05) and maps the
// neutral payload to a lang-sql SQLNamespace ready to pass as opts.schema /
// updateSchema({schema}). baseUrl is Datasette's base_url (may be "" or "/" or a
// mount prefix). Throws a descriptive Error on a non-200 response.
export async function datasetteSchema(baseUrl, database) {
  const base = (baseUrl || "").replace(/\/+$/, "");
  const url = `${base}/${encodeURIComponent(database)}/-/editor-schema.json`;
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(
      `datasetteSchema: failed to fetch ${url} (${response.status} ${response.statusText})`,
    );
  }
  const data = await response.json();
  return schemaFromTables(data.tables);
}

// readOnlyState(ro) -> extensions that toggle editability. EditorState.readOnly
// blocks document edits; EditorView.editable additionally drops the
// contenteditable attribute so screen readers and the cursor reflect the
// read-only state.
function readOnlyState(ro) {
  return [EditorState.readOnly.of(ro), EditorView.editable.of(!ro)];
}

// Theme that plumbs a small set of CSS custom properties into the editor so
// embedders can restyle without reaching into CodeMirror internals. The fallbacks
// reproduce Datasette's current editor appearance (monospace family, the
// page-inherited font size, a transparent background over the page/plugin
// background) so mounting the element on an existing Datasette page is visually a
// no-op — including in dark mode, which Datasette implements entirely through the
// surrounding page's colors, not editor-specific CSS. CodeMirror themes are static
// CSS-in-JS, but var() references pass straight through to the generated rules and
// resolve at render time, so an embedder's :root / @media (prefers-color-scheme)
// overrides just work.
const sqlEditorTheme = EditorView.theme({
  "&": {
    fontSize: "var(--datasette-sql-editor-font-size, inherit)",
    background: "var(--datasette-sql-editor-bg, transparent)",
  },
  ".cm-content, .cm-gutters": {
    fontFamily: "var(--datasette-sql-editor-font-family, monospace)",
  },
});

// <datasette-sql-editor> — a form-associated, light-DOM custom element wrapping
// createSqlEditor(). The module auto-registers the default tag on import (see the
// bottom of this file); call registerSqlEditorElement("my-tag") to also/instead
// register it under a different name.
//
// Attributes (all optional):
//   name          form-field name for the submitted SQL (form participation)
//   database      Datasette database name; when set and schema-url is absent the
//                 schema URL is derived as
//                 {base-url}/{database}/-/editor-schema.json
//   base-url      Datasette base_url prefix used for the derived schema URL ("")
//   schema-url    explicit URL returning the neutral {tables:[...]} schema payload
//   default-table unqualified-column default table for autocomplete
//   readonly      boolean; mounts the editor read-only
//   autofocus     boolean; focuses the editor once mounted
// The initial document is, in priority order: a programmatically-set value; the
// value of a light-DOM <textarea> first child (a progressive-enhancement form
// field that keeps working with JS disabled - it is adopted then removed); or
// the element's trimmed textContent. The light DOM is cleared on mount.
//
// Properties:
//   value       get/set the document (set is host-tagged: no "input" event)
//   schema      set -> updateSchema({schema, defaultTable})
//   view        get the raw EditorView escape hatch (null before mount)
//   readOnly    get/set via a Compartment
//   extensions  get/set extra CodeMirror extensions; honored ONLY before the
//               element connects (createSqlEditor builds the extension set once)
// Methods: focus(), updateSchema(conf), format().
// Events (all bubble):
//   input          {detail:{origin:"user"}} on user edits (host edits suppressed)
//   submit         cancelable; default action requestSubmit()s internals.form
//   ready          once mounted (schema may still be fetching — see below)
//   editor-escape  on Escape at the editor top level
export class DatasetteSqlEditorElement extends HTMLElement {
  static formAssociated = true;

  constructor() {
    super();
    this._handle = null;
    this._internals = null;
    this._readOnly = false;
    this._readOnlyCompartment = new Compartment();
    this._extensions = [];
    this._pendingDoc = null;
    this._initialDoc = "";
    // attachInternals is guarded: Safari < 16.4 has no ElementInternals. Without
    // it the editor still works fully, but form participation
    // (setFormValue/reset) is a no-op, so this field degrades to contributing
    // nothing on submit. Documented as an accepted graceful degradation.
    try {
      this._internals = this.attachInternals ? this.attachInternals() : null;
    } catch (err) {
      this._internals = null;
    }
  }

  connectedCallback() {
    if (this._handle) return; // already mounted (e.g. move within the DOM)

    // Parser-timing guard. When this element's definition loads BEFORE the parser
    // reaches the element (e.g. a non-deferred <script> in <head>, which is how
    // Datasette's own pages load the bundle), the browser upgrades and connects
    // the element at its start tag - before its light-DOM children (the initial
    // document / fallback <textarea>) have been parsed. Reading them now would
    // yield an empty document. Detect that case - still parsing, nothing set
    // programmatically, no children yet - and defer mounting to DOMContentLoaded,
    // by which point the element's subtree is fully parsed. The listener is
    // registered as the parser sees this element (before any later inline
    // script's DOMContentLoaded handler), so consumers reading .view on
    // DOMContentLoaded still observe a mounted editor.
    if (
      this.ownerDocument.readyState === "loading" &&
      this._pendingDoc == null &&
      !this.firstChild
    ) {
      this.ownerDocument.addEventListener(
        "DOMContentLoaded",
        () => this.connectedCallback(),
        { once: true },
      );
      return;
    }

    // Progressive-enhancement fallback: a light-DOM <textarea> first child is a
    // real form field that keeps working with JavaScript disabled. When present,
    // adopt its value as the initial document and remove it, so it does not also
    // submit a duplicate field alongside the value the element contributes via
    // setFormValue.
    const firstEl = this.firstElementChild;
    const fallbackTextarea =
      firstEl && firstEl.tagName === "TEXTAREA" ? firstEl : null;
    let fallbackDoc = null;
    if (fallbackTextarea) {
      fallbackDoc = fallbackTextarea.value;
      fallbackTextarea.remove();
    }

    const initialDoc =
      this._pendingDoc != null
        ? this._pendingDoc
        : fallbackDoc != null
          ? fallbackDoc
          : this.textContent.trim();
    this._initialDoc = initialDoc;
    this._pendingDoc = null;
    // Clear the light-DOM text so it doesn't render behind the editor.
    this.textContent = "";

    this._readOnly = this.hasAttribute("readonly");
    const defaultTable = this.getAttribute("default-table") || undefined;

    this._handle = createSqlEditor(this, {
      doc: initialDoc,
      defaultTable,
      extensions: [
        sqlEditorTheme,
        this._readOnlyCompartment.of(readOnlyState(this._readOnly)),
        ...(this._extensions || []),
      ],
      onChange: () => {
        this._syncFormValue();
        this.dispatchEvent(
          new CustomEvent("input", {
            bubbles: true,
            detail: { origin: "user" },
          }),
        );
      },
      onSubmit: () => {
        const proceed = this.dispatchEvent(
          new CustomEvent("submit", { bubbles: true, cancelable: true }),
        );
        if (!proceed) return; // default prevented
        const form = this._internals && this._internals.form;
        if (!form) return;
        // requestSubmit() runs constraint validation and submit handlers, exactly
        // like clicking a submit button; fall back to submit() where unsupported.
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.submit();
        }
      },
      onEscape: () => {
        this.dispatchEvent(new CustomEvent("editor-escape", { bubbles: true }));
      },
    });

    this._syncFormValue();

    // Fetch schema (if configured) without blocking the editor: a failure
    // downgrades to keyword-only completion and never breaks editing.
    const schemaUrl = this._resolveSchemaUrl();
    if (schemaUrl) {
      fetch(schemaUrl, { credentials: "same-origin" })
        .then((response) => {
          if (!response.ok) {
            throw new Error(
              `schema fetch ${schemaUrl} -> ${response.status} ${response.statusText}`,
            );
          }
          return response.json();
        })
        .then((data) => {
          this.updateSchema({ schema: schemaFromTables(data.tables) });
        })
        .catch((err) => {
          console.warn(
            "datasette-sql-editor: schema fetch failed; keyword-only completion",
            err,
          );
        });
    }

    if (this.hasAttribute("autofocus")) {
      this._handle.view.focus();
    }

    // "ready" fires after mount; schema may still be in flight (it applies later
    // via updateSchema). Dispatched synchronously so listeners attached before the
    // element is inserted observe it.
    this.dispatchEvent(new CustomEvent("ready", { bubbles: true }));
  }

  disconnectedCallback() {
    if (this._handle) {
      // Preserve the document across DOM moves (disconnect + reconnect):
      // connectedCallback prefers _pendingDoc over textContent, and the dead
      // editor's DOM must not be left behind to be misread as initial content.
      this._pendingDoc = this._handle.value;
      this._handle.destroy();
      this._handle = null;
      this.replaceChildren();
    }
  }

  formResetCallback() {
    if (!this._handle) return;
    this._handle.value = this._initialDoc; // hostChange-tagged: no "input" event
    this._syncFormValue();
  }

  _resolveSchemaUrl() {
    const explicit = this.getAttribute("schema-url");
    if (explicit) return explicit;
    const database = this.getAttribute("database");
    if (!database) return null;
    const base = (this.getAttribute("base-url") || "").replace(/\/+$/, "");
    return `${base}/${encodeURIComponent(database)}/-/editor-schema.json`;
  }

  _syncFormValue() {
    if (this._internals && this._internals.setFormValue) {
      this._internals.setFormValue(this.value);
    }
  }

  // ---- properties -------------------------------------------------------
  get value() {
    return this._handle ? this._handle.value : this._pendingDoc || "";
  }
  set value(newValue) {
    const v = newValue == null ? "" : String(newValue);
    if (this._handle) {
      this._handle.value = v; // hostChange-tagged: suppresses the "input" event
      this._syncFormValue();
    } else {
      this._pendingDoc = v;
    }
  }

  set schema(ns) {
    this.updateSchema({ schema: ns });
  }

  get view() {
    return this._handle ? this._handle.view : null;
  }

  get readOnly() {
    return this._readOnly;
  }
  set readOnly(value) {
    this._readOnly = !!value;
    if (this._handle) {
      this._handle.view.dispatch({
        effects: this._readOnlyCompartment.reconfigure(
          readOnlyState(this._readOnly),
        ),
      });
    }
  }

  get extensions() {
    return this._extensions;
  }
  set extensions(exts) {
    if (this._handle) {
      console.warn(
        "datasette-sql-editor: .extensions must be set before the element connects; ignoring",
      );
      return;
    }
    this._extensions = exts || [];
  }

  // ---- methods ----------------------------------------------------------
  focus() {
    if (this._handle) this._handle.view.focus();
  }

  updateSchema(conf = {}) {
    if (!this._handle) return;
    // Merge in default-table so a bare {schema} update doesn't drop it (the
    // compartment reconfigure replaces the whole sql() extension).
    this._handle.updateSchema({
      defaultTable: this.getAttribute("default-table") || undefined,
      ...conf,
    });
  }

  format() {
    const formatter =
      typeof window !== "undefined" ? window.sqlFormatter : undefined;
    if (!formatter || typeof formatter.format !== "function") {
      console.warn(
        "datasette-sql-editor: window.sqlFormatter is not loaded; format() is a no-op",
      );
      return;
    }
    if (!this._handle) return;
    const formatted = formatter.format(this.value);
    this._handle.value = formatted; // hostChange-tagged full replace
    this._syncFormValue();
  }
}

// registerSqlEditorElement(tagName) — defines the element under tagName, guarding
// against double registration (customElements.define throws on a duplicate). A
// no-op in non-DOM contexts. Returns the tag name.
export function registerSqlEditorElement(tagName = "datasette-sql-editor") {
  if (typeof customElements === "undefined") return tagName;
  if (!customElements.get(tagName)) {
    customElements.define(tagName, DatasetteSqlEditorElement);
  }
  return tagName;
}

// Re-export the CodeMirror pieces callers need so plugin code shares this
// module's single CM instance instead of bundling its own.
export {
  EditorView,
  EditorState,
  Compartment,
  Annotation,
  Prec,
  keymap,
  tooltips,
  sql,
  SQLDialect,
  autocompletion,
  completionKeymap,
};

// Auto-register the default <datasette-sql-editor> tag on import. Guarded so
// importing this module in a non-DOM context (SSR/tests) or after another copy of
// the module already claimed the tag is a harmless no-op. This gives template and
// dogfood usage a zero-config element; plugins that want to compose the primitives
// without the element simply don't touch the tag. Register a differently-named tag
// with registerSqlEditorElement("my-tag").
if (
  typeof customElements !== "undefined" &&
  !customElements.get("datasette-sql-editor")
) {
  registerSqlEditorElement("datasette-sql-editor");
}
