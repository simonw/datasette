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

function schemaFromTables(tables) {
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
