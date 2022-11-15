import { EditorView, basicSetup } from "codemirror";
import { sql, SQLite } from "@codemirror/lang-sql";

// Utility function from https://codemirror.net/docs/migration/
export function editorFromTextArea(textarea) {
  // This could also be configured with a set of tables and columns for better autocomplete:
  // https://github.com/codemirror/lang-sql#user-content-sqlconfig.tables
  let view = new EditorView({
    doc: textarea.value,
    extensions: [
      basicSetup,
      EditorView.lineWrapping,
      sql({
        dialect: SQLite,
      }),
    ],
  });
  textarea.parentNode.insertBefore(view.dom, textarea);
  textarea.style.display = "none";
  if (textarea.form)
    textarea.form.addEventListener("submit", () => {
      textarea.value = view.state.doc.toString();
    });
  return view;
}
