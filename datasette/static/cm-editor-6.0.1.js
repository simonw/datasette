import { EditorView, basicSetup } from "codemirror";
import { keymap } from "@codemirror/view";
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
      keymap.of([
        {
          key: "Shift-Enter",
          run: function () {
            textarea.value = view.state.doc.toString();
            textarea.form.submit();
          },
          preventDefault: true,
        },
      ]),
      sql({
        dialect: SQLite,
      }),
    ],
  });

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
