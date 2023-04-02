// Local dev
// datasette/static/datasette-manager.js
// use quokka run on current file for JS debugging

// Custom events for use with the native CustomEvent API
const DATASETTE_EVENTS = {
  INIT: "InitDatasette", // returns datasette manager instance in evt.detail
}

// Datasette "core" -> Methods/APIs that are core
// Plugins will have greater stability if they use the functional hooks- but if they do decide to hook into
// literal DOM selectors, they'll have an easier time using these addresses.
const DOM_SELECTORS = {
  /** Should have one match */
  jsonExportLink: ".export-links a[href*=json]",

  /** Event listeners that go outside of the main table, e.g. existing scroll lisetner */
  tableWrapper: ".table-wrapper",
  table: "table.rows-and-columns",

  // These could have multiple matches
  /** Used for selecting table headers. Use getColumnHeaderItems if you want to add menu items. */
  tableHeaders: `table.rows-and-columns th`,

  /** Used to add "where"  clauses to query using direct manipulation */
  filterRows: ".filter-row",
  /** Used to show top available enum values for a column ("facets") */
  facetResults: ".facet-results [data-column]",
};


/**
 * Monolith class for interacting with Datasette JS API
 * Imported with DEFER, runs after main document parsed
 */
const datasetteManager = {
  VERSION: 'TODO_INJECT_VERSION_OR_ENDPOINT_FROM_SERVER_OR_AT_BUILD_TIME',

  // Let plugins register. TODO... what should unique identifiers be?
  // Name, etc. Should this be a MAP instead of a list?
  // Does order of registration matter?

  // Should plugins be allowed to clobber others or is it last-in takes priority?
  // Does pluginMetadata need to be serializable, or can we let it be stateful / have functions?
  // Should we notify plugins that have dependencies
  // when all dependencies were fulfilled? (leaflet, codemirror, etc)
  // https://github.com/simonw/datasette-leaflet -> this way
  // multiple plugins can all request the same copy of leaflet.
  plugins: new Map(),
  registerPlugin: (name, pluginMetadata) => {
    if (datasetteManager.plugins.get(name)) {
      console.warn(`Warning -> plugin ${name} was redefined`);
    }
    datasetteManager.plugins.set(name, pluginMetadata);
  },

  /**
   * New DOM elements are created each time the button is clicked so the data is not stale.
   * Items
   *  - must provide label (text)
   *  - might provide href (string) or an onclick ((evt) => void)
   *
   * columnMeta is metadata stored on the column header (TH) as a DOMStringMap
   * - column: string
   * - columnNotNull: 0 or 1
   * - columnType: sqlite datatype enum (text, number, etc)
   * - isPk: 0 or 1
   */
  getColumnHeaderItems: (columnMeta) => {
    let items = [];

    datasetteManager.plugins.forEach(plugin => {
      // Accept function that returns list of items with keys
      // Must have: text label
      // Optional: onClick or href
      if (plugin.getColumnHeaderItems) {
        // Plugins can provide multiple items if they want
        // Note: If multiple plugins try to create entry with same label, the last one wins
        items.push(...plugin.getColumnHeaderItems(columnMeta));
      }
    });

    // TODO: Validate item configs and give an informative error message if something is missing.
    return items;
  },

  /** Selectors for document (DOM) elements. Store identifier instead of immediate references in case they haven't loaded when Manager starts. */
  selectors: DOM_SELECTORS,

  // Future API ideas
  // Fetch page's data in array, and cache so plugins could reuse it
  // Provide knowledge of what datasette JS or server-side via traditional console autocomplete
  // State helpers: URL params https://github.com/simonw/datasette/issues/1144 and localstorage
  // UI Hooks: command + k, tab manager hook
};

/**
 * Fire AFTER the document has been parsed
 * Initialization... TODO how to make sure this exists BEFORE datasette manager is loaded? */
const initializeDatasette = () => {
  // Make Manager available to other plugins
  window.__DATASETTE__ = datasetteManager;
  console.log("Datasette Manager Created!");

  const initDatasetteEvent = new CustomEvent(DATASETTE_EVENTS.INIT, {
    detail: datasetteManager
  });
  document.dispatchEvent(initDatasetteEvent)
}

// Main function
document.addEventListener("DOMContentLoaded", function () {
  initializeDatasette();
});
