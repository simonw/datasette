import { EditorView, basicSetup } from "codemirror";
import { Compartment } from "@codemirror/state";
import { keymap } from "@codemirror/view";
import { sql, SQLDialect } from "@codemirror/lang-sql";

// A variation of SQLite from lang-sql https://github.com/codemirror/lang-sql/blob/ebf115fffdbe07f91465ccbd82868c587f8182bc/src/sql.ts#L231
const SQLite = SQLDialect.define({
  // Based on https://www.sqlite.org/lang_keywords.html based on likely keywords to be used in select queries
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

// Builds the sql() extension from a {schema, defaultTable, defaultSchema} conf object
function sqlExtension(conf) {
  return sql({
    dialect: SQLite,
    schema: conf.schema,
    defaultTable: conf.defaultTable,
    defaultSchema: conf.defaultSchema,
  });
}

// Utility function from https://codemirror.net/docs/migration/
export function editorFromTextArea(textarea, conf = {}) {
  // Wraps the sql() extension so it can be swapped out later via view.updateSchema()
  // https://codemirror.net/examples/config/#dynamic-configuration
  let sqlCompartment = new Compartment();

  let view = new EditorView({
    doc: textarea.value,
    extensions: [
      keymap.of([
        {
          key: "Shift-Enter",
          run: function () {
            textarea.value = view.state.doc.toString();
            textarea.form.submit();
            return true;
          },
        },
        {
          key: "Meta-Enter",
          run: function () {
            textarea.value = view.state.doc.toString();
            textarea.form.submit();
            return true;
          },
        },
      ]),
      // This has to be after the keymap or else the basicSetup keys will prevent
      // Meta-Enter from running
      basicSetup,
      EditorView.lineWrapping,
      sqlCompartment.of(sqlExtension(conf)),
    ],
  });

  // Allows callers (and plugins) to update the schema/defaultTable/defaultSchema
  // used for autocomplete after the editor has already been created.
  view.updateSchema = (conf2) =>
    view.dispatch({
      effects: sqlCompartment.reconfigure(sqlExtension(conf2)),
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
