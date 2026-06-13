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

function ensureRowDeleteStatus(manager) {
  var status = document.querySelector(".row-delete-status");
  if (status) {
    return status;
  }

  status = document.createElement("p");
  status.className = "row-delete-status";
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

function showRowDeleteStatus(manager, message, isError) {
  var status = ensureRowDeleteStatus(manager);
  status.hidden = false;
  status.classList.toggle("row-delete-status-error", !!isError);
  status.textContent = message;
  return status;
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

function rowDeleteRequestError(response, data) {
  if (data && data.errors) {
    return new Error(data.errors.join(" "));
  }
  if (data && data.error) {
    return new Error(data.error);
  }
  if (data && data.title) {
    return new Error(data.title);
  }
  return new Error("Delete failed with HTTP " + response.status);
}

function nextRowDeleteFocusTarget(row, manager) {
  var sibling = row.nextElementSibling;
  while (sibling) {
    var nextButton = sibling.querySelector(
      'button[data-row-action="delete"]:not([disabled])',
    );
    if (nextButton) {
      return nextButton;
    }
    sibling = sibling.nextElementSibling;
  }

  sibling = row.previousElementSibling;
  while (sibling) {
    var previousButton = sibling.querySelector(
      'button[data-row-action="delete"]:not([disabled])',
    );
    if (previousButton) {
      return previousButton;
    }
    sibling = sibling.previousElementSibling;
  }

  return ensureRowDeleteStatus(manager);
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
        throw rowDeleteRequestError(response, data);
      }

      var focusTarget = nextRowDeleteFocusTarget(state.currentRow, state.manager);
      var statusMessage = state.currentPkPath
        ? "Deleted row " + state.currentPkPath + "."
        : "Deleted row.";
      state.shouldRestoreFocus = false;
      state.dialog.close();
      state.currentRow.remove();
      showRowDeleteStatus(state.manager, statusMessage, false);
      if (focusTarget && document.contains(focusTarget)) {
        focusTarget.focus();
      } else {
        ensureRowDeleteStatus(state.manager).focus();
      }
    } catch (error) {
      setRowDeleteDialogBusy(state, false);
      showRowDeleteDialogError(state, error.message || "Delete failed");
    }
  });

  return rowDeleteDialogState;
}

function openRowDeleteDialog(button, manager) {
  var row = button.closest("tr[data-row-delete-url]");
  if (!row || !row.dataset.rowDeleteUrl) {
    return;
  }
  var state = ensureRowDeleteDialog(manager);
  if (!state) {
    return;
  }

  state.manager = manager;
  state.currentButton = button;
  state.currentRow = row;
  state.currentDeleteUrl = row.dataset.rowDeleteUrl;
  state.currentPkPath = row.dataset.rowPkPath || "";
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

function rowJsonUrl(row) {
  var url = new URL(row.dataset.rowUrl, location.href);
  url.pathname = url.pathname + ".json";
  url.searchParams.set("_extra", "columns,column_types");
  return url.toString();
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

function createRowEditField(column, value, isPk, columnType, index) {
  var field = document.createElement("div");
  field.className = "row-edit-field";

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

  if (control.nodeName === "TEXTAREA") {
    control.rows = Math.min(8, Math.max(3, control.value.split("\n").length));
  } else {
    control.type = "text";
  }

  if (isPk) {
    control.readOnly = true;
  }

  var meta = document.createElement("span");
  meta.id = metaId;
  meta.className = "row-edit-field-meta";
  var metaParts = [];
  if (isPk) {
    metaParts.push("Primary key");
  }
  if (value === null) {
    metaParts.push("Current value: NULL");
    control.placeholder = "NULL";
  }
  if (columnType && columnType.type) {
    metaParts.push("Custom type: " + columnType.type);
  }
  meta.textContent = metaParts.join(" · ");

  controlWrap.appendChild(control);
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
}

function setRowEditDialogLoading(state, isLoading) {
  state.isLoading = isLoading;
  state.loading.hidden = !isLoading;
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
      ),
    );
  });

  var firstEditable = state.fields.querySelector(".row-edit-input:not([readonly])");
  var firstField = state.fields.querySelector(".row-edit-input");
  (firstEditable || firstField || state.cancelButton).focus();
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
  dialog.setAttribute("aria-describedby", "row-edit-summary");
  dialog.innerHTML = `
    <div class="modal-header">
      <span class="modal-title" id="row-edit-title">Edit row</span>
    </div>
    <form class="row-edit-form">
      <p class="row-edit-summary" id="row-edit-summary">Editing row <span class="row-edit-id"></span></p>
      <p class="row-edit-loading">Loading row...</p>
      <p class="row-edit-error" role="alert" hidden></p>
      <div class="row-edit-fields"></div>
      <div class="modal-footer">
        <button type="button" class="btn btn-ghost row-edit-cancel">Cancel</button>
        <button type="button" class="btn btn-primary row-edit-save" disabled>Save</button>
      </div>
    </form>
  `;
  document.body.appendChild(dialog);

  rowEditDialogState = {
    dialog: dialog,
    form: dialog.querySelector(".row-edit-form"),
    rowId: dialog.querySelector(".row-edit-id"),
    loading: dialog.querySelector(".row-edit-loading"),
    error: dialog.querySelector(".row-edit-error"),
    fields: dialog.querySelector(".row-edit-fields"),
    cancelButton: dialog.querySelector(".row-edit-cancel"),
    saveButton: dialog.querySelector(".row-edit-save"),
    currentButton: null,
    currentRow: null,
    currentPkPath: null,
    loadId: 0,
    manager: manager,
    isLoading: false,
    shouldRestoreFocus: true,
  };

  rowEditDialogState.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
  });

  rowEditDialogState.cancelButton.addEventListener("click", function () {
    rowEditDialogState.shouldRestoreFocus = true;
    dialog.close();
  });

  dialog.addEventListener("click", function (ev) {
    if (ev.target === dialog) {
      rowEditDialogState.shouldRestoreFocus = true;
      dialog.close();
    }
  });

  dialog.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") {
      return;
    }
    ev.preventDefault();
    rowEditDialogState.shouldRestoreFocus = true;
    dialog.close();
  });

  dialog.addEventListener("cancel", function () {
    rowEditDialogState.shouldRestoreFocus = true;
  });

  dialog.addEventListener("close", function () {
    var state = rowEditDialogState;
    state.loadId += 1;
    clearRowEditDialogError(state);
    setRowEditDialogLoading(state, false);
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
  var row = button.closest("tr[data-row-url]");
  if (!row || !row.dataset.rowUrl) {
    return;
  }
  var state = ensureRowEditDialog(manager);
  if (!state) {
    return;
  }

  state.manager = manager;
  state.currentButton = button;
  state.currentRow = row;
  state.currentPkPath = row.dataset.rowPkPath || "";
  state.shouldRestoreFocus = true;
  state.loadId += 1;
  var loadId = state.loadId;

  clearRowEditDialogError(state);
  setRowEditDialogLoading(state, true);
  state.fields.innerHTML = "";
  state.rowId.textContent = state.currentPkPath || "this row";

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
      throw rowDeleteRequestError(response, data);
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
  initRowEditActions(manager);
  initRowDeleteActions(manager);

  // Other UI functions with interactive JS needs
  addButtonsToFilterRows(manager);
  initAutocompleteForFilterValues(manager);
});
