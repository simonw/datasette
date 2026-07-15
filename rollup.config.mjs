import { nodeResolve } from "@rollup/plugin-node-resolve";
import terser from "@rollup/plugin-terser";

const plugins = [nodeResolve(), terser()];

export default [
  // IIFE bundle for Datasette's own pages (global name `cm`, included by
  // _codemirror.html). The shared datasette-sql-editor.js module is inlined.
  {
    input: "datasette/static/cm-editor.js",
    output: {
      file: "datasette/static/cm-editor.bundle.js",
      format: "iife",
      name: "cm",
    },
    plugins,
  },
  // Self-contained ESM bundle for plugin authors to import directly, e.g.
  // import {createSqlEditor, datasetteSchema} from
  //   "/-/static/datasette-sql-editor.bundle.js"
  // No bare specifiers remain; all @codemirror/* deps are inlined.
  {
    input: "datasette/static/datasette-sql-editor.js",
    output: {
      file: "datasette/static/datasette-sql-editor.bundle.js",
      format: "es",
    },
    plugins,
  },
];
