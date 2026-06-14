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

var SET_COLUMN_TYPE_DIALOG_ID = "set-column-type-dialog";
var setColumnTypeDialogState = null;
var ROW_DELETE_DIALOG_ID = "row-delete-dialog";
var rowDeleteDialogState = null;
var ROW_EDIT_DIALOG_ID = "row-edit-dialog";
var rowEditDialogState = null;

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

function getSetColumnTypeData() {
  return window._setColumnTypeData || null;
}

function getSetColumnTypeConfig(column) {
  var data = getSetColumnTypeData();
  if (!data || !data.columns) {
    return null;
  }
  return data.columns[column] || null;
}

function canSetColumnType() {
  return !!(getSetColumnTypeData() && window.HTMLDialogElement && window.fetch);
}

function setColumnTypeActionLabel(column) {
  var columnConfig = getSetColumnTypeConfig(column);
  if (!columnConfig) {
    return null;
  }
  return columnConfig.current
    ? `Custom type: ${columnConfig.current.type}`
    : "Set custom type";
}

function createSetColumnTypeOption(value, name, description, checked) {
  var label = document.createElement("label");
  label.className = "set-column-type-option";

  var input = document.createElement("input");
  input.type = "radio";
  input.name = "set-column-type-choice";
  input.value = value;
  input.checked = checked;

  var content = document.createElement("span");
  content.className = "set-column-type-option-content";

  var title = document.createElement("span");
  title.className = "set-column-type-option-name";
  title.textContent = name;

  var detail = document.createElement("span");
  detail.className = "set-column-type-option-description";
  detail.textContent = description;

  content.appendChild(title);
  content.appendChild(detail);
  label.appendChild(input);
  label.appendChild(content);
  return label;
}

function setSetColumnTypeDialogBusy(state, isBusy) {
  state.isBusy = isBusy;
  state.saveButton.disabled = isBusy;
  state.cancelButton.disabled = isBusy;
  Array.from(
    state.optionsWrap.querySelectorAll('input[name="set-column-type-choice"]'),
  ).forEach(function (input) {
    input.disabled = isBusy;
  });
  state.saveButton.textContent = isBusy ? "Saving..." : "Save";
}

function clearSetColumnTypeDialogError(state) {
  state.error.hidden = true;
  state.error.textContent = "";
}

function showSetColumnTypeDialogError(state, message) {
  state.error.hidden = false;
  state.error.textContent = message;
}

function ensureSetColumnTypeDialog() {
  if (setColumnTypeDialogState) {
    return setColumnTypeDialogState;
  }
  if (!window.HTMLDialogElement) {
    return null;
  }

  var dialog = document.createElement("dialog");
  dialog.id = SET_COLUMN_TYPE_DIALOG_ID;
  dialog.className = "set-column-type-dialog";
  dialog.setAttribute("aria-labelledby", "set-column-type-title");
  dialog.innerHTML = `
    <div class="modal-header">
      <span class="modal-title" id="set-column-type-title">Set custom type</span>
      <span class="modal-meta"></span>
    </div>
    <p class="set-column-type-status"></p>
    <p class="set-column-type-error" hidden></p>
    <div class="set-column-type-options"></div>
    <div class="modal-footer">
      <span class="footer-info"></span>
      <button type="button" class="btn btn-ghost set-column-type-cancel">Cancel</button>
      <button type="button" class="btn btn-primary set-column-type-save">Save</button>
    </div>
  `;
  document.body.appendChild(dialog);

  setColumnTypeDialogState = {
    dialog: dialog,
    meta: dialog.querySelector(".modal-meta"),
    status: dialog.querySelector(".set-column-type-status"),
    error: dialog.querySelector(".set-column-type-error"),
    optionsWrap: dialog.querySelector(".set-column-type-options"),
    footerInfo: dialog.querySelector(".footer-info"),
    cancelButton: dialog.querySelector(".set-column-type-cancel"),
    saveButton: dialog.querySelector(".set-column-type-save"),
    currentColumn: null,
    currentConfig: null,
    isBusy: false,
  };

  setColumnTypeDialogState.cancelButton.addEventListener("click", function () {
    if (!setColumnTypeDialogState.isBusy) {
      dialog.close();
    }
  });

  dialog.addEventListener("click", function (ev) {
    if (ev.target === dialog && !setColumnTypeDialogState.isBusy) {
      dialog.close();
    }
  });

  dialog.addEventListener("cancel", function (ev) {
    if (setColumnTypeDialogState.isBusy) {
      ev.preventDefault();
    }
  });

  dialog.addEventListener("close", function () {
    clearSetColumnTypeDialogError(setColumnTypeDialogState);
    setSetColumnTypeDialogBusy(setColumnTypeDialogState, false);
  });

  setColumnTypeDialogState.saveButton.addEventListener("click", async function () {
    var state = setColumnTypeDialogState;
    var selected = state.dialog.querySelector(
      'input[name="set-column-type-choice"]:checked',
    );
    var selectedType = selected ? selected.value : "";
    var currentType = state.currentConfig.current
      ? state.currentConfig.current.type
      : "";

    if (selectedType === currentType) {
      state.dialog.close();
      return;
    }

    clearSetColumnTypeDialogError(state);
    setSetColumnTypeDialogBusy(state, true);

    var payload = {
      column: state.currentColumn,
      column_type: selectedType ? { type: selectedType } : null,
    };

    try {
      var response = await fetch(getSetColumnTypeData().path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });
      var data = await response.json();
      if (!response.ok || data.ok === false) {
        var message = (data.errors || ["Request failed"]).join(" ");
        throw new Error(message);
      }
      location.reload();
    } catch (error) {
      setSetColumnTypeDialogBusy(state, false);
      showSetColumnTypeDialogError(state, error.message || "Request failed");
    }
  });

  return setColumnTypeDialogState;
}

function openSetColumnTypeDialog(th) {
  var column = th.dataset.column;
  var columnConfig = getSetColumnTypeConfig(column);
  if (!columnConfig) {
    return;
  }

  var state = ensureSetColumnTypeDialog();
  if (!state) {
    return;
  }

  clearSetColumnTypeDialogError(state);
  setSetColumnTypeDialogBusy(state, false);
  state.currentColumn = column;
  state.currentConfig = columnConfig;
  state.status.textContent = `Column: ${column}`;
  state.meta.textContent = getColumnTypeText(th) || "Type unavailable";
  state.footerInfo.textContent = columnConfig.current
    ? `Current custom type: ${columnConfig.current.type}`
    : "No custom type set.";
  state.optionsWrap.innerHTML = "";

  var currentType = columnConfig.current ? columnConfig.current.type : "";
  state.optionsWrap.appendChild(
    createSetColumnTypeOption(
      "",
      "No custom type",
      "Use standard Datasette rendering without a custom type.",
      currentType === "",
    ),
  );

  columnConfig.options.forEach(function (option) {
    state.optionsWrap.appendChild(
      createSetColumnTypeOption(
        option.name,
        option.name,
        option.description,
        option.name === currentType,
      ),
    );
  });

  if (!columnConfig.options.length) {
    var emptyState = document.createElement("p");
    emptyState.className = "set-column-type-empty";
    emptyState.textContent =
      "No registered custom types are compatible with this SQLite type.";
    state.optionsWrap.appendChild(emptyState);
  }

  if (!state.dialog.open) {
    state.dialog.showModal();
  }
  var selectedOption = state.dialog.querySelector(
    'input[name="set-column-type-choice"]:checked',
  );
  if (selectedOption) {
    selectedOption.focus();
  } else {
    state.saveButton.focus();
  }
}

function ensureRowMutationStatus(manager) {
  var status = document.querySelector(".row-mutation-status");
  if (status) {
    return status;
  }

  status = document.createElement("p");
  status.className = "row-mutation-status";
  status.hidden = true;
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.setAttribute("tabindex", "-1");

  var tableWrapper = document.querySelector(manager.selectors.tableWrapper);
  if (tableWrapper && tableWrapper.parentNode) {
    tableWrapper.parentNode.insertBefore(status, tableWrapper);
  } else {
    document.body.appendChild(status);
  }
  return status;
}

function showRowMutationStatus(manager, message, isError) {
  var status = ensureRowMutationStatus(manager);
  status.hidden = false;
  status.classList.toggle("row-mutation-status-error", !!isError);
  status.textContent = message;
  return status;
}

function hideRowMutationStatus() {
  var status = document.querySelector(".row-mutation-status");
  if (!status) {
    return;
  }
  status.hidden = true;
  status.classList.remove("row-mutation-status-error");
  status.textContent = "";
}

function setRowDeleteDialogBusy(state, isBusy) {
  state.isBusy = isBusy;
  state.confirmButton.disabled = isBusy;
  state.cancelButton.disabled = isBusy;
  state.confirmButton.textContent = isBusy ? "Deleting..." : "Delete row";
}

function clearRowDeleteDialogError(state) {
  state.error.hidden = true;
  state.error.textContent = "";
}

function showRowDeleteDialogError(state, message) {
  state.error.hidden = false;
  state.error.textContent = message;
}

function rowMutationRequestError(response, data) {
  if (data && data.errors) {
    return new Error(data.errors.join(" "));
  }
  if (data && data.error) {
    return new Error(data.error);
  }
  if (data && data.title) {
    return new Error(data.title);
  }
  return new Error("Request failed with HTTP " + response.status);
}

function tildeDecode(value) {
  if (!value) {
    return "";
  }
  var placeholder = "__datasette_percent_placeholder__";
  try {
    return decodeURIComponent(
      value
        .replace(/%/g, placeholder)
        .replace(/~/g, "%")
        .replace(/\+/g, " "),
    ).replace(new RegExp(placeholder, "g"), "%");
  } catch (_error) {
    return value;
  }
}

function tildeEncode(value) {
  var bytes = new TextEncoder().encode(String(value));
  var encoded = "";
  bytes.forEach(function (byte) {
    var isSafe =
      (byte >= 65 && byte <= 90) ||
      (byte >= 97 && byte <= 122) ||
      (byte >= 48 && byte <= 57) ||
      byte === 95 ||
      byte === 45;
    if (isSafe) {
      encoded += String.fromCharCode(byte);
    } else if (byte === 32) {
      encoded += "+";
    } else {
      encoded += "~" + byte.toString(16).toUpperCase().padStart(2, "0");
    }
  });
  return encoded;
}

function rowDisplayLabel(row) {
  return tildeDecode(row.getAttribute("data-row") || "");
}

function tableBaseUrl() {
  var tableUrl =
    window._datasetteTableData && window._datasetteTableData.tableUrl;
  var url = new URL(tableUrl || location.href, location.href);
  url.hash = "";
  url.search = "";
  return url;
}

function tableInsertData() {
  return window._datasetteTableData && window._datasetteTableData.insertRow;
}

function tableInsertUrl() {
  var data = tableInsertData();
  if (data && data.path) {
    return new URL(data.path, location.href).toString();
  }
  var url = tableBaseUrl();
  url.pathname = url.pathname.replace(/\/$/, "") + "/-/insert";
  return url.toString();
}

function rowResourceUrl(row) {
  var rowId = row.getAttribute("data-row");
  if (!rowId) {
    return null;
  }
  var url = tableBaseUrl();
  url.pathname = url.pathname.replace(/\/$/, "") + "/" + rowId;
  return url;
}

function rowJsonUrl(row) {
  var url = rowResourceUrl(row);
  if (!url) {
    return "";
  }
  url.pathname = url.pathname + ".json";
  url.searchParams.set("_extra", "columns,column_types");
  return url.toString();
}

function rowDeleteUrl(row) {
  var url = rowResourceUrl(row);
  if (!url) {
    return "";
  }
  url.pathname = url.pathname.replace(/\/$/, "") + "/-/delete";
  return url.toString();
}

function rowUpdateUrl(row) {
  var url = rowResourceUrl(row);
  if (!url) {
    return "";
  }
  url.pathname = url.pathname.replace(/\/$/, "") + "/-/update";
  return url.toString();
}

function rowFragmentUrl(row) {
  var rowId = row.getAttribute("data-row");
  return rowFragmentUrlById(rowId);
}

function rowFragmentUrlById(rowId) {
  if (!rowId) {
    return "";
  }
  var url = tableBaseUrl();
  url.search = new URL(location.href).search;
  url.pathname = url.pathname.replace(/\/$/, "") + "/-/fragment";
  url.searchParams.delete("_next");
  url.searchParams.set("_row", rowId);
  url.searchParams.set("_nocount", "1");
  url.searchParams.set("_nofacet", "1");
  url.searchParams.set("_nosuggest", "1");
  return url.toString();
}

function nextRowActionFocusTarget(row, action) {
  var selector = 'button[data-row-action="' + action + '"]:not([disabled])';
  var sibling = row.nextElementSibling;
  while (sibling) {
    var nextButton = sibling.querySelector(selector);
    if (nextButton) {
      return nextButton;
    }
    sibling = sibling.nextElementSibling;
  }

  sibling = row.previousElementSibling;
  while (sibling) {
    var previousButton = sibling.querySelector(selector);
    if (previousButton) {
      return previousButton;
    }
    sibling = sibling.previousElementSibling;
  }

  return null;
}

function nextRowDeleteFocusTarget(row, manager) {
  return nextRowActionFocusTarget(row, "delete") || ensureRowMutationStatus(manager);
}

function ensureRowDeleteDialog(manager) {
  if (rowDeleteDialogState) {
    return rowDeleteDialogState;
  }
  if (!window.HTMLDialogElement) {
    return null;
  }

  var dialog = document.createElement("dialog");
  dialog.id = ROW_DELETE_DIALOG_ID;
  dialog.className = "row-delete-dialog";
  dialog.setAttribute("aria-labelledby", "row-delete-title");
  dialog.setAttribute("aria-describedby", "row-delete-message");
  dialog.innerHTML = `
    <div class="modal-header">
      <span class="modal-title" id="row-delete-title">Delete row</span>
    </div>
    <p class="row-delete-message" id="row-delete-message">Delete row <span class="row-delete-id"></span>?</p>
    <p class="row-delete-error" role="alert" hidden></p>
    <div class="modal-footer">
      <button type="button" class="btn btn-ghost row-delete-cancel">Cancel</button>
      <button type="button" class="btn btn-primary row-delete-confirm">Delete row</button>
    </div>
  `;
  document.body.appendChild(dialog);

  rowDeleteDialogState = {
    dialog: dialog,
    message: dialog.querySelector(".row-delete-message"),
    rowId: dialog.querySelector(".row-delete-id"),
    error: dialog.querySelector(".row-delete-error"),
    cancelButton: dialog.querySelector(".row-delete-cancel"),
    confirmButton: dialog.querySelector(".row-delete-confirm"),
    currentRow: null,
    currentDeleteUrl: null,
    currentPkPath: null,
    manager: manager,
    isBusy: false,
    shouldRestoreFocus: true,
  };

  rowDeleteDialogState.cancelButton.addEventListener("click", function () {
    if (!rowDeleteDialogState.isBusy) {
      rowDeleteDialogState.shouldRestoreFocus = true;
      dialog.close();
    }
  });

  dialog.addEventListener("click", function (ev) {
    if (ev.target === dialog && !rowDeleteDialogState.isBusy) {
      rowDeleteDialogState.shouldRestoreFocus = true;
      dialog.close();
    }
  });

  dialog.addEventListener("keydown", function (ev) {
    if (
      ev.key === "Enter" &&
      document.activeElement === rowDeleteDialogState.confirmButton
    ) {
      ev.preventDefault();
      if (!rowDeleteDialogState.isBusy) {
        rowDeleteDialogState.confirmButton.click();
      }
      return;
    }
    if (ev.key !== "Escape") {
      return;
    }
    if (rowDeleteDialogState.isBusy) {
      ev.preventDefault();
      return;
    }
    ev.preventDefault();
    rowDeleteDialogState.shouldRestoreFocus = true;
    dialog.close();
  });

  dialog.addEventListener("cancel", function (ev) {
    if (rowDeleteDialogState.isBusy) {
      ev.preventDefault();
    } else {
      rowDeleteDialogState.shouldRestoreFocus = true;
    }
  });

  dialog.addEventListener("close", function () {
    var state = rowDeleteDialogState;
    clearRowDeleteDialogError(state);
    setRowDeleteDialogBusy(state, false);
    if (
      state.shouldRestoreFocus &&
      state.currentButton &&
      document.contains(state.currentButton)
    ) {
      state.currentButton.focus();
    }
  });

  rowDeleteDialogState.confirmButton.addEventListener("click", async function () {
    var state = rowDeleteDialogState;
    clearRowDeleteDialogError(state);
    setRowDeleteDialogBusy(state, true);

    try {
      var response = await fetch(state.currentDeleteUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
        },
      });
      var data = null;
      try {
        data = await response.json();
      } catch (_error) {
        data = null;
      }
      if (!response.ok || (data && data.ok === false)) {
        throw rowMutationRequestError(response, data);
      }

      var focusTarget = nextRowDeleteFocusTarget(state.currentRow, state.manager);
      var statusMessage = state.currentPkPath
        ? "Deleted row " + state.currentPkPath + "."
        : "Deleted row.";
      state.shouldRestoreFocus = false;
      state.dialog.close();
      state.currentRow.remove();
      showRowMutationStatus(state.manager, statusMessage, false);
      if (focusTarget && document.contains(focusTarget)) {
        focusTarget.focus();
      } else {
        ensureRowMutationStatus(state.manager).focus();
      }
    } catch (error) {
      setRowDeleteDialogBusy(state, false);
      showRowDeleteDialogError(state, error.message || "Delete failed");
    }
  });

  return rowDeleteDialogState;
}

function openRowDeleteDialog(button, manager) {
  var row = button.closest("[data-row]");
  if (!row || !row.getAttribute("data-row")) {
    return;
  }
  var state = ensureRowDeleteDialog(manager);
  if (!state) {
    return;
  }

  state.manager = manager;
  state.currentButton = button;
  state.currentRow = row;
  state.currentDeleteUrl = rowDeleteUrl(row);
  state.currentPkPath = rowDisplayLabel(row);
  state.shouldRestoreFocus = true;

  clearRowDeleteDialogError(state);
  setRowDeleteDialogBusy(state, false);
  state.rowId.textContent = state.currentPkPath || "this row";

  if (!state.dialog.open) {
    state.dialog.showModal();
  }
  state.confirmButton.focus();
}

function initRowDeleteActions(manager) {
  if (!window.fetch || !window.HTMLDialogElement) {
    return;
  }
  document.addEventListener("click", function (ev) {
    var button = ev.target.closest('button[data-row-action="delete"]');
    if (!button) {
      return;
    }
    ev.preventDefault();
    openRowDeleteDialog(button, manager);
  });
}

function valueToEditText(value) {
  if (value === null || typeof value === "undefined") {
    return "";
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function shouldUseTextarea(value) {
  if (value && typeof value === "object") {
    return true;
  }
  var text = valueToEditText(value);
  return text.length > 80 || text.indexOf("\n") !== -1;
}

function rowEditValueType(value) {
  if (value === null || typeof value === "undefined") {
    return "null";
  }
  if (typeof value === "number") {
    return "number";
  }
  if (typeof value === "boolean") {
    return "boolean";
  }
  if (typeof value === "object") {
    return "json";
  }
  return "string";
}

function createRowEditField(column, value, isPk, columnType, index, options) {
  options = options || {};
  var field = document.createElement("div");
  field.className = "row-edit-field";
  var hasDefault =
    options.hasDefault ||
    (options.defaultValue !== null && typeof options.defaultValue !== "undefined");
  var useDefaultInitially = hasDefault && options.useDefaultInitially;

  var fieldId = "row-edit-field-" + index;
  var metaId = "row-edit-field-meta-" + index;
  var label = document.createElement("label");
  label.className = "row-edit-label";
  label.setAttribute("for", fieldId);
  label.textContent = column;

  var controlWrap = document.createElement("div");
  controlWrap.className = "row-edit-control-wrap";

  var control = shouldUseTextarea(value)
    ? document.createElement("textarea")
    : document.createElement("input");
  control.className = "row-edit-input";
  control.id = fieldId;
  control.name = column;
  control.value = valueToEditText(value);
  control.setAttribute("aria-describedby", metaId);
  control.dataset.originalValue = valueToEditText(value);
  control.dataset.originalValueType =
    options.valueType || rowEditValueType(value);
  control.dataset.primaryKey = isPk ? "1" : "0";
  if (useDefaultInitially) {
    control.dataset.useDefault = "1";
    control.disabled = true;
  }
  if (options.omitIfBlank) {
    control.dataset.omitIfBlank = "1";
  }

  if (control.nodeName === "TEXTAREA") {
    control.rows = Math.min(8, Math.max(3, control.value.split("\n").length));
  } else {
    control.type = "text";
  }

  if (isPk && options.primaryKeyReadonly !== false) {
    control.readOnly = true;
  }

  var meta = document.createElement("span");
  meta.id = metaId;
  meta.className = "row-edit-field-meta";
  var metaParts = [];
  if (isPk) {
    metaParts.push("Primary key");
  }
  if (options.notnull) {
    metaParts.push("Required");
  }
  if (hasDefault && !useDefaultInitially) {
    metaParts.push("Default: " + options.defaultValue);
  }
  if (value === null) {
    metaParts.push("Current value: NULL");
    control.placeholder = "NULL";
  }
  if (columnType && columnType.type) {
    metaParts.push("Custom type: " + columnType.type);
  }
  meta.textContent = metaParts.join(" · ");

  if (useDefaultInitially) {
    var defaultBlock = document.createElement("div");
    defaultBlock.className = "row-edit-default";
    defaultBlock.setAttribute("aria-describedby", metaId);

    var defaultText = document.createElement("span");
    defaultText.className = "row-edit-default-text";
    defaultText.appendChild(document.createTextNode("default "));
    var defaultCode = document.createElement("code");
    defaultCode.className = "row-edit-default-code";
    defaultCode.textContent = options.defaultValue;
    defaultText.appendChild(defaultCode);

    var setValueButton = document.createElement("button");
    setValueButton.type = "button";
    setValueButton.className =
      "row-edit-default-button row-edit-default-set-value";
    setValueButton.textContent = "Set value";
    setValueButton.setAttribute("aria-label", "Set value for " + column);

    var customWrap = document.createElement("div");
    customWrap.className = "row-edit-custom-value";
    customWrap.hidden = true;

    var useDefaultButton = document.createElement("button");
    useDefaultButton.type = "button";
    useDefaultButton.className = "row-edit-default-button";
    useDefaultButton.textContent = "Use default";
    useDefaultButton.setAttribute("aria-label", "Use default for " + column);

    setValueButton.addEventListener("click", function () {
      control.dataset.useDefault = "0";
      control.disabled = false;
      defaultBlock.hidden = true;
      customWrap.hidden = false;
      control.focus();
    });

    useDefaultButton.addEventListener("click", function () {
      control.dataset.useDefault = "1";
      control.disabled = true;
      control.value = "";
      customWrap.hidden = true;
      defaultBlock.hidden = false;
      setValueButton.focus();
    });

    defaultBlock.appendChild(defaultText);
    defaultBlock.appendChild(setValueButton);
    customWrap.appendChild(control);
    customWrap.appendChild(useDefaultButton);
    controlWrap.appendChild(defaultBlock);
    controlWrap.appendChild(customWrap);
  } else {
    controlWrap.appendChild(control);
  }
  if (meta.textContent) {
    controlWrap.appendChild(meta);
  }
  field.appendChild(label);
  field.appendChild(controlWrap);
  return field;
}

function clearRowEditDialogError(state) {
  state.error.hidden = true;
  state.error.textContent = "";
}

function showRowEditDialogError(state, message) {
  state.error.hidden = false;
  state.error.textContent = message;
  state.error.focus();
}

function updateRowEditDialogButtons(state) {
  state.saveButton.disabled = state.isLoading || state.isSaving || !state.hasLoaded;
  state.cancelButton.disabled = state.isSaving;
  var saveLabel = state.mode === "insert" ? "Insert row" : "Save";
  state.saveButton.textContent = state.isSaving ? "Saving..." : saveLabel;
  state.form.setAttribute(
    "aria-busy",
    state.isLoading || state.isSaving ? "true" : "false",
  );
}

function setRowEditDialogLoading(state, isLoading) {
  state.isLoading = isLoading;
  state.loading.hidden = !isLoading;
  updateRowEditDialogButtons(state);
}

function setRowEditDialogSaving(state, isSaving) {
  state.isSaving = isSaving;
  updateRowEditDialogButtons(state);
}

function valueFromRowEditControl(control) {
  var value = control.value;
  return valueFromRowEditText(
    control.name,
    value,
    control.dataset.originalValueType || "string",
  );
}

function valueFromRowEditText(name, value, originalValueType) {
  var trimmed = value.trim();

  if (originalValueType === "null" && value === "") {
    return null;
  }
  if (originalValueType === "number") {
    if (trimmed === "") {
      return null;
    }
    var numberValue = Number(trimmed);
    if (Number.isNaN(numberValue)) {
      throw new Error(name + " must be a number");
    }
    return numberValue;
  }
  if (originalValueType === "boolean") {
    if (/^(true|1|yes)$/i.test(trimmed)) {
      return true;
    }
    if (/^(false|0|no)$/i.test(trimmed)) {
      return false;
    }
    throw new Error(name + " must be true or false");
  }
  if (originalValueType === "json") {
    if (trimmed === "") {
      return null;
    }
    try {
      return JSON.parse(value);
    } catch (_error) {
      throw new Error(name + " must be valid JSON");
    }
  }
  return value;
}

function originalValueFromRowEditControl(control) {
  return valueFromRowEditText(
    control.name,
    control.dataset.originalValue || "",
    control.dataset.originalValueType || "string",
  );
}

function rowEditValuesMatch(left, right) {
  if (left === right) {
    return true;
  }
  if (
    left &&
    right &&
    typeof left === "object" &&
    typeof right === "object"
  ) {
    return JSON.stringify(left) === JSON.stringify(right);
  }
  return false;
}

function collectRowFormValues(state) {
  var values = {};
  state.fields.querySelectorAll(".row-edit-input").forEach(function (control) {
    if (
      state.mode === "edit" &&
      (control.readOnly || control.dataset.primaryKey === "1")
    ) {
      return;
    }
    if (control.dataset.useDefault === "1") {
      return;
    }
    if (control.dataset.omitIfBlank === "1" && control.value === "") {
      return;
    }
    var value = valueFromRowEditControl(control);
    if (
      state.mode === "edit" &&
      rowEditValuesMatch(value, originalValueFromRowEditControl(control))
    ) {
      return;
    }
    values[control.name] = value;
  });
  return values;
}

function findDataRowElement(root, rowId) {
  var elements = root.querySelectorAll("[data-row]");
  for (var i = 0; i < elements.length; i += 1) {
    if (elements[i].getAttribute("data-row") === rowId) {
      return elements[i];
    }
  }
  return null;
}

async function fetchUpdatedRowElement(state) {
  if (!state.currentFragmentUrl || !state.currentRowId) {
    return null;
  }
  var response = await fetch(state.currentFragmentUrl, {
    headers: {
      Accept: "text/html",
    },
  });
  var html = await response.text();
  if (!response.ok) {
    throw new Error("Could not refresh row: HTTP " + response.status);
  }
  var doc = new DOMParser().parseFromString(html, "text/html");
  return findDataRowElement(doc, state.currentRowId);
}

function rowPathFromRowData(row, primaryKeys) {
  if (!row) {
    return null;
  }
  var keys = primaryKeys && primaryKeys.length ? primaryKeys : ["rowid"];
  var bits = [];
  for (var i = 0; i < keys.length; i += 1) {
    var key = keys[i];
    if (typeof row[key] === "undefined") {
      return null;
    }
    bits.push(tildeEncode(row[key]));
  }
  return bits.join(",");
}

function addInsertedRowToPage(rowElement) {
  var importedRow = document.importNode(rowElement, true);
  var firstRow = document.querySelector("[data-row]");
  if (firstRow && firstRow.parentNode) {
    firstRow.parentNode.insertBefore(importedRow, firstRow);
  } else {
    var tbody = document.querySelector("table.rows-and-columns tbody");
    if (!tbody) {
      return null;
    }
    tbody.appendChild(importedRow);
  }
  var zeroResults = document.querySelector(".zero-results");
  if (zeroResults) {
    zeroResults.remove();
  }
  return importedRow;
}

async function saveRowEditDialog(state) {
  if (state.isLoading || state.isSaving || !state.hasLoaded) {
    return;
  }
  clearRowEditDialogError(state);
  setRowEditDialogSaving(state, true);

  try {
    var url = state.mode === "insert" ? state.currentInsertUrl : state.currentUpdateUrl;
    if (!url) {
      throw new Error(
        state.mode === "insert"
          ? "Could not find the row insert URL"
          : "Could not find the row update URL",
      );
    }
    var formValues = collectRowFormValues(state);
    if (state.mode === "edit" && !Object.keys(formValues).length) {
      state.shouldRestoreFocus = true;
      hideRowMutationStatus();
      state.dialog.close();
      return;
    }
    var payload =
      state.mode === "insert"
        ? { row: formValues, return: true }
        : { update: formValues, return: true };
    var response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    var data = null;
    try {
      data = await response.json();
    } catch (_error) {
      data = null;
    }
    if (!response.ok || (data && data.ok === false)) {
      throw rowMutationRequestError(response, data);
    }

    if (state.mode === "insert") {
      var insertData = tableInsertData() || {};
      var insertedRowData = data && data.rows && data.rows.length ? data.rows[0] : null;
      var insertedRowId = rowPathFromRowData(
        insertedRowData,
        insertData.primaryKeys || [],
      );
      state.shouldRestoreFocus = false;
      if (!insertedRowId) {
        state.dialog.close();
        var missingIdStatus = showRowMutationStatus(
          state.manager,
          "Inserted row. Refresh the page to see it.",
          false,
        );
        missingIdStatus.focus();
        return;
      }

      state.currentRowId = insertedRowId;
      state.currentFragmentUrl = rowFragmentUrlById(insertedRowId);
      var insertedStatusMessage =
        "Inserted row " + tildeDecode(insertedRowId) + ".";
      var insertedRow = null;
      try {
        insertedRow = await fetchUpdatedRowElement(state);
      } catch (_error) {
        state.dialog.close();
        var refreshFailedStatus = showRowMutationStatus(
          state.manager,
          "Inserted row, but could not refresh the table row. Refresh the page to see it.",
          true,
        );
        refreshFailedStatus.focus();
        return;
      }
      if (insertedRow) {
        var addedRow = addInsertedRowToPage(insertedRow);
        state.dialog.close();
        showRowMutationStatus(state.manager, insertedStatusMessage, false);
        if (addedRow) {
          var insertedFocusTarget =
            addedRow.querySelector('button[data-row-action="edit"]') || addedRow;
          insertedFocusTarget.focus();
        }
      } else {
        state.dialog.close();
        var filteredStatus = showRowMutationStatus(
          state.manager,
          "Inserted row. It does not match the current filters.",
          false,
        );
        filteredStatus.focus();
      }
      return;
    }

    var updatedRow = await fetchUpdatedRowElement(state);
    var focusTarget = null;
    if (updatedRow && state.currentRow && document.contains(state.currentRow)) {
      var importedRow = document.importNode(updatedRow, true);
      state.currentRow.replaceWith(importedRow);
      showRowMutationStatus(
        state.manager,
        state.currentPkPath
          ? "Updated row " + state.currentPkPath + "."
          : "Updated row.",
        false,
      );
      focusTarget =
        importedRow.querySelector('button[data-row-action="edit"]') || importedRow;
    } else if (state.currentRow && document.contains(state.currentRow)) {
      focusTarget =
        nextRowActionFocusTarget(state.currentRow, "edit") ||
        ensureRowMutationStatus(state.manager);
      state.currentRow.remove();
      showRowMutationStatus(
        state.manager,
        state.currentPkPath
          ? "Updated row " +
              state.currentPkPath +
              ". It no longer matches the current filters."
          : "Updated row. It no longer matches the current filters.",
        false,
      );
    }

    state.shouldRestoreFocus = false;
    state.dialog.close();
    if (focusTarget && document.contains(focusTarget)) {
      focusTarget.focus();
    }
  } catch (error) {
    setRowEditDialogSaving(state, false);
    showRowEditDialogError(state, error.message || "Could not save row");
  }
}

function renderRowEditFields(state, data) {
  var row = data.rows && data.rows.length ? data.rows[0] : null;
  var columns = data.columns || (row ? Object.keys(row) : []);
  var primaryKeys = data.primary_keys || [];
  var columnTypes = data.column_types || {};

  state.fields.innerHTML = "";
  columns.forEach(function (column, index) {
    state.fields.appendChild(
      createRowEditField(
        column,
        row ? row[column] : null,
        primaryKeys.indexOf(column) !== -1,
        columnTypes[column],
        index,
        {
          primaryKeyReadonly: true,
        },
      ),
    );
  });

  state.hasLoaded = true;
  updateRowEditDialogButtons(state);
  var firstEditable = state.fields.querySelector(".row-edit-input:not([readonly])");
  var firstField = state.fields.querySelector(".row-edit-input");
  (firstEditable || firstField || state.cancelButton).focus();
}

function renderRowInsertFields(state, data) {
  var columns = data.columns || [];

  state.fields.innerHTML = "";
  columns.forEach(function (column, index) {
    state.fields.appendChild(
      createRowEditField(
        column.name,
        "",
        !!column.is_pk,
        column.column_type,
        index,
        {
          defaultValue: column.default,
          hasDefault: column.has_default,
          notnull: column.notnull,
          primaryKeyReadonly: false,
          useDefaultInitially: column.has_default,
          valueType: column.value_type,
        },
      ),
    );
  });

  if (!columns.length) {
    var emptyMessage = document.createElement("p");
    emptyMessage.className = "row-edit-empty";
    emptyMessage.textContent = "This row will use the table defaults.";
    state.fields.appendChild(emptyMessage);
  }

  state.hasLoaded = true;
  updateRowEditDialogButtons(state);
  var firstControl = state.fields.querySelector(
    ".row-edit-default-set-value, .row-edit-input:not(:disabled)",
  );
  (firstControl || state.saveButton).focus();
}

function setRowEditDialogTitle(state, text, codeText) {
  state.title.textContent = "";
  state.title.appendChild(document.createTextNode(text));
  if (!codeText) {
    return;
  }
  state.title.appendChild(document.createTextNode(" "));
  var code = document.createElement("code");
  code.textContent = codeText;
  state.title.appendChild(code);
}

function ensureRowEditDialog(manager) {
  if (rowEditDialogState) {
    return rowEditDialogState;
  }
  if (!window.HTMLDialogElement) {
    return null;
  }

  var dialog = document.createElement("dialog");
  dialog.id = ROW_EDIT_DIALOG_ID;
  dialog.className = "row-edit-dialog";
  dialog.setAttribute("aria-labelledby", "row-edit-title");
  dialog.innerHTML = `
    <div class="modal-header">
      <span class="modal-title" id="row-edit-title">Edit row</span>
    </div>
    <form class="row-edit-form" method="post">
      <p class="row-edit-summary" id="row-edit-summary" hidden></p>
      <p class="row-edit-loading" role="status" aria-live="polite">Loading row...</p>
      <p class="row-edit-error" role="alert" tabindex="-1" hidden></p>
      <div class="row-edit-fields"></div>
      <div class="modal-footer">
        <button type="button" class="btn btn-ghost row-edit-cancel">Cancel</button>
        <button type="submit" class="btn btn-primary row-edit-save" disabled>Save</button>
      </div>
    </form>
  `;
  document.body.appendChild(dialog);

  rowEditDialogState = {
    dialog: dialog,
    form: dialog.querySelector(".row-edit-form"),
    title: dialog.querySelector(".modal-title"),
    summary: dialog.querySelector(".row-edit-summary"),
    loading: dialog.querySelector(".row-edit-loading"),
    error: dialog.querySelector(".row-edit-error"),
    fields: dialog.querySelector(".row-edit-fields"),
    cancelButton: dialog.querySelector(".row-edit-cancel"),
    saveButton: dialog.querySelector(".row-edit-save"),
    currentButton: null,
    currentRow: null,
    currentRowId: null,
    currentPkPath: null,
    currentInsertUrl: null,
    currentUpdateUrl: null,
    currentFragmentUrl: null,
    mode: "edit",
    loadId: 0,
    manager: manager,
    isLoading: false,
    isSaving: false,
    hasLoaded: false,
    shouldRestoreFocus: true,
  };

  rowEditDialogState.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    saveRowEditDialog(rowEditDialogState);
  });

  rowEditDialogState.cancelButton.addEventListener("click", function () {
    if (!rowEditDialogState.isSaving) {
      rowEditDialogState.shouldRestoreFocus = true;
      dialog.close();
    }
  });

  dialog.addEventListener("click", function (ev) {
    if (ev.target === dialog && !rowEditDialogState.isSaving) {
      rowEditDialogState.shouldRestoreFocus = true;
      dialog.close();
    }
  });

  dialog.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") {
      return;
    }
    if (rowEditDialogState.isSaving) {
      ev.preventDefault();
      return;
    }
    ev.preventDefault();
    rowEditDialogState.shouldRestoreFocus = true;
    dialog.close();
  });

  dialog.addEventListener("cancel", function (ev) {
    if (rowEditDialogState.isSaving) {
      ev.preventDefault();
    } else {
      rowEditDialogState.shouldRestoreFocus = true;
    }
  });

  dialog.addEventListener("close", function () {
    var state = rowEditDialogState;
    state.loadId += 1;
    clearRowEditDialogError(state);
    state.hasLoaded = false;
    setRowEditDialogLoading(state, false);
    setRowEditDialogSaving(state, false);
    if (
      state.shouldRestoreFocus &&
      state.currentButton &&
      document.contains(state.currentButton)
    ) {
      state.currentButton.focus();
    }
  });

  return rowEditDialogState;
}

async function openRowEditDialog(button, manager) {
  var row = button.closest("[data-row]");
  if (!row || !row.getAttribute("data-row")) {
    return;
  }
  var state = ensureRowEditDialog(manager);
  if (!state) {
    return;
  }

  state.manager = manager;
  state.mode = "edit";
  state.currentButton = button;
  state.currentRow = row;
  state.currentRowId = row.getAttribute("data-row") || "";
  state.currentPkPath = rowDisplayLabel(row);
  state.currentInsertUrl = null;
  state.currentUpdateUrl = rowUpdateUrl(row);
  state.currentFragmentUrl = rowFragmentUrl(row);
  if (state.currentUpdateUrl) {
    state.form.action = new URL(state.currentUpdateUrl, location.href).toString();
  } else {
    state.form.removeAttribute("action");
  }
  state.shouldRestoreFocus = true;
  state.hasLoaded = false;
  state.loadId += 1;
  var loadId = state.loadId;

  clearRowEditDialogError(state);
  setRowEditDialogLoading(state, true);
  state.fields.innerHTML = "";
  state.dialog.removeAttribute("aria-describedby");
  setRowEditDialogTitle(state, "Edit row", state.currentPkPath || "this row");
  state.summary.hidden = true;
  state.summary.textContent = "";

  if (!state.dialog.open) {
    state.dialog.showModal();
  }
  state.cancelButton.focus();

  try {
    var response = await fetch(rowJsonUrl(row), {
      headers: {
        Accept: "application/json",
      },
    });
    var data = await response.json();
    if (loadId !== state.loadId) {
      return;
    }
    if (!response.ok || data.ok === false) {
      throw rowMutationRequestError(response, data);
    }
    setRowEditDialogLoading(state, false);
    renderRowEditFields(state, data);
  } catch (error) {
    if (loadId !== state.loadId) {
      return;
    }
    setRowEditDialogLoading(state, false);
    showRowEditDialogError(state, error.message || "Could not load row");
    state.cancelButton.focus();
  }
}

function openRowInsertDialog(button, manager) {
  var insertData = tableInsertData();
  if (!insertData) {
    return;
  }
  var state = ensureRowEditDialog(manager);
  if (!state) {
    return;
  }

  state.manager = manager;
  state.mode = "insert";
  state.currentButton = button;
  state.currentRow = null;
  state.currentRowId = null;
  state.currentPkPath = null;
  state.currentInsertUrl = tableInsertUrl();
  state.currentUpdateUrl = null;
  state.currentFragmentUrl = null;
  state.shouldRestoreFocus = true;
  state.hasLoaded = false;
  state.loadId += 1;

  if (state.currentInsertUrl) {
    state.form.action = new URL(state.currentInsertUrl, location.href).toString();
  } else {
    state.form.removeAttribute("action");
  }

  clearRowEditDialogError(state);
  setRowEditDialogLoading(state, false);
  state.fields.innerHTML = "";
  state.dialog.removeAttribute("aria-describedby");
  setRowEditDialogTitle(
    state,
    insertData.tableName ? "Insert row into " + insertData.tableName : "Insert row",
  );
  state.summary.hidden = true;
  state.summary.textContent = "";

  if (!state.dialog.open) {
    state.dialog.showModal();
  }
  renderRowInsertFields(state, insertData);
}

function initRowEditActions(manager) {
  if (!window.fetch || !window.HTMLDialogElement) {
    return;
  }
  document.addEventListener("click", function (ev) {
    var button = ev.target.closest('button[data-row-action="edit"]');
    if (!button) {
      return;
    }
    ev.preventDefault();
    openRowEditDialog(button, manager);
  });
}

function initRowInsertActions(manager) {
  if (!window.fetch || !window.HTMLDialogElement || !tableInsertData()) {
    return;
  }
  document.addEventListener("click", function (ev) {
    var button = ev.target.closest('button[data-table-action="insert-row"]');
    if (!button) {
      return;
    }
    ev.preventDefault();
    openRowInsertDialog(button, manager);
  });
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

function hasMultipleVisibleColumns(manager) {
  return (
    Array.from(document.querySelectorAll(manager.selectors.tableHeaders)).filter(
      (th) => th.dataset.column && th.dataset.isLinkColumn !== "1",
    ).length > 1
  );
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

  if (canSetColumnType() && getSetColumnTypeConfig(column)) {
    columnActions.push({
      label: setColumnTypeActionLabel(column),
      href: "#",
      onClick:
        options.onSetColumnType ||
        function (ev) {
          ev.preventDefault();
          window.setTimeout(function () {
            openSetColumnTypeDialog(th);
          }, 0);
        },
    });
  }

  if (th.dataset.isPk !== "1" && hasMultipleVisibleColumns(manager)) {
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
      onSetColumnType: function (ev) {
        ev.preventDefault();
        closeMenu();
        window.setTimeout(function () {
          openSetColumnTypeDialog(th);
        }, 0);
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
  initRowInsertActions(manager);
  initRowEditActions(manager);
  initRowDeleteActions(manager);

  // Other UI functions with interactive JS needs
  addButtonsToFilterRows(manager);
  initAutocompleteForFilterValues(manager);
});
