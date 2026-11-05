/* Table treatment switcher.

   Chooses a treatment from ?_tt=<id>, then localStorage, then the default,
   and sets it on <html data-table-treatment="..."> straight away (this script
   runs synchronously in <head>, so there is no flash of the wrong style).
   Each treatment's CSS is scoped to that attribute.

   It also copies each column's name and declared type from its <th> onto every
   <td> as data-column / data-column-type, so treatments can style by column. */
(() => {
  const TREATMENTS = [
    { id: "current", label: "Current", blurb: "Datasette's table as it ships today, for comparison." },
    { id: "datasette", label: "Datasette", blurb: "A refresh in Datasette's own design language, borrowing from the modal dialogs and filter panel." },
    { id: "agent", label: "Agent markdown", blurb: "Matches how datasette-agent renders markdown tables in chat responses." },
    { id: "ledger", label: "Ledger", blurb: "Editorial and typographic: hairline rules, tabular numbers, no boxes." },
    { id: "grid", label: "Data grid", blurb: "Dense spreadsheet: single-line cells, sticky header and first column." },
    { id: "cards", label: "Row cards", blurb: "Every row becomes a labelled card, even on a wide screen." },
    { id: "soft", label: "Soft", blurb: "Airy and friendly, with type icons in headers and rounded row highlights." },
    { id: "terminal", label: "Terminal", blurb: "Dark, monospaced and high-contrast, like a SQL console." },
  ];
  const KEY = "datasette-table-treatment";
  const DEFAULT = "datasette";
  const ids = TREATMENTS.map((t) => t.id);
  const root = document.documentElement;

  const storage = {
    get() {
      try { return localStorage.getItem(KEY); } catch (e) { return null; }
    },
    set(value) {
      try { localStorage.setItem(KEY, value); } catch (e) {}
    },
  };

  let current = new URLSearchParams(location.search).get("_tt");
  if (!ids.includes(current)) current = storage.get();
  if (!ids.includes(current)) current = DEFAULT;
  root.dataset.tableTreatment = current;

  function annotateCells() {
    const table = document.querySelector("table.rows-and-columns");
    if (!table) return;
    const columns = [...table.querySelectorAll("thead th")].map((th) => ({
      name: th.dataset.column || th.textContent.trim(),
      type: th.dataset.columnType || "",
      pk: th.dataset.isPk === "1",
    }));
    table.querySelectorAll("tbody tr").forEach((tr) => {
      [...tr.children].forEach((td, index) => {
        const column = columns[index];
        if (!column) return;
        td.dataset.column = column.name;
        td.dataset.columnType = column.type;
        if (column.pk) td.dataset.isPk = "1";
      });
    });
  }

  function syncLinks(id) {
    // Datasette's server-rendered links carry ?_tt= along; keep them current.
    document.querySelectorAll('a[href*="_tt="]').forEach((a) => {
      const url = new URL(a.href, location.href);
      url.searchParams.set("_tt", id);
      a.setAttribute("href", url.pathname + url.search + url.hash);
    });
    const url = new URL(location.href);
    url.searchParams.set("_tt", id);
    history.replaceState(history.state, "", url);
  }

  function select(id, { focus = false } = {}) {
    if (!ids.includes(id)) return;
    current = id;
    root.dataset.tableTreatment = id;
    storage.set(id);
    syncLinks(id);
    render(focus);
  }

  let switcher;

  function render(focus) {
    if (!switcher) return;
    const treatment = TREATMENTS.find((t) => t.id === current);
    switcher.querySelectorAll("button[data-tt]").forEach((button) => {
      const active = button.dataset.tt === current;
      button.setAttribute("aria-pressed", String(active));
      if (active && focus) button.focus();
    });
    switcher.querySelector(".tt-blurb").textContent = treatment.blurb;
  }

  function build() {
    annotateCells();
    const panel = document.querySelector(".above-table-panel");
    if (!panel || switcher) return;
    switcher = document.createElement("section");
    switcher.className = "tt-switcher";
    switcher.setAttribute("aria-label", "Table style prototypes");
    switcher.innerHTML = `
      <div class="tt-head">
        <span class="tt-title" id="tt-title">Table style</span>
        <span class="tt-badge">Prototype</span>
        <span class="tt-hint">Press <kbd>1</kbd>–<kbd>${TREATMENTS.length}</kbd> to switch</span>
      </div>
      <div class="tt-options" role="group" aria-labelledby="tt-title"></div>
      <p class="tt-blurb" aria-live="polite"></p>`;
    const options = switcher.querySelector(".tt-options");
    TREATMENTS.forEach((treatment, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.tt = treatment.id;
      button.title = `${treatment.label} (${index + 1})`;
      button.innerHTML = `<span class="tt-key" aria-hidden="true">${index + 1}</span>`;
      button.append(document.createTextNode(treatment.label));
      button.addEventListener("click", () => select(treatment.id));
      options.append(button);
    });
    panel.prepend(switcher);
    render(false);
  }

  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    if (target.closest && target.closest("input, textarea, select, [contenteditable], .cm-editor, dialog[open]")) return;
    const index = Number(event.key) - 1;
    if (Number.isInteger(index) && index >= 0 && index < TREATMENTS.length) {
      event.preventDefault();
      select(TREATMENTS[index].id, { focus: document.activeElement?.closest?.(".tt-switcher") != null });
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
