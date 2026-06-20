// Custom events for use with the native CustomEvent API
const DATASETTE_EVENTS = {
  INIT: "datasette_init", // returns datasette manager instance in evt.detail
};

// Datasette "core" -> Methods/APIs that are foundational
// Plugins will have greater stability if they use the functional hooks- but if they do decide to hook into
// literal DOM selectors, they'll have an easier time using these addresses.
const DOM_SELECTORS = {
  /** Should have one match */
  jsonExportLink: ".export-links a[href*=json]",

  /** Event listeners that go outside of the main table, e.g. existing scroll listener */
  tableWrapper: ".table-wrapper",
  table: "table.rows-and-columns",
  aboveTablePanel: ".above-table-panel",

  // These could have multiple matches
  /** Used for selecting table headers. Use makeColumnActions if you want to add menu items. */
  tableHeaders: `table.rows-and-columns th`,

  /** Used to add "where"  clauses to query using direct manipulation */
  filterRows: ".filter-row",
  /** Used to show top available enum values for a column ("facets") */
  facetResults: ".facet-results [data-column]",
};

let editToolsPromise = null;
let autocompletePromise = null;

function datasettePath(path) {
  const baseUrl = window.datasetteBaseUrl || "/";
  return baseUrl.replace(/\/?$/, "/") + String(path || "").replace(/^\/+/, "");
}

function parseInsertDialogRow(value) {
  if (!value) {
    return {};
  }
  try {
    const row = JSON.parse(value);
    if (!row || typeof row !== "object" || Array.isArray(row)) {
      throw new Error("row must be an object");
    }
    return row;
  } catch (error) {
    throw new Error("Invalid insert dialog row: " + error.message);
  }
}

/**
 * Monolith class for interacting with Datasette JS API
 * Imported with DEFER, runs after main document parsed
 * For now, manually synced with datasette/version.py
 */
const datasetteManager = {
  VERSION: window.datasetteVersion,

  // TODO: Should order of registration matter more?

  // Should plugins be allowed to clobber others or is it last-in takes priority?
  // Does pluginMetadata need to be serializable, or can we let it be stateful / have functions?
  plugins: new Map(),

  path: datasettePath,

  loadAutocomplete: () => {
    if (!window.customElements) {
      return Promise.resolve(null);
    }
    if (customElements.get("datasette-autocomplete")) {
      return Promise.resolve(customElements.get("datasette-autocomplete"));
    }
    if (!autocompletePromise) {
      const url =
        window.datasetteAutocompleteUrl ||
        datasettePath("/-/static/autocomplete.js");
      autocompletePromise = import(url).then(() =>
        customElements.get("datasette-autocomplete"),
      );
    }
    return autocompletePromise;
  },

  loadEditTools: () => {
    if (window.__DATASETTE_EDIT_TOOLS__) {
      if (window.__DATASETTE_EDIT_TOOLS__.install) {
        window.__DATASETTE_EDIT_TOOLS__.install(datasetteManager);
      }
      return Promise.resolve(window.__DATASETTE_EDIT_TOOLS__);
    }
    if (!editToolsPromise) {
      const url =
        window.datasetteEditToolsUrl ||
        datasettePath("/-/static/edit-tools.js");
      editToolsPromise = import(url).then(() => {
        if (!window.__DATASETTE_EDIT_TOOLS__) {
          throw new Error("edit-tools.js did not register its API");
        }
        if (window.__DATASETTE_EDIT_TOOLS__.install) {
          window.__DATASETTE_EDIT_TOOLS__.install(datasetteManager);
        }
        return window.__DATASETTE_EDIT_TOOLS__;
      });
    }
    return editToolsPromise;
  },

  insertDialog: async (database, table, row, message, options) => {
    const editTools = await datasetteManager.loadEditTools();
    return editTools.insertDialog(
      datasetteManager,
      database,
      table,
      row,
      message,
      options,
    );
  },

  registerPlugin: (name, pluginMetadata) => {
    if (datasetteManager.plugins.has(name)) {
      console.warn(`Warning -> plugin ${name} was redefined`);
    }
    datasetteManager.plugins.set(name, pluginMetadata);

    // If the plugin participates in the panel... update the panel.
    if (pluginMetadata.makeAboveTablePanelConfigs) {
      datasetteManager.renderAboveTablePanel();
    }
  },

  /**
   * New DOM elements are created on each click, so the data is not stale.
   *
   * Items
   *  - must provide label (text)
   *  - might provide href (string) or an onclick ((evt) => void)
   *
   * columnMeta is metadata stored on the column header (TH) as a DOMStringMap
   * - column: string
   * - columnNotNull: boolean
   * - columnType: sqlite datatype enum (text, number, etc)
   * - isPk: boolean
   */
  makeColumnActions: (columnMeta) => {
    let columnActions = [];

    // Accept function that returns list of columnActions with keys
    // Required: label (text)
    // Optional: onClick or href
    datasetteManager.plugins.forEach((plugin) => {
      if (plugin.makeColumnActions) {
        // Plugins can provide multiple columnActions if they want
        // If multiple try to create entry with same label, the last one deletes the others
        columnActions.push(...plugin.makeColumnActions(columnMeta));
      }
    });

    // TODO: Validate columnAction configs and give informative error message if missing keys.
    return columnActions;
  },

  /**
   * Allows JavaScript plugins to replace or enhance insert/edit modal fields
   * for specific Datasette column types.
   *
   * The first plugin to return a control object wins. Returning null or
   * undefined means "I do not handle this field".
   */
  makeColumnField: (context) => {
    for (const [pluginName, plugin] of datasetteManager.plugins) {
      if (!plugin.makeColumnField) {
        continue;
      }
      let control = null;
      try {
        control = plugin.makeColumnField(context);
      } catch (error) {
        console.error(
          `Error in makeColumnField() for plugin ${pluginName}`,
          error,
        );
        continue;
      }
      if (control) {
        return Object.assign({ pluginName }, control);
      }
    }
    return null;
  },

  makeJumpSections: (context) => {
    let jumpSections = [];

    datasetteManager.plugins.forEach((plugin) => {
      if (plugin.makeJumpSections) {
        const sections = plugin.makeJumpSections(context) || [];
        jumpSections.push(...sections);
      }
    });

    return jumpSections;
  },

  /**
   * In MVP, each plugin can only have 1 instance.
   * In future, panels could be repeated. We omit that for now since so many plugins depend on
   * shared URL state, so having multiple instances of plugin at same time is problematic.
   * Currently, we never destroy any panels, we just hide them.
   *
   * TODO: nicer panel css, show panel selection state.
   * TODO: does this hook need to take any arguments?
   */
  renderAboveTablePanel: () => {
    const aboveTablePanel = document.querySelector(
      DOM_SELECTORS.aboveTablePanel,
    );

    if (!aboveTablePanel) {
      console.warn(
        "This page does not have a table, the renderAboveTablePanel cannot be used.",
      );
      return;
    }

    let aboveTablePanelWrapper = aboveTablePanel.querySelector(".panels");

    // First render: create wrappers. Otherwise, reuse previous.
    if (!aboveTablePanelWrapper) {
      aboveTablePanelWrapper = document.createElement("div");
      aboveTablePanelWrapper.classList.add("tab-contents");
      const panelNav = document.createElement("div");
      panelNav.classList.add("tab-controls");

      // Temporary: css for minimal amount of breathing room.
      panelNav.style.display = "flex";
      panelNav.style.gap = "8px";
      panelNav.style.marginTop = "4px";
      panelNav.style.marginBottom = "20px";

      aboveTablePanel.appendChild(panelNav);
      aboveTablePanel.appendChild(aboveTablePanelWrapper);
    }

    datasetteManager.plugins.forEach((plugin, pluginName) => {
      const { makeAboveTablePanelConfigs } = plugin;

      if (makeAboveTablePanelConfigs) {
        const controls = aboveTablePanel.querySelector(".tab-controls");
        const contents = aboveTablePanel.querySelector(".tab-contents");

        // Each plugin can make multiple panels
        const configs = makeAboveTablePanelConfigs();

        configs.forEach((config, i) => {
          const nodeContentId = `${pluginName}_${config.id}_panel-content`;

          // quit if we've already registered this plugin
          // TODO: look into whether plugins should be allowed to ask
          // parent to re-render, or if they should manage that internally.
          if (document.getElementById(nodeContentId)) {
            return;
          }

          // Add tab control button
          const pluginControl = document.createElement("button");
          pluginControl.textContent = config.label;
          pluginControl.onclick = () => {
            contents.childNodes.forEach((node) => {
              if (node.id === nodeContentId) {
                node.style.display = "block";
              } else {
                node.style.display = "none";
              }
            });
          };
          controls.appendChild(pluginControl);

          // Add plugin content area
          const pluginNode = document.createElement("div");
          pluginNode.id = nodeContentId;
          config.render(pluginNode);
          pluginNode.style.display = "none"; // Default to hidden unless you're ifrst

          contents.appendChild(pluginNode);
        });

        // Let first node be selected by default
        if (contents.childNodes.length) {
          contents.childNodes[0].style.display = "block";
        }
      }
    });
  },

  /** Selectors for document (DOM) elements. Store identifier instead of immediate references in case they haven't loaded when Manager starts. */
  selectors: DOM_SELECTORS,

  // Future API ideas
  // Fetch page's data in array, and cache so plugins could reuse it
  // Provide knowledge of what datasette JS or server-side via traditional console autocomplete
  // State helpers: URL params https://github.com/simonw/datasette/issues/1144 and localstorage
  // UI Hooks: command + k, tab manager hook
  // Should we notify plugins that have dependencies
  // when all dependencies were fulfilled? (leaflet, codemirror, etc)
  // https://github.com/simonw/datasette-leaflet -> this way
  // multiple plugins can all request the same copy of leaflet.
};

const initializeDatasette = () => {
  window.datasette = datasetteManager;
  window.__DATASETTE__ = datasetteManager;

  const initDatasetteEvent = new CustomEvent(DATASETTE_EVENTS.INIT, {
    detail: datasetteManager,
  });

  document.dispatchEvent(initDatasetteEvent);

  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-insert-dialog]");
    if (!button) {
      return;
    }
    event.preventDefault();
    let row;
    try {
      row = parseInsertDialogRow(button.dataset.row || "{}");
    } catch (error) {
      console.error(error);
      return;
    }
    const reloadOnInsert = button.hasAttribute("data-insert-dialog-reload");
    datasetteManager
      .insertDialog(
        button.dataset.database,
        button.dataset.table,
        row,
        button.dataset.message || "",
        { flashMessage: reloadOnInsert },
      )
      .then((result) => {
        if (
          reloadOnInsert &&
          result &&
          result.ok &&
          result.status === "inserted"
        ) {
          window.location.reload();
        }
      })
      .catch((error) => {
        console.error(error);
      });
  });
};

/**
 * Main function
 * Fires AFTER the document has been parsed
 */
document.addEventListener("DOMContentLoaded", function () {
  initializeDatasette();
});
