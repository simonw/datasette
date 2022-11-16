import { EditorView, basicSetup } from "codemirror";
import { keymap } from "@codemirror/view";
import { sql, SQLDialect } from "@codemirror/lang-sql";

// A variation of SQLite from lang-sql https://github.com/codemirror/lang-sql/blob/ebf115fffdbe07f91465ccbd82868c587f8182bc/src/sql.ts#L231
const SQLite = SQLDialect.define({
  // https://www.sqlite.org/lang_keywords.html
  keywords:
    "abort action add after all alter always analyze and as asc attach autoincrement before begin between by cascade case cast check collate column commit conflict constraint create cross current current_date current_time current_timestamp database default deferrable deferred delete desc detach distinct do drop each else end escape except exclude exclusive exists explain fail filter first following for foreign from full generated glob group groups having if ignore immediate in index indexed initially inner insert instead intersect into is isnull join key last left like limit match materialized natural no not nothing notnull null nulls of offset on or order others outer over partition plan pragma preceding primary query raise range recursive references regexp reindex release rename replace restrict returning right rollback row rows savepoint select set table temp temporary then ties to transaction trigger unbounded union unique update using vacuum values view virtual when where window with without",
  // https://www.sqlite.org/datatype3.html
  types: "null integer real text blob",
  builtin:
    "auth backup bail changes clone databases dbinfo dump echo eqp explain fullschema headers help import imposter indexes iotrace lint load log mode nullvalue once print prompt quit restore save scanstats separator shell show stats system tables testcase timeout timer trace vfsinfo vfslist vfsname width",
  operatorChars: "*+-%<>!=&|/~",
  identifierQuotes: '`"',
  specialVar: "@:?$",
});

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
