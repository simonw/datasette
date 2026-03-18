var DROPDOWN_HTML = `<div class="dropdown-menu">
<div class="hook"></div>
<ul class="dropdown-actions"></ul>
<p class="dropdown-column-type"></p>
<p class="dropdown-column-description"></p>
</div>`;

var DROPDOWN_ICON_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="3"></circle>
  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
</svg>`;

function getParams() {
  return new URLSearchParams(location.search);
}

function paramsToUrl(params) {
  var s = params.toString();
  return s ? "?" + s : location.pathname;
}

function sortDescUrl(column) {
  var params = getParams();
  params.set("_sort_desc", column);
  params.delete("_sort");
  params.delete("_next");
  return paramsToUrl(params);
}

function sortAscUrl(column) {
  var params = getParams();
  params.set("_sort", column);
  params.delete("_sort_desc");
  params.delete("_next");
  return paramsToUrl(params);
}

function facetUrl(column) {
  var params = getParams();
  params.append("_facet", column);
  return paramsToUrl(params);
}

function hideColumnUrl(column) {
  var params = getParams();
  params.append("_nocol", column);
  return paramsToUrl(params);
}

function showAllColumnsUrl() {
  var params = getParams();
  params.delete("_nocol");
  params.delete("_col");
  return paramsToUrl(params);
}

function notBlankUrl(column) {
  var params = getParams();
  params.set(`${column}__notblank`, "1");
  return paramsToUrl(params);
}

function getDisplayedFacets() {
  return Array.from(document.querySelectorAll(".facet-info")).map(
    (el) => el.dataset.column,
  );
}

function getColumnClassName(th) {
  return Array.from(th.classList).find((className) =>
    className.startsWith("col-"),
  );
}

function getColumnCells(th) {
  var table = th.closest("table");
  var columnClassName = getColumnClassName(th);
  if (!table || !columnClassName) {
    return [];
  }
  return Array.from(table.querySelectorAll("td." + columnClassName));
}

function getColumnMeta(th) {
  return {
    columnName: th.dataset.column,
    columnNotNull: th.dataset.columnNotNull === "1",
    columnType: th.dataset.columnType,
    isPk: th.dataset.isPk === "1",
  };
}

function getColumnTypeText(th) {
  var columnType = th.dataset.columnType;
  if (!columnType) {
    return null;
  }
  var notNull = th.dataset.columnNotNull === "1" ? " NOT NULL" : "";
  return `Type: ${columnType.toUpperCase()}${notNull}`;
}

function canChooseColumns() {
  return !!(
    document.querySelector("column-chooser") && window._columnChooserData
  );
}

function shouldShowShowAllColumns() {
  var params = getParams();
  return params.getAll("_nocol").length || params.getAll("_col").length;
}

function buildColumnActionItems(manager, th, options) {
  options = options || {};
  var params = getParams();
  var column = th.dataset.column;
  var columnActions = [];
  var isSortable = !!th.querySelector("a");
  var isFirstColumn = th.parentElement.querySelector("th:first-of-type") === th;
  var isSinglePk =
    th.dataset.isPk === "1" &&
    document.querySelectorAll('th[data-is-pk="1"]').length === 1;
  var hasBlankValues = getColumnCells(th).some(
    (el) => el.innerText.trim() === "",
  );

  if (isSortable && params.get("_sort") !== column) {
    columnActions.push({
      label: "Sort ascending",
      href: sortAscUrl(column),
    });
  }

  if (isSortable && params.get("_sort_desc") !== column) {
    columnActions.push({
      label: "Sort descending",
      href: sortDescUrl(column),
    });
  }

  if (
    DATASETTE_ALLOW_FACET &&
    !isFirstColumn &&
    !getDisplayedFacets().includes(column) &&
    !isSinglePk
  ) {
    columnActions.push({
      label: "Facet by this",
      href: facetUrl(column),
    });
  }

  if (options.includeChooseColumns && canChooseColumns()) {
    columnActions.push({
      label: "Choose columns",
      href: "#",
      onClick:
        options.onChooseColumns ||
        function (ev) {
          ev.preventDefault();
          openColumnChooser();
        },
    });
  }

  if (th.dataset.isPk !== "1") {
    columnActions.push({
      label: "Hide this column",
      href: hideColumnUrl(column),
    });
  }

  if (options.includeShowAllColumns && shouldShowShowAllColumns()) {
    columnActions.push({
      label: "Show all columns",
      href: showAllColumnsUrl(),
    });
  }

  if (params.get(`${column}__notblank`) !== "1" && hasBlankValues) {
    columnActions.push({
      label: "Show not-blank rows",
      href: notBlankUrl(column),
    });
  }

  return columnActions.concat(manager.makeColumnActions(getColumnMeta(th)));
}

function buildColumnActionState(manager, th, options) {
  return {
    column: th.dataset.column,
    columnDescription: th.dataset.columnDescription || null,
    columnMeta: getColumnMeta(th),
    columnTypeText: getColumnTypeText(th),
    actionItems: buildColumnActionItems(manager, th, options),
  };
}

function initializeColumnActions(manager) {
  manager.columnActions = {
    buildColumnActionState: function (th, options) {
      return buildColumnActionState(manager, th, options);
    },
    buildColumnActionItems: function (th, options) {
      return buildColumnActionItems(manager, th, options);
    },
    canChooseColumns: canChooseColumns,
    facetUrl: facetUrl,
    getColumnMeta: getColumnMeta,
    getColumnTypeText: getColumnTypeText,
    hideColumnUrl: hideColumnUrl,
    notBlankUrl: notBlankUrl,
    shouldShowShowAllColumns: shouldShowShowAllColumns,
    showAllColumnsUrl: showAllColumnsUrl,
    sortAscUrl: sortAscUrl,
    sortDescUrl: sortDescUrl,
  };
}

function renderActionLink(itemConfig) {
  var newLink = document.createElement("a");
  newLink.textContent = itemConfig.label;
  newLink.href = itemConfig.href || "#";
  if (itemConfig.onClick) {
    newLink.addEventListener("click", itemConfig.onClick);
  }
  return newLink;
}

/** Main initialization function for Datasette Table interactions */
const initDatasetteTable = function (manager) {
  // Feature detection
  if (!window.URLSearchParams) {
    return;
  }
  function closeMenu() {
    menu.style.display = "none";
    menu.classList.remove("anim-scale-in");
  }

  const tableWrapper = document.querySelector(manager.selectors.tableWrapper);
  if (tableWrapper) {
    tableWrapper.addEventListener("scroll", closeMenu);
  }
  document.body.addEventListener("click", (ev) => {
    /* was this click outside the menu? */
    var target = ev.target;
    while (target && target != menu) {
      target = target.parentNode;
    }
    if (!target) {
      closeMenu();
    }
  });

  function onTableHeaderClick(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    menu.innerHTML = DROPDOWN_HTML;
    var th = ev.target;
    while (th.nodeName != "TH") {
      th = th.parentNode;
    }
    var rect = th.getBoundingClientRect();
    var menuTop = rect.bottom + window.scrollY;
    var menuLeft = rect.left + window.scrollX;
    var actionState = manager.columnActions.buildColumnActionState(th, {
      includeChooseColumns: true,
      includeShowAllColumns: true,
      onChooseColumns: function (ev) {
        ev.preventDefault();
        closeMenu();
        openColumnChooser();
      },
    });
    var menuList = menu.querySelector("ul.dropdown-actions");
    menuList.innerHTML = "";
    actionState.actionItems.forEach((itemConfig) => {
      var menuItem = document.createElement("li");
      menuItem.appendChild(renderActionLink(itemConfig));
      menuList.appendChild(menuItem);
    });

    var columnTypeP = menu.querySelector(".dropdown-column-type");
    if (actionState.columnTypeText) {
      columnTypeP.style.display = "block";
      columnTypeP.innerText = actionState.columnTypeText;
    } else {
      columnTypeP.style.display = "none";
    }

    var columnDescriptionP = menu.querySelector(".dropdown-column-description");
    if (actionState.columnDescription) {
      columnDescriptionP.innerText = actionState.columnDescription;
      columnDescriptionP.style.display = "block";
    } else {
      columnDescriptionP.style.display = "none";
    }
    menu.style.position = "absolute";
    menu.style.top = menuTop + 6 + "px";
    menu.style.left = menuLeft + "px";
    menu.style.display = "block";
    menu.classList.add("anim-scale-in");

    // Measure width of menu and adjust position if too far right
    const menuWidth = menu.offsetWidth;
    const windowWidth = window.innerWidth;
    if (menuLeft + menuWidth > windowWidth) {
      menu.style.left = windowWidth - menuWidth - 20 + "px";
    }
    // Align menu .hook arrow with the column cog icon
    const hook = menu.querySelector(".hook");
    const icon = th.querySelector(".dropdown-menu-icon");
    const iconRect = icon.getBoundingClientRect();
    const hookLeft = iconRect.left - menuLeft + 1 + "px";
    hook.style.left = hookLeft;
    // Move the whole menu right if the hook is too far right
    const menuRect = menu.getBoundingClientRect();
    if (iconRect.right > menuRect.right) {
      menu.style.left = iconRect.right - menuWidth + "px";
      // And move hook tip as well
      hook.style.left = menuWidth - 13 + "px";
    }
  }

  var svg = document.createElement("div");
  svg.innerHTML = DROPDOWN_ICON_SVG;
  svg = svg.querySelector("*");
  svg.classList.add("dropdown-menu-icon");
  var menu = document.createElement("div");
  menu.innerHTML = DROPDOWN_HTML;
  menu = menu.querySelector("*");
  menu.style.position = "absolute";
  menu.style.display = "none";
  document.body.appendChild(menu);

  var ths = Array.from(
    document.querySelectorAll(manager.selectors.tableHeaders),
  );
  ths.forEach((th) => {
    if (!th.querySelector("a")) {
      return;
    }
    var icon = svg.cloneNode(true);
    icon.addEventListener("click", onTableHeaderClick);
    th.appendChild(icon);
  });
};

/* Add x buttons to the filter rows */
function addButtonsToFilterRows(manager) {
  var x = "✖";
  var rows = Array.from(
    document.querySelectorAll(manager.selectors.filterRow),
  ).filter((el) => el.querySelector(".filter-op"));
  rows.forEach((row) => {
    var a = document.createElement("a");
    a.setAttribute("href", "#");
    a.setAttribute("aria-label", "Remove this filter");
    a.style.textDecoration = "none";
    a.innerText = x;
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      let row = ev.target.closest("div");
      row.querySelector("select").value = "";
      row.querySelector(".filter-op select").value = "exact";
      row.querySelector("input.filter-value").value = "";
      ev.target.closest("a").style.display = "none";
    });
    row.appendChild(a);
    var column = row.querySelector("select");
    if (!column.value) {
      a.style.display = "none";
    }
  });
}

/* Set up datalist autocomplete for filter values */
function initAutocompleteForFilterValues(manager) {
  function createDataLists() {
    var facetResults = document.querySelectorAll(
      manager.selectors.facetResults,
    );
    Array.from(facetResults).forEach(function (facetResult) {
      // Use link text from all links in the facet result
      var links = Array.from(
        facetResult.querySelectorAll("li:not(.facet-truncated) a"),
      );
      // Create a datalist element
      var datalist = document.createElement("datalist");
      datalist.id = "datalist-" + facetResult.dataset.column;
      // Create an option element for each link text
      links.forEach(function (link) {
        var option = document.createElement("option");
        option.label = link.innerText;
        option.value = link.dataset.facetValue;
        datalist.appendChild(option);
      });
      // Add the datalist to the facet result
      facetResult.appendChild(datalist);
    });
  }
  createDataLists();
  // When any select with name=_filter_column changes, update the datalist
  document.body.addEventListener("change", function (event) {
    if (event.target.name === "_filter_column") {
      event.target
        .closest(manager.selectors.filterRow)
        .querySelector(".filter-value")
        .setAttribute("list", "datalist-" + event.target.value);
    }
  });
}

/** Open the column-chooser web component */
function openColumnChooser() {
  var chooser = document.querySelector("column-chooser");
  var data = window._columnChooserData;
  if (!chooser || !data) return;

  var nonPkColumns = data.allColumns.filter(function (col) {
    return data.primaryKeys.indexOf(col) === -1;
  });
  var selected = data.selectedColumns.filter(function (col) {
    return data.primaryKeys.indexOf(col) === -1;
  });

  chooser.open({
    columns: nonPkColumns,
    selected: selected,
    onApply: function (cols) {
      var params = new URLSearchParams(location.search);
      params.delete("_col");
      params.delete("_nocol");
      params.delete("_next");

      if (cols.length === nonPkColumns.length) {
        // Check if order matches original - if so, no params needed
        var orderMatches = cols.every(function (col, i) {
          return col === nonPkColumns[i];
        });
        if (!orderMatches) {
          cols.forEach(function (col) {
            params.append("_col", col);
          });
        }
      } else {
        cols.forEach(function (col) {
          params.append("_col", col);
        });
      }
      var qs = params.toString();
      location.href = qs ? "?" + qs : location.pathname;
    },
  });
}

// Ensures Table UI is initialized only after the Manager is ready.
document.addEventListener("datasette_init", function (evt) {
  const { detail: manager } = evt;

  initializeColumnActions(manager);

  // Main table
  initDatasetteTable(manager);

  // Other UI functions with interactive JS needs
  addButtonsToFilterRows(manager);
  initAutocompleteForFilterValues(manager);
});
