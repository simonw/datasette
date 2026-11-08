// Small bootstrap for the row/table editing UI.
//
// The editing dialogs live in edit-tools.js, which is large. This loader
// wires up click handlers for the buttons that open those dialogs and only
// fetches edit-tools.js the first time one of them is used (or hovered/focused,
// so the code is usually ready by the time the click lands).
//
// The URL of the full script is passed using a data-edit-tools-src attribute
// on the <script> tag that loads this file.
(function () {
  var currentScript = document.currentScript;
  var editToolsSrc =
    currentScript && currentScript.getAttribute("data-edit-tools-src");
  var loadPromise = null;
  var pendingOpen = false;

  function tablePageData() {
    return window._datasetteTableData || {};
  }

  function databasePageData() {
    return window._datasetteDatabaseData || {};
  }

  // Each action maps a button selector to the function in edit-tools.js
  // that opens the corresponding dialog.
  var ACTIONS = [
    {
      selector: 'button[data-database-action="create-table"]',
      open: "openTableCreateDialog",
      enabled: function () {
        return !!databasePageData().createTable;
      },
    },
    {
      selector: 'button[data-table-action="alter-table"]',
      open: "openTableAlterDialog",
      enabled: function () {
        return !!tablePageData().alterTable;
      },
    },
    {
      selector: 'button[data-table-action="insert-row"]',
      open: "openRowInsertDialog",
      enabled: function () {
        return !!tablePageData().insertRow;
      },
    },
    {
      selector: 'button[data-row-action="edit"]',
      open: "openRowEditDialog",
    },
    {
      selector: 'button[data-row-action="delete"]',
      open: "openRowDeleteDialog",
    },
  ];

  function loadEditTools() {
    if (window.datasetteEditTools) {
      return Promise.resolve(window.datasetteEditTools);
    }
    if (loadPromise) {
      return loadPromise;
    }
    loadPromise = new Promise(function (resolve, reject) {
      if (!editToolsSrc) {
        reject(new Error("edit-tools.js URL not configured"));
        return;
      }
      var script = document.createElement("script");
      script.src = editToolsSrc;
      script.async = true;
      script.onload = function () {
        if (window.datasetteEditTools) {
          resolve(window.datasetteEditTools);
        } else {
          reject(new Error("edit-tools.js did not initialize"));
        }
      };
      script.onerror = function () {
        script.remove();
        reject(new Error("Could not load " + editToolsSrc));
      };
      document.head.appendChild(script);
    }).catch(function (error) {
      // Allow a later click to retry
      loadPromise = null;
      throw error;
    });
    return loadPromise;
  }

  function matchAction(target, actions) {
    if (!target || !target.closest) {
      return null;
    }
    for (var i = 0; i < actions.length; i += 1) {
      var button = target.closest(actions[i].selector);
      if (button) {
        return { action: actions[i], button: button };
      }
    }
    return null;
  }

  function validateJsonColumnField(field) {
    var value = field.input.value;
    if (value.trim() === "") {
      field.clearValidity();
      return true;
    }
    try {
      JSON.parse(value);
      field.clearValidity();
      return true;
    } catch (error) {
      field.setValidity(
        "Invalid JSON" + (error && error.message ? ": " + error.message : ""),
      );
      return false;
    }
  }

  // Registered eagerly (rather than from edit-tools.js) so it keeps its
  // place in plugin registration order - the first makeColumnField() to
  // return a control wins.
  function registerBuiltinColumnFieldPlugins(manager) {
    if (!manager || !manager.registerPlugin) {
      return;
    }
    manager.registerPlugin("datasette-json-column", {
      version: "1.0",
      makeColumnField: function (context) {
        if (!context.columnType || context.columnType.type !== "json") {
          return;
        }
        return {
          useTextarea: true,
          render: function (field) {
            field.input.addEventListener("input", function () {
              validateJsonColumnField(field);
            });
            field.input.addEventListener("change", function () {
              validateJsonColumnField(field);
            });
            validateJsonColumnField(field);
            return field.input;
          },
          focus: function (field) {
            field.input.focus();
          },
        };
      },
    });
  }

  document.addEventListener("datasette_init", function (evt) {
    var manager = evt.detail;
    registerBuiltinColumnFieldPlugins(manager);

    if (!window.fetch || !window.HTMLDialogElement) {
      return;
    }
    var actions = ACTIONS.filter(function (action) {
      return !action.enabled || action.enabled();
    });
    if (!actions.length) {
      return;
    }

    function preload(ev) {
      if (matchAction(ev.target, actions)) {
        loadEditTools().catch(function () {});
      }
    }
    document.addEventListener("pointerover", preload);
    document.addEventListener("focusin", preload);

    document.addEventListener("click", function (ev) {
      var match = matchAction(ev.target, actions);
      if (!match) {
        return;
      }
      ev.preventDefault();
      if (pendingOpen) {
        return;
      }
      pendingOpen = true;
      match.button.setAttribute("aria-busy", "true");
      loadEditTools()
        .then(function (editTools) {
          editTools[match.action.open](match.button, manager);
        })
        .catch(function (error) {
          console.error(error);
          window.alert("Could not load the editing tools. Please try again.");
        })
        .finally(function () {
          pendingOpen = false;
          match.button.removeAttribute("aria-busy");
        });
    });
  });
})();
