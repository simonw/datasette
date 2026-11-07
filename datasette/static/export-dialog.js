/* "Export data" dialog on table and query pages, from _export_dialog.html.

   The template wires the buttons up with commandfor/command so the dialog
   opens without JavaScript too. View and Download are left to the browser:
   plain links, using the download attribute, or for CSV, form submit buttons
   with _dl=on. Downloads therefore stream straight to disk and show in the
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

  function setState(el, state) {
    clearTimeout(resetTimers.get(el));
    el.dataset.state = state;
    if (state === "done" || state === "error") {
      resetTimers.set(
        el,
        setTimeout(() => setState(el, "idle"), RESET_AFTER_MS),
      );
    }
  }

  // The CSV row builds its URL from its form, so the checkboxes apply
  function csvUrl(form) {
    const params = new URLSearchParams(new FormData(form));
    return new URL(`${form.getAttribute("action")}?${params}`, location.href)
      .href;
  }

  function exportUrl(el) {
    const form = el.closest(".export-csv-form");
    if (form) return csvUrl(form);
    const view = el
      .closest(".export-option")
      .querySelector('[data-export-action="view"]');
    return new URL(view.getAttribute("href"), location.href).href;
  }

  function isText(contentType) {
    return !contentType || /^text\/|json|xml|javascript|csv/i.test(contentType);
  }

  async function fetchText(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    if (!isText(response.headers.get("content-type"))) {
      throw new Error("not a text format");
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

  async function copyData(url) {
    // Safari only allows clipboard writes during the click, so hand it a
    // promise for the data rather than awaiting the fetch first.
    if (window.ClipboardItem && navigator.clipboard.write) {
      const blob = fetchText(url).then(
        (text) => new Blob([text], { type: "text/plain" }),
      );
      blob.catch(() => {}); // Reported below instead
      try {
        await navigator.clipboard.write([
          new ClipboardItem({ "text/plain": blob }),
        ]);
      } catch (ex) {
        // Firefox and Safari replace a failed fetch with a generic error
        await blob;
        throw ex;
      }
    } else {
      await navigator.clipboard.writeText(await fetchText(url));
    }
  }

  const formatNumber = (n) => n.toLocaleString("en-US");
  const rowsText = (n) => `${formatNumber(n)} row${n === 1 ? "" : "s"}`;

  // How many rows an export returns: JSON and plugin formats return this
  // page, CSV asks for _size=max, and streamed CSV returns every row.
  // Mirrors the rows_label macro in _export_dialog.html.
  function rowsLabel(kind, rows, stream) {
    const { page, more, max, total } = rows;
    if (kind === "csv" && stream) {
      return {
        text: total === null ? "All rows" : `All ${rowsText(total)}`,
        partial: false,
      };
    }
    let n = page;
    if (kind === "csv" && more) {
      if (total !== null) {
        n = Math.min(total, max);
      } else if (page < max) {
        return { text: `Up to ${rowsText(max)}`, partial: true };
      } else {
        n = max;
      }
    }
    if (n === 0) return { text: "No rows", partial: false };
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
    const { rowsPage, rowsMore, rowsMax, rowsTotal } = dialog.dataset;
    return {
      page: Number(rowsPage),
      more: rowsMore === "true",
      max: Number(rowsMax),
      total: rowsTotal ? Number(rowsTotal) : null,
    };
  }

  function renderRows(dialog, rows, counting = false) {
    const stream = dialog.querySelector('input[name="_stream"]');
    if (stream && rows.total !== null && rows.total <= rows.max) {
      // CSV already includes every row, so the option would do nothing
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
    const streamLabel = dialog.querySelector(".export-stream-label");
    if (streamLabel) {
      streamLabel.textContent =
        rows.total !== null
          ? `Include all ${rowsText(rows.total)}`
          : counting
            ? "Include all rows (counting…)"
            : "Include all rows";
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
      // Keep the labels that don't depend on the count
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
    const open = () => {
      announce("");
      // Otherwise Chrome can focus the scrollable body when it overflows
      modal.show({
        returnFocusTo: button,
        initialFocus: dialog.querySelector(".export-action"),
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
        // Download the CSV through a link with a download attribute, like the
        // other formats, rather than submitting the form with _dl=on: WebKit
        // on Linux does not treat that response as a download
        ev.preventDefault();
        const link = document.createElement("a");
        link.href = `${exportUrl(el)}&_dl=on`;
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
      try {
        if (action === "copy-url") {
          await navigator.clipboard.writeText(url);
          announce(`Copied the ${name} URL`);
        } else {
          setState(el, "busy");
          await copyData(url);
          announce(`Copied ${name} data`);
        }
        setState(el, "done");
      } catch (ex) {
        setState(el, "error");
        announce(
          `Could not ${action.replace("-", " ")} ${name}: ${ex.message}`,
        );
      }
    });

    // Links to the old "Advanced export" box used #export. Drop it on close
    // so that reloading the page or going back does not reopen the dialog.
    if (location.hash === "#export") {
      open();
    }
    dialog.addEventListener("close", () => {
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
