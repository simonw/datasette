// Custom events for use with the native CustomEvent API
const DATASETTE_EVENTS = {
  INIT: "datasette_init", // returns datasette manager instance in evt.detail
};

// Datasette "core" -> Methods/APIs that are foundational
// Plugins will have greater stability if they use the functional hooks- but if they do decide to hook into
// literal DOM selectors, they'll have an easier time using these addresses.
const DOM_SELECTORS = {
  /** The first match is the default JSON export link */
  jsonExportLink: ".export-links a[href*=json]",

  /** Event listeners that go outside of the main table, e.g. existing scroll listener */
  tableWrapper: "div.table-wrapper",
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
   * Sections shown on the homepage, between the search box and the catalog.
   * Each plugin's makeHomepageSections(context) returns an array of
   * {id, render(node, context)} objects, the same shape as makeJumpSections.
   */
  makeHomepageSections: (context) => {
    let homepageSections = [];

    datasetteManager.plugins.forEach((plugin, pluginName) => {
      if (plugin.makeHomepageSections) {
        try {
          const sections = plugin.makeHomepageSections(context) || [];
          homepageSections.push(...sections);
        } catch (error) {
          console.error(
            `Error in makeHomepageSections() for plugin ${pluginName}`,
            error,
          );
        }
      }
    });

    return homepageSections;
  },

  /**
   * In MVP, each plugin can only have 1 instance.
   * In future, panels could be repeated. We omit that for now since so many plugins depend on
   * shared URL state, so having multiple instances of plugin at same time is problematic.
   * Currently, we never destroy any panels, we just hide them.
   *
   * The panels follow the WAI-ARIA tabs pattern: the buttons are tabs in a
   * tablist, the selected one has aria-selected="true" and is the only one
   * in the tab order, and the arrow, Home and End keys move between them.
   *
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

    let contents = aboveTablePanel.querySelector(".tab-contents");
    let controls = aboveTablePanel.querySelector(".tab-controls");

    const getTabs = () => Array.from(controls.querySelectorAll('[role="tab"]'));

    const selectTab = (selectedTab) => {
      getTabs().forEach((tab) => {
        const isSelected = tab === selectedTab;
        tab.setAttribute("aria-selected", isSelected ? "true" : "false");
        // Roving tabindex: Tab enters the tablist at the selected tab and
        // then moves on to the panel, the arrow keys move between tabs
        tab.tabIndex = isSelected ? 0 : -1;
        const panel = document.getElementById(
          tab.getAttribute("aria-controls"),
        );
        if (panel) {
          panel.hidden = !isSelected;
        }
      });
    };

    // First render: create wrappers. Otherwise, reuse previous.
    if (!contents || !controls) {
      controls = document.createElement("div");
      controls.classList.add("tab-controls");
      controls.setAttribute("role", "tablist");
      controls.setAttribute("aria-label", "Panels");
      controls.addEventListener("keydown", (ev) => {
        if (ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) {
          return;
        }
        const tabs = getTabs();
        const current = tabs.indexOf(ev.target.closest('[role="tab"]'));
        if (current === -1) {
          return;
        }
        const isRtl = getComputedStyle(controls).direction === "rtl";
        let next;
        if (ev.key === (isRtl ? "ArrowLeft" : "ArrowRight")) {
          next = (current + 1) % tabs.length;
        } else if (ev.key === (isRtl ? "ArrowRight" : "ArrowLeft")) {
          next = (current - 1 + tabs.length) % tabs.length;
        } else if (ev.key === "Home") {
          next = 0;
        } else if (ev.key === "End") {
          next = tabs.length - 1;
        } else {
          return;
        }
        ev.preventDefault();
        selectTab(tabs[next]);
        tabs[next].focus();
      });

      contents = document.createElement("div");
      contents.classList.add("tab-contents");

      aboveTablePanel.appendChild(controls);
      aboveTablePanel.appendChild(contents);
    }

    datasetteManager.plugins.forEach((plugin, pluginName) => {
      const { makeAboveTablePanelConfigs } = plugin;

      if (makeAboveTablePanelConfigs) {
        // Each plugin can make multiple panels
        const configs = makeAboveTablePanelConfigs();

        configs.forEach((config) => {
          // These are used as ID references, which cannot contain whitespace
          const nodeContentId =
            `${pluginName}_${config.id}_panel-content`.replace(/\s+/g, "-");
          const tabId = `${nodeContentId}-tab`;

          // quit if we've already registered this plugin
          // TODO: look into whether plugins should be allowed to ask
          // parent to re-render, or if they should manage that internally.
          if (document.getElementById(nodeContentId)) {
            return;
          }

          // Add tab control button
          const pluginControl = document.createElement("button");
          pluginControl.type = "button";
          pluginControl.id = tabId;
          pluginControl.textContent = config.label;
          pluginControl.setAttribute("role", "tab");
          pluginControl.setAttribute("aria-selected", "false");
          pluginControl.setAttribute("aria-controls", nodeContentId);
          pluginControl.tabIndex = -1;
          pluginControl.addEventListener("click", () => {
            selectTab(pluginControl);
          });
          controls.appendChild(pluginControl);

          // Add plugin content area
          const pluginNode = document.createElement("div");
          pluginNode.id = nodeContentId;
          pluginNode.setAttribute("role", "tabpanel");
          pluginNode.setAttribute("aria-labelledby", tabId);
          // Focusable, so keyboard users can reach a panel that has no
          // focusable content of its own
          pluginNode.tabIndex = 0;
          pluginNode.hidden = true; // Hidden unless its tab is selected
          config.render(pluginNode);

          contents.appendChild(pluginNode);
        });
      }
    });

    // Let the first panel be selected by default, keeping any existing
    // selection when more panels are registered later
    const tabs = getTabs();
    if (
      tabs.length &&
      !tabs.some((tab) => tab.getAttribute("aria-selected") === "true")
    ) {
      selectTab(tabs[0]);
    }
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
  // Hide the global behind __ prefix. Ideally they should be listening for the
  // DATASETTE_EVENTS.INIT event to avoid the habit of reading from the window.

  window.__DATASETTE__ = datasetteManager;

  const initDatasetteEvent = new CustomEvent(DATASETTE_EVENTS.INIT, {
    detail: datasetteManager,
  });

  document.dispatchEvent(initDatasetteEvent);
};

/**
 * Main function
 * Fires AFTER the document has been parsed
 */
document.addEventListener("DOMContentLoaded", function () {
  initializeDatasette();
});
