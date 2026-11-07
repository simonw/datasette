/* "Export data" dialog on table, query and row pages, from _export_dialog.html.

   The template wires the buttons up with commandfor/command so the dialog
   opens without JavaScript too. View and Download are left to the browser:
   plain links, using the download attribute, or for TSV and CSV, submit
   buttons for the form that holds their shared options. Downloads therefore stream straight to disk and show in the
   browser's own download list. Here we route the dialog through
   DatasetteModal, reveal the Copy and Copy URL actions when the clipboard is
   available, and keep the row counts up to date. */
(() => {
  const RESET_AFTER_MS = 1500;
  // Copy holds the whole export in memory and on the clipboard, so give up
  // on anything bigger than this and suggest Download instead
  const MAX_COPY_BYTES = 10 * 1024 * 1024;
  const resetTimers = new WeakMap();

  // Extra labels for each action, beyond the label in the template. They are
  // stacked in one grid cell so the button keeps the width of the longest.
  const STATE_LABELS = {
    copy: { busy: "Copying…", done: "Copied", error: "Failed" },
    "copy-url": { done: "Copied", error: "Failed" },
  };

  function addStateLabels(el, labels) {
    const idle = el.textContent.trim();
    el.textContent = "";
    for (const [state, text] of Object.entries({ idle, ...labels })) {
      const span = document.createElement("span");
      span.className = "export-action-label";
      span.dataset.state = state;
      span.textContent = text;
      el.append(span);
    }
    el.dataset.state = "idle";
  }

  // "Copied" resets after a moment; "Failed" stays until the next attempt
  function setState(el, state) {
    clearTimeout(resetTimers.get(el));
    el.dataset.state = state;
    if (state === "done") {
      resetTimers.set(
        el,
        setTimeout(() => setState(el, "idle"), RESET_AFTER_MS),
      );
    }
  }

  // Why an action failed, shown under its row
  function errorFor(el) {
    const actions = el.closest(".export-actions");
    return actions.parentElement.querySelector(
      `:scope > .export-option-error[data-action="${el.dataset.exportAction}"]`,
    );
  }

  function showError(el, message) {
    let error = errorFor(el);
    if (!error) {
      error = document.createElement("p");
      error.className = "export-option-error";
      error.dataset.action = el.dataset.exportAction;
      el.closest(".export-actions").after(error);
    }
    error.textContent = message;
  }

  function clearError(el) {
    errorFor(el)?.remove();
  }

  // TSV and CSV build their URLs from the form they share, so its checkboxes
  // apply. TSV's buttons send the form to its own path using formaction.
  function exportUrl(el) {
    const row = el.closest(".export-option");
    const form = el.closest(".export-csv-form");
    if (!form) {
      // The View or Download link: binary formats have no View, and web
      // pages no Download
      const link = row.querySelector("a.export-action[href]");
      return new URL(link.getAttribute("href"), location.href).href;
    }
    const view = row.querySelector('[data-export-action="view"]');
    const path = view.getAttribute("formaction") || form.getAttribute("action");
    const url = new URL(path, location.href);
    // Row pages have no fields to send, so no "?" either
    url.search = new URLSearchParams(new FormData(form));
    return url.href;
  }

  function isText(contentType) {
    return !contentType || /^text\/|json|xml|javascript|csv/i.test(contentType);
  }

  async function fetchText(url, signal) {
    const response = await fetch(url, { credentials: "same-origin", signal });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const contentType = response.headers.get("content-type");
    if (/^text\/html/i.test(contentType || "")) {
      // A plugin format that shows a page rather than returning data
      throw new Error("this format is a web page, use View");
    }
    if (!isText(contentType)) {
      throw new Error("not a text format, use Download");
    }
    if (!response.body) return response.text();
    // Read as it arrives, so a huge export is abandoned at the limit rather
    // than loaded in full first
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_COPY_BYTES) {
        reader.cancel();
        throw new Error(
          `over ${MAX_COPY_BYTES / 1024 / 1024} MB is too much to copy, use Download`,
        );
      }
      chunks.push(value);
    }
    return new Blob(chunks).text();
  }

  async function copyData(url, controller) {
    // Safari only allows clipboard writes during the click, so hand it a
    // promise for the data rather than awaiting the fetch first.
    if (window.ClipboardItem && navigator.clipboard.write) {
      let fetchError = null;
      const blob = fetchText(url, controller.signal).then(
        (text) => new Blob([text], { type: "text/plain" }),
        (error) => {
          fetchError = error;
          throw error;
        },
      );
      blob.catch(() => {}); // Reported below instead
      try {
        await navigator.clipboard.write([
          new ClipboardItem({ "text/plain": blob }),
        ]);
      } catch (ex) {
        // The clipboard can refuse straight away, so stop fetching data that
        // can no longer be copied rather than waiting for it. When the fetch
        // is what failed, Firefox and Safari report a generic clipboard error
        // instead, so report the fetch's own error.
        controller.abort();
        throw fetchError || ex;
      }
    } else {
      await navigator.clipboard.writeText(
        await fetchText(url, controller.signal),
      );
    }
  }

  const formatNumber = (n) => n.toLocaleString("en-US");
  const rowsText = (n) => `${formatNumber(n)} row${n === 1 ? "" : "s"}`;

  // How many rows an export returns: JSON and plugin formats return the rows
  // on this page, TSV and CSV ("csv" here) ask for _size=max starting from
  // this page, and "Include all rows" streams every row from the first one.
  // Mirrors the rows_label macro in _export_dialog.html.
  function rowsLabel(kind, rows, stream) {
    const { page, more, later, max, total } = rows;
    if (kind === "csv" && stream) {
      return {
        text: total === null ? "All rows" : `All ${rowsText(total)}`,
        partial: false,
      };
    }
    let n = page;
    if (kind === "csv" && more) {
      if (later || (total === null && page < max)) {
        return {
          text: `Up to ${rowsText(max)}${later ? " from this page" : ""}`,
          partial: true,
        };
      }
      n = total === null ? max : Math.min(total, max);
    }
    if (n === 0) return { text: "No rows", partial: false };
    if (later) return { text: `${rowsText(n)} on this page`, partial: true };
    if (!more || (total !== null && n >= total)) {
      return {
        text: `All ${rowsText(total === null ? n : total)}`,
        partial: false,
      };
    }
    return {
      text:
        total === null
          ? `First ${rowsText(n)}`
          : `First ${formatNumber(n)} of ${rowsText(total)}`,
      partial: true,
    };
  }

  function readRows(dialog) {
    const { rowsPage, rowsMore, rowsLater, rowsMax, rowsTotal } =
      dialog.dataset;
    return {
      page: Number(rowsPage),
      more: rowsMore === "true",
      later: rowsLater === "true",
      max: Number(rowsMax),
      total: rowsTotal ? Number(rowsTotal) : null,
    };
  }

  function renderRows(dialog, rows, counting = false) {
    const stream = dialog.querySelector('input[name="_stream"]');
    if (
      stream &&
      !rows.later &&
      rows.total !== null &&
      rows.total <= rows.max
    ) {
      // TSV and CSV already include every row, so the option would do nothing
      stream.checked = false;
      stream.disabled = true;
      stream.closest("label").hidden = true;
      const options = stream.closest(".export-csv-options");
      if (!options.querySelector("label:not([hidden])")) options.hidden = true;
    }
    dialog.querySelectorAll("[data-export-rows]").forEach((el) => {
      const { text, partial } = rowsLabel(
        el.dataset.exportRows,
        rows,
        !!(stream && stream.checked),
      );
      el.textContent = text;
      el.dataset.partial = String(partial);
    });
    // "Include all rows" starts from the first row, so it leaves out the
    // page's ?_next= cursor - which streaming would reject anyway
    const cursor = dialog.querySelector('.export-csv-form input[name="_next"]');
    if (cursor) cursor.disabled = !!(stream && stream.checked);
    const streamLabel = dialog.querySelector(".export-stream-label");
    if (streamLabel) {
      const fromStart = rows.later ? " from the first page" : "";
      streamLabel.textContent =
        rows.total !== null
          ? `Include all ${rowsText(rows.total)}${fromStart}`
          : counting
            ? "Include all rows (counting…)"
            : `Include all rows${fromStart}`;
    }
  }

  // The page's count can be missing or capped at Datasette's count limit,
  // so count the rows once the dialog is open. countTableRows() from
  // table.js shares one request with the page header's "count all" button.
  async function fetchCount(dialog, rows) {
    const url = dialog.dataset.countUrl;
    delete dialog.dataset.countUrl;
    if (typeof window.countTableRows !== "function") return;
    renderRows(dialog, rows, true);
    try {
      rows.total = await window.countTableRows(url);
      dialog.dataset.rowsTotal = String(rows.total);
    } catch (ex) {
      // Keep the labels that don't depend on the count, and try again the
      // next time the dialog opens
      dialog.dataset.countUrl = url;
    }
    renderRows(dialog, rows);
  }

  function initExportDialog() {
    const button = document.querySelector(".export-data-button");
    const dialog = document.getElementById("export-dialog");
    const modal = dialog && dialog.closest("datasette-modal");
    if (!button || !modal || typeof modal.show !== "function") {
      return;
    }
    const status = dialog.querySelector(".export-status");
    const announce = (message) => {
      if (status) status.textContent = message;
    };
    const rows = readRows(dialog);
    const pendingCopies = new Set();
    const open = () => {
      announce("");
      // Start afresh, without failures from last time
      dialog
        .querySelectorAll('.export-action[data-state="error"]')
        .forEach((el) => {
          setState(el, "idle");
          clearError(el);
        });
      // Otherwise Chrome can focus the scrollable body when it overflows
      modal.show({
        returnFocusTo: button,
        initialFocus: dialog.querySelector(".export-action:not([hidden])"),
      });
      if (dialog.dataset.countUrl) fetchCount(dialog, rows);
    };
    dialog
      .querySelector('input[name="_stream"]')
      ?.addEventListener("change", () => renderRows(dialog, rows));

    button.addEventListener("click", (ev) => {
      ev.preventDefault();
      open();
    });
    dialog
      .querySelector(".export-dialog-close")
      ?.addEventListener("click", (ev) => {
        ev.preventDefault();
        modal.requestClose("cancel");
      });

    const canCopy = !!(window.isSecureContext && navigator.clipboard);
    dialog.querySelectorAll("[data-export-action]").forEach((el) => {
      const action = el.dataset.exportAction;
      if (action.startsWith("copy")) el.hidden = !canCopy;
      if (STATE_LABELS[action]) addStateLabels(el, STATE_LABELS[action]);
    });

    dialog.addEventListener("click", async (ev) => {
      const el = ev.target.closest("[data-export-action]");
      const action = el && el.dataset.exportAction;
      if (action === "download" && el.closest(".export-csv-form")) {
        // Download TSV or CSV through a link with a download attribute, like
        // the other formats, rather than submitting the form with _dl=on:
        // WebKit on Linux does not treat that response as a download
        ev.preventDefault();
        const url = new URL(exportUrl(el));
        url.searchParams.set("_dl", "on");
        const link = document.createElement("a");
        link.href = url.href;
        link.download = "";
        link.hidden = true;
        // Inside the dialog: everything outside a modal dialog is inert
        dialog.append(link);
        link.click();
        link.remove();
        return;
      }
      if (!STATE_LABELS[action]) return; // View and Download: the browser's
      ev.preventDefault();
      if (el.dataset.state === "busy") return;
      const url = exportUrl(el);
      const name = el
        .closest(".export-option")
        .querySelector(".export-option-name").textContent;
      clearError(el);
      try {
        if (action === "copy-url") {
          await navigator.clipboard.writeText(url);
          announce(`Copied the ${name} URL`);
        } else {
          setState(el, "busy");
          const controller = new AbortController();
          pendingCopies.add(controller);
          try {
            await copyData(url, controller);
          } finally {
            pendingCopies.delete(controller);
          }
          announce(`Copied ${name} data`);
        }
        setState(el, "done");
      } catch (ex) {
        if (ex.name === "AbortError" && !dialog.open) {
          // Cancelled by closing the dialog
          setState(el, "idle");
          return;
        }
        setState(el, "error");
        const message = `Could not ${action.replace("-", " ")} ${name}: ${ex.message}`;
        showError(el, message);
        announce(message);
      }
    });

    // Links to the old "Advanced export" box used #export. Drop it on close
    // so that reloading the page or going back does not reopen the dialog.
    if (location.hash === "#export") {
      open();
    }
    dialog.addEventListener("close", () => {
      // Stop fetching data for copies that can no longer finish
      pendingCopies.forEach((controller) => controller.abort());
      if (location.hash === "#export") {
        history.replaceState(
          history.state,
          "",
          location.pathname + location.search,
        );
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initExportDialog);
  } else {
    initExportDialog();
  }
})();
