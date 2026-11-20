// Column action menus for query results, on SQL query and stored query pages.
//
// On table pages the "Column statistics" item lives in table.js's column
// menu. Query results have no other column actions, so this gives each
// result column a cog menu with just that item, plus a button for narrow
// screens where the table headers are hidden. column-stats.js, which builds
// the dialog, is only loaded the first time one of them is used: see
// _column_stats_loader.html.
(() => {
  const COG_SVG = `<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="3"></circle>
  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
</svg>`;

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (name === "text") node.textContent = value;
      else if (name === "className") node.className = value;
      else if (name === "hidden") node.hidden = value;
      else node.setAttribute(name, value);
    }
    node.append(...children);
    return node;
  }

  function init() {
    const data = window._datasetteQueryData;
    const stats = window.DatasetteColumnStats;
    if (!data || !stats || !stats.isAvailable() || !window.DatasetteMenu) {
      return;
    }
    const table = document.querySelector("table.rows-and-columns");
    const columns = data.executeSql.columns;
    const headers = table ? Array.from(table.querySelectorAll("thead th")) : [];
    if (!headers.length || headers.length !== columns.length) {
      return;
    }
    const menu = el("div", {
      id: "column-actions-menu",
      className: "column-actions-menu datasette-menu",
      hidden: true,
    });
    document.body.appendChild(menu);
    const wrapper = table.closest(".table-wrapper");
    if (wrapper) {
      wrapper.addEventListener("scroll", () => window.DatasetteMenu.close());
    }

    function openStatistics(column, returnFocusTo) {
      stats.open({
        column: column.name,
        columnType: column.type,
        returnFocusTo,
      });
    }

    function openMenu(event, trigger, column) {
      event.preventDefault();
      event.stopPropagation();
      if (event.type === "click" && window.DatasetteMenu.isOpenFor(trigger)) {
        window.DatasetteMenu.close({ restoreFocus: true });
        return;
      }
      window.DatasetteMenu.close();
      const item = el("a", {
        href: "#",
        role: "menuitem",
        text: "Column statistics",
      });
      item.addEventListener("click", (clickEvent) => {
        clickEvent.preventDefault();
        window.DatasetteMenu.close({ restoreFocus: true });
        window.setTimeout(() => openStatistics(column, trigger), 0);
      });
      menu.replaceChildren(
        el(
          "div",
          { className: "datasette-menu-heading", role: "presentation" },
          [
            el("strong", {
              className: "datasette-menu-title",
              text: column.name,
            }),
          ],
        ),
        el("ul", { className: "dropdown-actions", role: "none" }, [
          el("li", { role: "none" }, [item]),
        ]),
      );
      if (column.type) {
        menu.appendChild(
          el("p", {
            className: "dropdown-column-type",
            text: `Type: ${column.type.toUpperCase()}`,
          }),
        );
      }
      menu.setAttribute("role", "menu");
      menu.setAttribute("aria-label", "Actions for column " + column.name);
      window.DatasetteMenu.open({
        trigger,
        panel: menu,
        focusLast: event.key === "ArrowUp",
        focusMenu: event.type === "keydown" || event.detail === 0,
      });
    }

    headers.forEach((th, index) => {
      const column = columns[index];
      const trigger = el("button", {
        type: "button",
        className: "column-menu-trigger",
        "aria-label": "Actions for column " + column.name,
        "aria-haspopup": "menu",
        "aria-expanded": "false",
        "aria-controls": menu.id,
      });
      trigger.innerHTML = COG_SVG;
      trigger.addEventListener("click", (event) =>
        openMenu(event, trigger, column),
      );
      trigger.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          openMenu(event, trigger, column);
        }
      });
      th.appendChild(trigger);
    });

    const mobileButton = el(
      "button",
      {
        type: "button",
        className:
          "column-actions-mobile small-screen-only column-stats-mobile",
        "aria-haspopup": "dialog",
      },
      [el("span", { text: "Column statistics" })],
    );
    mobileButton.insertAdjacentHTML("afterbegin", COG_SVG);
    mobileButton.addEventListener("click", () =>
      openStatistics(columns[0], mobileButton),
    );
    (wrapper || table).before(mobileButton);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
