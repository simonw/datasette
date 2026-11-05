/* Datasette treatment behaviours. Both only run while
   <html data-table-treatment="datasette"> is set, and both undo themselves
   completely when another treatment is chosen.

   1. Long values: a three-line preview.

   CSS cannot cap the height of a table cell, so the contents of long cells
   get a wrapper:

     <td>
       <div class="tt-long">
         <div class="tt-long-text">…the cell's original nodes…</div>
         <button class="tt-long-toggle" aria-expanded="false">more</button>
       </div>
     </td>

   Any value taller than three lines is clamped, so no row is ever taller
   than three lines plus padding. Clicking "more" expands that value;
   Alt-click expands or collapses every clamped value in the column.

   2. Freeze: a pin button in the primary key column's header.

   Pressing it freezes the header row and the row's identity columns (the
   leading run of Link and primary key columns: one column for most tables,
   Link + rowid for rowid tables, Link + every key column for compound keys).
   The table card becomes a scroll box the height of the viewport so the
   header can stick to it. The choice is remembered for every table. */
(() => {
  const ID = "datasette";
  const PREVIEW_LINES = 3; // keep in step with max-height: 3lh in datasette.css
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

  /* 1. Long values ------------------------------------------------------ */

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

  function setupClamp(t) {
    if (t.dataset.ttLong === "on") return;
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

  function teardownClamp(t) {
    if (t.dataset.ttLong !== "on") return;
    delete t.dataset.ttLong;
    t.querySelectorAll("tbody td").forEach((td) => {
      if (td.querySelector(":scope > .tt-long")) unwrap(td);
    });
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
    if (!open && td.getBoundingClientRect().top < visibleTop(table())) {
      td.scrollIntoView({ block: "nearest" });
    }
    // The pill has moved; keep focus on it without scrolling to its new spot.
    button.focus({ preventScroll: true });
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

  // Lowest viewport y at which a body row is not hidden behind the header.
  function visibleTop(t) {
    if (!t?.dataset.ttFreeze) return 0;
    const wrapper = t.closest(".table-wrapper");
    return Math.max(0, wrapper.getBoundingClientRect().top + t.tHead.offsetHeight);
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

  function applyFreeze(on, { keepPlace = false } = {}) {
    const { t, wrapper, run, pin } = freeze;
    // Keep the rows the reader is looking at in the same place on screen.
    const topBefore = wrapper.getBoundingClientRect().top;
    const innerBefore = wrapper.scrollTop;
    pin.setAttribute("aria-pressed", String(on));
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
      if (Math.abs(top) > 1) {
        window.scrollBy({ top, behavior: topBefore < 0 || reducedMotion() ? "instant" : "smooth" });
      }
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
    const names = keys.map((k) => k.dataset.column).join(" and ");
    const label = `Freeze the header row and the ${names} column${keys.length > 1 ? "s" : ""}`;

    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "tt-pin";
    pin.setAttribute("aria-label", label);
    pin.title = label;
    pin.innerHTML = pinIcon;
    th.append(pin);

    // Datasette's table.js appends the column gear after load; keep the pin
    // after it so the gear sits in the same place in every header.
    const keepLast = new MutationObserver(() => {
      if (th.lastElementChild !== pin) th.append(pin);
    });
    keepLast.observe(th, { childList: true });

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

    freeze = { t, wrapper, run, pin, observers: [keepLast, resize], onScroll };
    applyFreeze(prefs.frozen());
  }

  function teardownFreeze() {
    if (!freeze) return;
    const { wrapper, pin, observers, onScroll } = freeze;
    applyFreeze(false);
    observers.forEach((o) => o.disconnect());
    wrapper.removeEventListener("scroll", onScroll);
    wrapper.classList.remove("is-scrolled-x", "is-scrolled-y");
    if (!wrapper.getAttribute("class")) wrapper.removeAttribute("class");
    pin.remove();
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

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const t = table();
      if (!t || !active()) return;
      teardownClamp(t);
      setupClamp(t);
    }, 200);
  });

  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["data-table-treatment"] });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sync);
  } else {
    sync();
  }
})();
