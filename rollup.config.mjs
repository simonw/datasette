import { nodeResolve } from "@rollup/plugin-node-resolve";
import terser from "@rollup/plugin-terser";

export default {
  input: "datasette/static/cm-editor.js",
  output: {
    file: "datasette/static/cm-editor.bundle.js",
    format: "iife",
    name: "cm",
  },
  plugins: [nodeResolve(), terser()],
};
