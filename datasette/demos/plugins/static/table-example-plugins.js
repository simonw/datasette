/**
 * Example usage of Datasette JS Manager API
 */

document.addEventListener("datasette_init", function (evt) {
  const { detail: manager } = evt;
  // === Demo plugins: remove before merge===
  addPlugins(manager);
});

/**
 * Examples for to test datasette JS api
 */
const addPlugins = (manager) => {

  manager.registerPlugin("column-name-plugin", {
    version: 0.1,
    makeColumnActions: (columnMeta) => {
      const { column } = columnMeta;

      return [
        {
          label: "Copy name to clipboard",
          onClick: (evt) => copyToClipboard(column),
        },
        {
          label: "Log column metadata to console",
          onClick: (evt) => console.log(column),
        },
      ];
    },
  });

  manager.registerPlugin("panel-plugin-graphs", {
    version: 0.1,
    makeAboveTablePanelConfigs: () => {
      return [
        {
          id: 'first-panel',
          label: "First",
          render: node => {
            const description = document.createElement('p');
            description.innerText = 'Hello world';
            node.appendChild(description);
          }
        },
        {
          id: 'second-panel',
          label: "Second",
          render: node => {
            const iframe = document.createElement('iframe');
            iframe.src = "https://observablehq.com/embed/@d3/sortable-bar-chart?cell=viewof+order&cell=chart";
            iframe.width = 800;
            iframe.height = 635;
            iframe.frmaeborder = '0';
            node.appendChild(iframe);
          }
        },
      ];
    },
  });

  manager.registerPlugin("panel-plugin-maps", {
    version: 0.1,
    makeAboveTablePanelConfigs: () => {
      return [
        {
          // ID only has to be unique within a plugin, manager namespaces for you
          id: 'first-map-panel',
          label: "Map plugin",
          // datasette-vega, leafleft can provide a "render" function
          render: node => node.innerHTML = "Here sits a map",
        },
        {
          id: 'second-panel',
          label: "Image plugin",
          render: node => {
            const img = document.createElement('img');
            img.src = 'https://datasette.io/static/datasette-logo.svg'
            node.appendChild(img);
          },
        }
      ];
    },
  });

  // Future: dispatch message to some other part of the page with CustomEvent API
  // Could use to drive filter/sort query builder actions without  page refresh.
}



async function copyToClipboard(str) {
  try {
    await navigator.clipboard.writeText(str);
  } catch (err) {
    /** Rejected - text failed to copy to the clipboard. Browsers didn't give permission */
    console.error('Failed to copy: ', err);
  }
}
