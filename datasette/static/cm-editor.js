// IIFE entry point for Datasette's own SQL editor pages. Built as
// cm-editor.bundle.js (global name `cm`) and included by _codemirror.html.
//
// This is a thin consumer of the datasette-sql-editor.js primitives so there is
// a single CodeMirror implementation. rollup inlines the shared module into this
// bundle.
import { createSqlEditor, SQLiteDialect } from "./datasette-sql-editor.js";

// Re-exported so plugins/pages using the IIFE global can reach the curated
// dialect (cm.SQLiteDialect) without a second CodeMirror instance.
export { SQLiteDialect };

// Utility function from https://codemirror.net/docs/migration/. Wraps a textarea
// with a CodeMirror SQL editor, mirroring the textarea's value back on submit.
// Returns the EditorView (with an added updateSchema method) for backwards
// compatibility with existing callers (window.editor).
export function editorFromTextArea(textarea, conf = {}) {
  const submit = (view) => {
    textarea.value = view.state.doc.toString();
    textarea.form.submit();
  };

  const handle = createSqlEditor(null, {
    doc: textarea.value,
    schema: conf.schema,
    defaultTable: conf.defaultTable,
    defaultSchema: conf.defaultSchema,
    onSubmit: submit,
  });
  const view = handle.view;

  // Preserve the historical public surface: callers use view.updateSchema(conf).
  view.updateSchema = handle.updateSchema;

  // Idea taken from https://discuss.codemirror.net/t/resizing-codemirror-6/3265.
  // Using CSS resize: both and scheduling a measurement when the element changes.
  let editorDOM = view.contentDOM.closest(".cm-editor");
  let observer = new ResizeObserver(function () {
    view.requestMeasure();
  });
  observer.observe(editorDOM, { attributes: true });

  textarea.parentNode.insertBefore(view.dom, textarea);
  textarea.style.display = "none";
  if (textarea.form) {
    textarea.form.addEventListener("submit", () => {
      textarea.value = view.state.doc.toString();
    });
  }
  return view;
}
