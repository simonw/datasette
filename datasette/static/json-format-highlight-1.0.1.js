/*
https://github.com/luyilin/json-format-highlight
From https://unpkg.com/json-format-highlight@1.0.1/dist/json-format-highlight.js
MIT Licensed
*/
(function (global, factory) {
  typeof exports === "object" && typeof module !== "undefined"
    ? (module.exports = factory())
    : typeof define === "function" && define.amd
    ? define(factory)
    : (global.jsonFormatHighlight = factory());
})(this, function () {
  "use strict";

  var defaultColors = {
    keyColor: "dimgray",
    numberColor: "lightskyblue",
    stringColor: "lightcoral",
    trueColor: "lightseagreen",
    falseColor: "#f66578",
    nullColor: "cornflowerblue",
  };

  function index(json, colorOptions) {
    if (colorOptions === void 0) colorOptions = {};

    if (!json) {
      return;
    }
    if (typeof json !== "string") {
      json = JSON.stringify(json, null, 2);
    }
    var colors = Object.assign({}, defaultColors, colorOptions);
    json = json.replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">");
    return json.replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+]?\d+)?)/g,
      function (match) {
        var color = colors.numberColor;
        if (/^"/.test(match)) {
          color = /:$/.test(match) ? colors.keyColor : colors.stringColor;
        } else {
          color = /true/.test(match)
            ? colors.trueColor
            : /false/.test(match)
            ? colors.falseColor
            : /null/.test(match)
            ? colors.nullColor
            : color;
        }
        return '<span style="color: ' + color + '">' + match + "</span>";
      }
    );
  }

  return index;
});
