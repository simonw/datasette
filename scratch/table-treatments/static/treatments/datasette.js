/* Datasette treatment: clamp long values to a three-line preview.

   CSS cannot cap the height of a table cell, so while this treatment is
   active the contents of long cells get a wrapper:

     <td>
       <div class="tt-long">
         <div class="tt-long-text">…the cell's original nodes…</div>
         <button class="tt-long-toggle" aria-expanded="false">more</button>
       </div>
     </td>

   Any value taller than three lines is clamped, so no row is ever taller
   than three lines plus padding. Clicking "more" expands that value;
   Alt-click expands or collapses every clamped value in the column.
   Switching to another treatment restores the original markup. */
(() => {
  const ID = "datasette";
  const PREVIEW_LINES = 3; // keep in step with max-height: 3lh in datasette.css
  const root = document.documentElement;
  const expanded = new WeakSet(); // cells the reader has opened

  const chevron =
    '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false">' +
    '<path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  const table = () => document.querySelector("table.rows-and-columns");
  const active = () => root.dataset.tableTreatment === ID;

  function columnName(td) {
    if (td.dataset.column) return td.dataset.column;
    const th = table()?.querySelectorAll("thead th")[td.cellIndex];
    return th?.dataset.column || "value";
  }

  function setState(td, open) {
    const box = td.querySelector(":scope > .tt-long");
    if (!box) return;
    const button = box.querySelector(":scope > .tt-long-toggle");
    box.classList.toggle("is-expanded", open);
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-label", `${open ? "Show less" : "Show more"}: ${columnName(td)}`);
    button.querySelector(".tt-long-label").textContent = open ? "less" : "more";
    if (open) expanded.add(td);
    else expanded.delete(td);
  }

  function wrap(td) {
    const box = document.createElement("div");
    box.className = "tt-long";
    const text = document.createElement("div");
    text.className = "tt-long-text";
    text.append(...td.childNodes);
    box.append(text);
    td.append(box);
    return box;
  }

  function unwrap(td) {
    const box = td.querySelector(":scope > .tt-long");
    if (!box) return;
    const text = box.querySelector(":scope > .tt-long-text");
    td.append(...text.childNodes);
    box.remove();
  }

  function addToggle(td, box) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tt-long-toggle";
    button.title = "Alt-click to expand or collapse this whole column";
    button.innerHTML = `<span class="tt-long-label">more</span>${chevron}`;
    box.append(button);
    setState(td, expanded.has(td));
  }

  // Cheap filter before measuring: long text, or several line breaks.
  function isCandidate(td) {
    const text = td.textContent;
    return text.length > 120 || (text.match(/\n/g) || []).length >= PREVIEW_LINES - 1;
  }

  function setup() {
    const t = table();
    if (!t || t.dataset.ttLong === "on") return;
    t.dataset.ttLong = "on";
    // Measure each value as it renders unclamped (line breaks and all), in
    // one read pass, then wrap the ones taller than the preview.
    const lineHeight = parseFloat(getComputedStyle(t).lineHeight) || 20;
    const limit = lineHeight * PREVIEW_LINES + 1;
    const range = document.createRange();
    const tooTall = [...t.querySelectorAll("tbody td")].filter(isCandidate).filter((td) => {
      range.selectNodeContents(td);
      return range.getBoundingClientRect().height > limit;
    });
    for (const td of tooTall) addToggle(td, wrap(td));
  }

  function teardown() {
    const t = table();
    if (!t || t.dataset.ttLong !== "on") return;
    delete t.dataset.ttLong;
    t.querySelectorAll("tbody td").forEach((td) => {
      if (td.querySelector(":scope > .tt-long")) unwrap(td);
    });
  }

  function sync() {
    if (active()) setup();
    else teardown();
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest?.(".tt-long-toggle");
    if (!button || !active()) return;
    const td = button.closest("td");
    const open = !button.closest(".tt-long").classList.contains("is-expanded");
    if (event.altKey) {
      const index = td.cellIndex;
      table().querySelectorAll("tbody tr").forEach((tr) => setState(tr.cells[index], open));
    } else {
      setState(td, open);
    }
    // Collapsing a tall value can leave the reader far below its row.
    if (!open && td.getBoundingClientRect().top < 0) {
      td.scrollIntoView({ block: "nearest" });
    }
    // The pill has moved; keep focus on it without scrolling to its new spot.
    button.focus({ preventScroll: true });
  });

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (!active()) return;
      teardown();
      setup();
    }, 200);
  });

  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["data-table-treatment"] });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sync);
  } else {
    sync();
  }
})();
