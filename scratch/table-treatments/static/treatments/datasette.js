/* Datasette treatment behaviours. Both only run while
   <html data-table-treatment="datasette"> is set, and both undo themselves
   completely when another treatment is chosen.

   1. Long values: a short preview.

   CSS cannot cap the height of a table cell, so the contents of long cells
   get a wrapper:

     <td>
       <div class="tt-long">
         <div class="tt-long-text">…the cell's original nodes…</div>
         <button class="tt-long-toggle" aria-expanded="false">expand</button>
       </div>
     </td>

   Any value taller than the preview (--tt-preview-lines in datasette.css,
   3 by default) is clamped, so no row is taller than that plus padding.
   The preview folds line breaks into spaces, unless the folded value would
   fit entirely, in which case it keeps them (.keeps-breaks) so "expand"
   always reveals something. "expand" opens one value and moves to its top
   right as "collapse". The column's gear menu gets "Expand/Collapse long values";
   Alt-click on a pill is a shortcut for the same. Rows that Datasette's
   edit and insert dialogs swap in are clamped too.

   2. Freeze: a pin button in the primary key column's header.

   Pressing it freezes the header row and the row's identity columns (the
   leading run of Link and primary key columns: one column for most tables,
   Link + rowid for rowid tables, Link + every key column for compound keys).
   The table card becomes a scroll box the height of the viewport so the
   header can stick to it. The choice is remembered for every table. */
(() => {
  const ID = "datasette";
  const MAX_FROZEN = 4; // datasette.css has sticky rules for up to four columns
  const FREEZE_KEY = "datasette-table-freeze";
  const root = document.documentElement;
  const expanded = new WeakSet(); // cells the reader has opened

  const chevron =
    '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false">' +
    '<path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  // Lucide "pin" (ISC licence), the same stroke style as Datasette's gear.
  const pinIcon =
    '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12 17v5"/>' +
    '<path class="tt-pin-head" d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>';

  const table = () => document.querySelector("table.rows-and-columns");
  const active = () => root.dataset.tableTreatment === ID;
  const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const rows = (t) => [...(t.tBodies[0]?.rows || [])];

  /* Scrolling helpers (shared) ----------------------------------------- */

  // While frozen, the card is the vertical scroller; otherwise the window.
  function scrollByY(t, dy) {
    if (Math.abs(dy) < 1) return;
    const scroller = t.dataset.ttFreeze ? t.closest(".table-wrapper") : window;
    scroller.scrollBy({ top: dy, behavior: "instant" });
  }

  // Run fn without moving `anchor` on screen, whatever grows or shrinks.
  function keepInPlace(t, anchor, fn) {
    const before = anchor?.getBoundingClientRect().top;
    fn();
    if (anchor?.isConnected) scrollByY(t, anchor.getBoundingClientRect().top - before);
  }

  // Lowest viewport y at which a body row is not hidden behind the header.
  function visibleTop(t) {
    if (!t?.dataset.ttFreeze) return 0;
    const wrapper = t.closest(".table-wrapper");
    return Math.max(0, wrapper.getBoundingClientRect().top + t.tHead.offsetHeight);
  }

  function firstVisibleRow(t) {
    const top = visibleTop(t);
    return rows(t).find((tr) => tr.getBoundingClientRect().bottom > top);
  }

  /* Header controls (shared) -------------------------------------------- */

  // Our header buttons sit after Datasette's gear, in this order. table.js
  // appends the gear after load, so each header with controls is watched
  // and re-ordered when its children change.
  const CONTROLS = ["tt-col-expand", "tt-pin"];
  const headerObservers = new Map();

  function arrangeControls(th) {
    const mine = CONTROLS.map((name) => th.querySelector(`:scope > .${name}`)).filter(Boolean);
    const tail = [...th.children].slice(-mine.length);
    if (mine.some((el, i) => tail[i] !== el)) th.append(...mine);
  }

  function watchHeader(th) {
    arrangeControls(th);
    if (headerObservers.has(th)) return;
    const observer = new MutationObserver(() => arrangeControls(th));
    observer.observe(th, { childList: true });
    headerObservers.set(th, observer);
  }

  function unwatchHeader(th) {
    if (CONTROLS.some((name) => th.querySelector(`:scope > .${name}`))) return;
    headerObservers.get(th)?.disconnect();
    headerObservers.delete(th);
  }

  /* 1. Long values ------------------------------------------------------ */

  function previewLines(t) {
    return parseFloat(getComputedStyle(t).getPropertyValue("--tt-preview-lines")) || 3;
  }

  function columnName(td) {
    if (td.dataset.column) return td.dataset.column;
    const th = table()?.tHead?.rows[0]?.cells[td.cellIndex];
    return th?.dataset.column || "value";
  }

  function rowName(td) {
    const tr = td.closest("tr");
    const label = tr.dataset.rowLabel || (tr.querySelector('td[data-is-pk="1"]') || tr.cells[0])?.textContent;
    return (label || "").trim().slice(0, 60);
  }

  function setState(td, open) {
    const box = td?.querySelector(":scope > .tt-long");
    if (!box) return;
    const button = box.querySelector(":scope > .tt-long-toggle");
    box.classList.toggle("is-expanded", open);
    // Expanded, the pill sits at the top right of the value, where the reader
    // is; collapsed, at the end of the preview's last line.
    if (open && box.firstElementChild !== button) box.prepend(button);
    if (!open && box.lastElementChild !== button) box.append(button);
    const word = open ? "collapse" : "expand";
    button.setAttribute("aria-expanded", String(open));
    const row = rowName(td);
    button.setAttribute("aria-label", `${word}: ${columnName(td)}${row ? `, ${row}` : ""}`);
    button.querySelector(".tt-long-label").textContent = word;
    if (open) expanded.add(td);
    else expanded.delete(td);
  }

  function setColumn(t, index, open) {
    rows(t).forEach((tr) => setState(tr.cells[index], open));
    updateColumnToggle(t, index);
  }

  // Expand-all toggle in the header of every column with a clamped value: a
  // pill like the cells' "expand" pills, so it reads as part of the same
  // feature (an icon here looked like a sort control). data-state is none /
  // some / all expanded; the text says what a click will do: "collapse all"
  // when all are open, "expand all" otherwise.
  function updateColumnToggle(t, index) {
    const th = t.tHead?.rows[0]?.cells[index];
    if (!th) return;
    const boxes = rows(t).map((tr) => tr.cells[index]?.querySelector(":scope > .tt-long")).filter(Boolean);
    let button = th.querySelector(":scope > .tt-col-expand");
    if (!boxes.length) {
      if (button) {
        button.remove();
        unwatchHeader(th);
      }
      return;
    }
    const column = th.dataset.column || "column";
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "tt-col-expand";
      button.innerHTML = `<span class="tt-col-expand-label">expand all</span>${chevron}`;
      th.append(button);
      watchHeader(th);
    }
    const open = boxes.filter((box) => box.classList.contains("is-expanded")).length;
    const state = open === 0 ? "none" : open === boxes.length ? "all" : "some";
    button.dataset.state = state;
    button.querySelector(".tt-col-expand-label").textContent = state === "all" ? "collapse all" : "expand all";
    button.setAttribute("aria-label", `${state === "all" ? "Collapse" : "Expand"} all ${column} values`);
    button.title =
      state === "all"
        ? `Collapse all ${column} values\nReturn every long ${column} value on this page to a short preview.`
        : `Expand all ${column} values\nShow every long ${column} value on this page in full.`;
  }

  function updateColumnToggles(t) {
    [...(t.tHead?.rows[0]?.cells || [])].forEach((_, index) => updateColumnToggle(t, index));
  }

  function removeColumnToggles(t) {
    t.querySelectorAll("thead .tt-col-expand").forEach((button) => {
      const th = button.parentElement;
      button.remove();
      unwatchHeader(th);
    });
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
    button.innerHTML = `<span class="tt-long-label">expand</span>${chevron}`;
    box.append(button);
    setState(td, expanded.has(td));
  }

  // Skip measuring cells that cannot reach three lines at any width.
  function mightBeTall(td) {
    const text = td.textContent;
    return text.length > 20 || text.includes("\n");
  }

  // Measure cells as they render unclamped (line breaks and all) and wrap
  // the ones taller than the preview. Layout reads and DOM writes are
  // batched: one read pass, one write pass, one read pass, one write pass.
  function clampRows(t, trs) {
    const lineHeight = parseFloat(getComputedStyle(t).lineHeight) || 20;
    const limit = lineHeight * previewLines(t) + 1;
    const range = document.createRange();
    const tall = trs
      .flatMap((tr) => [...tr.cells])
      .filter((td) => !td.querySelector(":scope > .tt-long") && mightBeTall(td))
      .filter((td) => {
        range.selectNodeContents(td);
        return range.getBoundingClientRect().height > limit;
      });
    const boxes = tall.map((td) => [td, wrap(td)]);
    const fitsFolded = boxes.map(([, box]) => box.firstElementChild.scrollHeight <= limit);
    boxes.forEach(([td, box], i) => {
      if (fitsFolded[i]) box.classList.add("keeps-breaks");
      addToggle(td, box);
    });
    updateColumnToggles(t);
  }

  let rowObserver = null;

  function setupClamp(t) {
    if (t.dataset.ttLong === "on") return;
    t.dataset.ttLong = "on";
    clampRows(t, rows(t));
    // Datasette's edit and insert dialogs swap rows in after load.
    if (t.tBodies[0]) {
      rowObserver = new MutationObserver((records) => {
        const added = records.flatMap((r) => [...r.addedNodes]).filter((n) => n.nodeName === "TR");
        if (added.length) clampRows(t, added);
      });
      rowObserver.observe(t.tBodies[0], { childList: true });
    }
  }

  function teardownClamp(t) {
    if (t.dataset.ttLong !== "on") return;
    delete t.dataset.ttLong;
    rowObserver?.disconnect();
    rowObserver = null;
    removeColumnToggles(t);
    t.querySelectorAll("tbody td").forEach((td) => {
      if (td.querySelector(":scope > .tt-long")) unwrap(td);
    });
  }

  // The header's expand-all toggle. Keeps the first visible row in place.
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.(".tt-col-expand");
    if (!button || !active()) return;
    const t = table();
    const open = button.dataset.state !== "all";
    keepInPlace(t, firstVisibleRow(t), () => setColumn(t, button.closest("th").cellIndex, open));
    button.focus({ preventScroll: true });
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest?.(".tt-long-toggle");
    if (!button || !active()) return;
    const t = table();
    const td = button.closest("td");
    const tr = td.closest("tr");
    const open = !button.closest(".tt-long").classList.contains("is-expanded");
    // Keep the clicked row where it is, even when Alt-click expands every
    // value above it.
    keepInPlace(t, tr, () => {
      if (event.altKey) setColumn(t, td.cellIndex, open);
      else setState(td, open);
    });
    updateColumnToggle(t, td.cellIndex);
    // Collapsing a value whose top had scrolled out of view: bring its row
    // back to the top.
    const top = visibleTop(t);
    if (!open && tr.getBoundingClientRect().top < top) {
      scrollByY(t, tr.getBoundingClientRect().top - top);
    }
    // The pill has moved; keep focus on it without scrolling to its new spot.
    button.focus({ preventScroll: true });
  });

  // Copying from a collapsed preview would otherwise lose the value's line
  // breaks, which are only folded for display.
  document.addEventListener("copy", (event) => {
    if (!active()) return;
    const selection = getSelection();
    if (!selection.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    const node = range.commonAncestorContainer;
    const text = (node.nodeType === 1 ? node : node.parentElement)?.closest(".tt-long-text");
    const box = text?.parentElement;
    if (!box || box.classList.contains("is-expanded") || box.classList.contains("keeps-breaks")) return;
    event.clipboardData.setData("text/plain", range.cloneContents().textContent);
    event.preventDefault();
  });

  // "Expand long values" / "Collapse long values" in the column's gear menu,
  // through Datasette's JavaScript plugin API. Called each time a menu opens.
  document.addEventListener("datasette_init", (event) => {
    event.detail.registerPlugin("table-treatment-datasette", {
      version: 0.1,
      makeColumnActions(column) {
        const t = table();
        if (!active() || !t?.tHead) return [];
        const index = [...t.tHead.rows[0].cells].findIndex((th) => th.dataset.column === column.columnName);
        if (index < 0) return [];
        const boxes = rows(t).map((tr) => tr.cells[index]?.querySelector(":scope > .tt-long")).filter(Boolean);
        if (!boxes.length) return [];
        const open = boxes.some((box) => !box.classList.contains("is-expanded"));
        return [
          {
            label: open ? "Expand long values" : "Collapse long values",
            onClick(evt) {
              evt.preventDefault();
              keepInPlace(t, firstVisibleRow(t), () => setColumn(t, index, open));
            },
          },
        ];
      },
    });
  });

  /* 2. Freeze ----------------------------------------------------------- */

  const prefs = {
    frozen() {
      try { return localStorage.getItem(FREEZE_KEY) === "1"; } catch (e) { return false; }
    },
    setFrozen(on) {
      try { localStorage.setItem(FREEZE_KEY, on ? "1" : "0"); } catch (e) {}
    },
  };

  let freeze = null; // { t, wrapper, run, pin, observers, onScroll }

  // The leading run of Link and primary key headers, if it has a key in it.
  function keyHeaders(t) {
    const run = [];
    for (const th of t.tHead?.rows[0]?.cells || []) {
      if (th.dataset.isPk === "1" || th.dataset.isLinkColumn === "1") run.push(th);
      else break;
    }
    return run.some((th) => th.dataset.isPk === "1") ? run.slice(0, MAX_FROZEN) : [];
  }

  function measureFreeze() {
    if (!freeze || !freeze.t.dataset.ttFreeze) return;
    const { t, wrapper, run } = freeze;
    // Differences between offsets, so this is right even mid-scroll.
    const origin = run[0].offsetLeft;
    run.forEach((th, i) => t.style.setProperty(`--tt-freeze-left-${i + 1}`, `${th.offsetLeft - origin}px`));
    const last = run[run.length - 1];
    wrapper.style.setProperty("--tt-freeze-width", `${last.offsetLeft - origin + last.offsetWidth}px`);
    wrapper.style.setProperty("--tt-freeze-top", `${t.tHead.offsetHeight}px`);
  }

  function updateScrolled() {
    if (!freeze) return;
    const { wrapper } = freeze;
    wrapper.classList.toggle("is-scrolled-x", wrapper.scrollLeft > 0);
    wrapper.classList.toggle("is-scrolled-y", wrapper.scrollTop > 0);
  }

  function clearInlineStyle(el, names) {
    names.forEach((name) => el.style.removeProperty(name));
    if (!el.getAttribute("style")) el.removeAttribute("style");
  }

  // After pinning, glide the box a little in each direction it can scroll
  // but hasn't yet, so content visibly slides under the frozen header and
  // columns and their shadows appear: it shows what pinning did.
  function nudge() {
    if (!freeze || !freeze.t.dataset.ttFreeze) return;
    const { wrapper } = freeze;
    const room = (scroll, size, client) => (scroll === 0 ? Math.max(0, size - client) : 0);
    const left = Math.min(48, room(wrapper.scrollLeft, wrapper.scrollWidth, wrapper.clientWidth));
    const top = Math.min(24, room(wrapper.scrollTop, wrapper.scrollHeight, wrapper.clientHeight));
    if (left || top) wrapper.scrollBy({ left, top, behavior: reducedMotion() ? "instant" : "smooth" });
  }

  function applyFreeze(on, { keepPlace = false } = {}) {
    const { t, wrapper, run, pin, describe } = freeze;
    // Keep the rows the reader is looking at in the same place on screen.
    const topBefore = wrapper.getBoundingClientRect().top;
    const innerBefore = wrapper.scrollTop;
    pin.setAttribute("aria-pressed", String(on));
    pin.title = describe(on);
    if (on) {
      t.dataset.ttFreeze = String(run.length);
      measureFreeze();
    } else {
      delete t.dataset.ttFreeze;
      clearInlineStyle(t, run.map((_, i) => `--tt-freeze-left-${i + 1}`));
      clearInlineStyle(wrapper, ["--tt-freeze-width", "--tt-freeze-top"]);
    }
    updateScrolled();
    if (!keepPlace) return;
    if (on) {
      // Hand the page's scroll position over to the box, then line the box
      // up with the top of the window so it fills the screen.
      if (topBefore < 0) wrapper.scrollTop = -topBefore;
      const top = wrapper.getBoundingClientRect().top - 8;
      const smooth = topBefore >= 0 && !reducedMotion();
      if (Math.abs(top) > 1) window.scrollBy({ top, behavior: smooth ? "smooth" : "instant" });
      // The box is its own scroller, so its nudge runs alongside the page's
      // glide rather than after it.
      nudge();
    } else if (innerBefore > 0) {
      // The box's own scroll position becomes page scroll again.
      window.scrollBy({ top: innerBefore + (wrapper.getBoundingClientRect().top - topBefore), behavior: "instant" });
    }
  }

  function setupFreeze(t) {
    if (freeze) return;
    const run = keyHeaders(t);
    const wrapper = t.closest(".table-wrapper");
    if (!run.length || !wrapper) return;
    const keys = run.filter((th) => th.dataset.isPk === "1");
    const th = keys[0];
    const plural = keys.length > 1;
    const names = keys.map((k) => k.dataset.column).join(" and ");
    const columns = `the ${names} column${plural ? "s" : ""}`;

    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "tt-pin";
    pin.setAttribute("aria-label", `Freeze the header row and ${columns}`);
    pin.innerHTML = pinIcon;
    th.append(pin);
    watchHeader(th);

    // Column widths change with the viewport, fonts and expanded values.
    const resize = new ResizeObserver(measureFreeze);
    run.forEach((h) => resize.observe(h));
    resize.observe(t.tHead);

    const onScroll = () => updateScrolled();
    wrapper.addEventListener("scroll", onScroll, { passive: true });

    pin.addEventListener("click", () => {
      const on = pin.getAttribute("aria-pressed") !== "true";
      prefs.setFrozen(on);
      applyFreeze(on, { keepPlace: true });
      pin.focus({ preventScroll: true });
    });

    // An explanatory tooltip that follows the pin's state (set in applyFreeze).
    const describe = (on) =>
      on
        ? `Unfreeze the header row and ${columns}\nColumn names stay at the top and ${columns} ${plural ? "stay" : "stays"} on the left while you scroll. Click to let them scroll with the table again.`
        : `Freeze the header row and ${columns}\nKeep column names at the top and ${columns} on the left while you scroll down the rows and across the columns. Applies to every table until you turn it off.`;

    freeze = { t, wrapper, run, pin, describe, observers: [resize], onScroll };
    applyFreeze(prefs.frozen());
  }

  function teardownFreeze() {
    if (!freeze) return;
    const { wrapper, pin, observers, onScroll } = freeze;
    applyFreeze(false);
    observers.forEach((o) => o.disconnect());
    wrapper.removeEventListener("scroll", onScroll);
    wrapper.classList.remove("is-scrolled-x", "is-scrolled-y");
    const th = pin.parentElement;
    pin.remove();
    if (th) unwatchHeader(th);
    freeze = null;
  }

  /* Wiring -------------------------------------------------------------- */

  function sync() {
    const t = table();
    if (!t) return;
    if (active()) {
      setupClamp(t);
      setupFreeze(t);
    } else {
      teardownFreeze();
      teardownClamp(t);
    }
  }

  // Re-measure long values only when the width changes (a height-only
  // resize cannot change line wrapping), and keep focus on a pill.
  let resizeTimer;
  let lastWidth = innerWidth;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (innerWidth === lastWidth) return;
      lastWidth = innerWidth;
      const t = table();
      if (!t || !active()) return;
      const focused = document.activeElement?.closest?.(".tt-long-toggle")?.closest("td");
      teardownClamp(t);
      setupClamp(t);
      focused?.querySelector(".tt-long-toggle")?.focus({ preventScroll: true });
    }, 200);
  });

  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["data-table-treatment"] });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sync);
  } else {
    sync();
  }
})();
