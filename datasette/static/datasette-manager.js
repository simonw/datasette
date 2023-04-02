// Local dev
// datasette/static/datasette-manager.js
// use quokka run on current file for JS debugging

const DATASETTE_EVENTS = {
  INIT: "InitDatasette"
}

/**
 * Monolith class for interacting with Datasette JS API
 * Imported with DEFER, runs after main document was parsed
 */
const datasetteManager = {
  VERSION: 'INSERT_HARDCODED_VERSION_OR_ENDPOINT',

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
      console.warn(`Warning -> plugin ${name} is redefined`);
    }
    datasetteManager.plugins.set(name, pluginMetadata);

    // Check which commands to include!
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

    // TODO: We could validate item configs and give an informative error message if something is missing.
    return items;
  },

  /** State helpers */
  // https://github.com/simonw/datasette/issues/1144

  // Datasette "core" -> Methods/APIs that are so core, we don't store them as a plugin
  /** Selectors for significant DOM elements. Store identifier rather than addresses rather than immediate references in case they haven't loaded yet  */
  domSelectors: {
    // aboveTableBar: 'div#plugin-bar',
    jsonUrlLink: '.export-links a[href*=json]',
    dataTable: 'table.rows-and-columns"',
  },

  // Fetch page's data in array format and store
  // Have knowledge of what datasette APIs are available

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
