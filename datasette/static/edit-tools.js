var ROW_DELETE_DIALOG_ID = "row-delete-dialog";
var rowDeleteDialogState = null;
var ROW_EDIT_DIALOG_ID = "row-edit-dialog";
var rowEditDialogState = null;

function datasetteManagerPath(manager, path) {
  if (manager && manager.path) {
    return manager.path(path);
  }
  var baseUrl = window.datasetteBaseUrl || "/";
  return baseUrl.replace(/\/?$/, "/") + String(path || "").replace(/^\/+/, "");
}

function ensureRowMutationStatus(manager) {
  var status = document.querySelector(".row-mutation-status");
  var tableWrapper = document.querySelector(manager.selectors.tableWrapper);
  var content = document.querySelector("section.content");
  var fallbackParent = content && content.parentNode;

  if (!status) {
    status = document.createElement("p");
    status.className = "row-mutation-status";
    status.hidden = true;
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.setAttribute("tabindex", "-1");
  }

  if (tableWrapper && tableWrapper.parentNode) {
    tableWrapper.parentNode.insertBefore(status, tableWrapper);
  } else if (content && fallbackParent) {
    fallbackParent.insertBefore(status, content);
  } else if (!status.parentNode) {
    document.body.appendChild(status);
  } else {
    document.body.insertBefore(status, document.body.firstChild);
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
      value.replace(/%/g, placeholder).replace(/~/g, "%").replace(/\+/g, " "),
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

function rowTitleLabel(row) {
  return row.getAttribute("data-row-label") || "";
}

function insertedRowStatusMessage(rowId, rowLabel) {
  var message = "Inserted row " + rowId;
  if (rowLabel && rowLabel !== rowId) {
    message += " (" + rowLabel + ")";
  }
  return message + ".";
}

function tableBaseUrl(state) {
  var tableUrl =
    (state && state.currentTableUrl) ||
    (window._datasetteTableData && window._datasetteTableData.tableUrl);
  var url = new URL(tableUrl || location.href, location.href);
  url.hash = "";
  url.search = "";
  return url;
}

function tablePageData() {
  return window._datasetteTableData || {};
}

function tableInsertData() {
  return tablePageData().insertRow;
}

function tableForeignKeys() {
  return tablePageData().foreignKeys || {};
}

function isRowPage() {
  return document.body && document.body.classList.contains("row");
}

function rowElementForActionButton(button) {
  return (
    button.closest("[data-row]") ||
    (button.getAttribute("data-row") ? button : null)
  );
}

function foreignKeyAutocompleteUrl(column, state) {
  if (state && state.currentForeignKeys && state.currentForeignKeys[column]) {
    return state.currentForeignKeys[column];
  }
  return tableForeignKeys()[column] || null;
}

function autocompleteRowPk(row) {
  var pks = (row && row.pks) || {};
  var keys = Object.keys(pks);
  if (keys.length !== 1) {
    return null;
  }
  return pks[keys[0]];
}

function foreignKeyRowUrl(autocompleteUrl, pk) {
  var url = new URL(autocompleteUrl, location.href);
  if (!/\/-\/autocomplete\/?$/.test(url.pathname)) {
    return null;
  }
  url.pathname =
    url.pathname.replace(/\/-\/autocomplete\/?$/, "") + "/" + tildeEncode(pk);
  url.search = "";
  url.hash = "";
  return url.toString();
}

function foreignKeyLabelText(row) {
  var pk = autocompleteRowPk(row);
  var label = row && row.label;
  if (
    label !== null &&
    typeof label !== "undefined" &&
    String(label) !== String(pk)
  ) {
    return String(label);
  }
  return "View row";
}

function rowEditMetaTextWithoutCurrentValue(meta) {
  return (meta.dataset.baseMeta || "")
    .split(" · ")
    .filter(function (part) {
      return part !== "Current value: NULL";
    })
    .join(" · ");
}

function updateRowEditForeignKeySeparator(meta) {
  var separator = meta.querySelector(".row-edit-fk-separator");
  if (!separator) {
    return;
  }
  var baseMeta = meta.querySelector(".row-edit-base-meta");
  var hasBaseMeta = !!(baseMeta && baseMeta.textContent);
  separator.textContent = hasBaseMeta ? " · " : "";
  separator.hidden = !hasBaseMeta;
}

function updateRowEditFieldMetaHidden(meta) {
  var baseMeta = meta.querySelector(".row-edit-base-meta");
  var hasBaseMeta = !!(baseMeta && baseMeta.textContent);
  var foreignKeyLinkWrap = meta.querySelector(".row-edit-fk-link-wrap");
  var hasForeignKeyLink = foreignKeyLinkWrap && !foreignKeyLinkWrap.hidden;
  meta.hidden =
    meta.dataset.reserveSpace !== "1" && !hasBaseMeta && !hasForeignKeyLink;
}

function setRowEditBaseMetaText(meta, text) {
  var baseMeta = meta.querySelector(".row-edit-base-meta");
  if (!baseMeta) {
    return;
  }
  baseMeta.textContent = text || "";
  updateRowEditForeignKeySeparator(meta);
  updateRowEditFieldMetaHidden(meta);
}

function setForeignKeyMetaLink(meta, autocompleteUrl, row) {
  var wrap = meta.querySelector(".row-edit-fk-link-wrap");
  if (!wrap) {
    return;
  }
  var pkSpan = wrap.querySelector(".row-edit-fk-pk");
  var link = wrap.querySelector("a");
  var pk = autocompleteRowPk(row);
  var url =
    pk === null || typeof pk === "undefined"
      ? null
      : foreignKeyRowUrl(autocompleteUrl, pk);
  if (!url) {
    wrap.hidden = true;
    pkSpan.textContent = "";
    link.removeAttribute("href");
    link.textContent = "";
    link.removeAttribute("aria-label");
    setRowEditBaseMetaText(meta, meta.dataset.baseMeta || "");
    updateRowEditFieldMetaHidden(meta);
    return;
  }
  setRowEditBaseMetaText(meta, rowEditMetaTextWithoutCurrentValue(meta));
  var pkText = String(pk);
  var linkText = foreignKeyLabelText(row);
  pkSpan.textContent = pkText;
  link.href = url;
  link.textContent = linkText;
  link.setAttribute(
    "aria-label",
    "Open referenced row " + pkText + " " + linkText + " in a new tab",
  );
  wrap.hidden = false;
  updateRowEditFieldMetaHidden(meta);
}

async function resolveForeignKeyMetaLink(control, autocompleteUrl, meta) {
  var value = control.value.trim();
  if (!value) {
    setForeignKeyMetaLink(meta, autocompleteUrl, null);
    return;
  }

  var url = new URL(autocompleteUrl, location.href);
  url.searchParams.set("q", value);
  try {
    var response = await fetch(url.toString(), {
      headers: {
        Accept: "application/json",
      },
    });
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    var data = await response.json();
    if (control.value.trim() !== value) {
      return;
    }
    var rows = (data && data.rows) || [];
    var row = rows.find(function (candidate) {
      var pk = autocompleteRowPk(candidate);
      return pk !== null && typeof pk !== "undefined" && String(pk) === value;
    });
    setForeignKeyMetaLink(meta, autocompleteUrl, row || null);
  } catch (_error) {
    if (control.value.trim() === value) {
      setForeignKeyMetaLink(meta, autocompleteUrl, null);
    }
  }
}

function tableInsertUrl(data, options) {
  data = data || tableInsertData();
  options = options || {};
  var url;
  if (data && data.path) {
    url = new URL(data.path, location.href);
  } else {
    url = tableBaseUrl();
    url.pathname = url.pathname.replace(/\/$/, "") + "/-/insert";
  }
  if (options.flashMessage) {
    url.searchParams.set("_message", "1");
  }
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
  if (isRowPage()) {
    url.searchParams.set("_redirect_to_table", "1");
  }
  return url.toString();
}

function rowUpdateUrl(row) {
  var url = rowResourceUrl(row);
  if (!url) {
    return "";
  }
  url.pathname = url.pathname.replace(/\/$/, "") + "/-/update";
  if (isRowPage()) {
    url.searchParams.set("_message", "1");
  }
  return url.toString();
}

function rowFragmentUrl(row) {
  var rowId = row.getAttribute("data-row");
  return rowFragmentUrlById(rowId);
}

function rowFragmentUrlById(rowId, state) {
  if (!rowId) {
    return "";
  }
  var url = tableBaseUrl(state);
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
  return (
    nextRowActionFocusTarget(row, "delete") || ensureRowMutationStatus(manager)
  );
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
    title: dialog.querySelector(".modal-title"),
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

  rowDeleteDialogState.confirmButton.addEventListener(
    "click",
    async function () {
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
        if (data && data.redirect) {
          state.shouldRestoreFocus = false;
          state.dialog.close();
          location.href = data.redirect;
          return;
        }

        var focusTarget = nextRowDeleteFocusTarget(
          state.currentRow,
          state.manager,
        );
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
    },
  );

  return rowDeleteDialogState;
}

function openRowDeleteDialog(button, manager) {
  var row = rowElementForActionButton(button);
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
  setRowDialogTitle(
    state.title,
    "Delete row",
    state.currentPkPath || "this row",
    rowTitleLabel(row),
  );
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

function shouldUseTextarea(value, columnType) {
  if (columnType && columnType.type === "textarea") {
    return true;
  }
  if (value && typeof value === "object") {
    return true;
  }
  var text = valueToEditText(value);
  return text.length > 80 || /[\r\n]/.test(text);
}

function rowEditValueKind(value) {
  if (value === null || typeof value === "undefined") {
    return "null";
  }
  if (typeof value === "number") {
    return "number";
  }
  if (typeof value === "boolean") {
    return "boolean";
  }
  return "string";
}

function rowEditControlElement(control, autocompleteUrl) {
  if (!autocompleteUrl || control.nodeName !== "INPUT") {
    return control;
  }
  var autocomplete = document.createElement("datasette-autocomplete");
  autocomplete.setAttribute("src", autocompleteUrl);
  autocomplete.setAttribute("suggest-on-focus", "");
  autocomplete.appendChild(control);
  return autocomplete;
}

function columnTypeForContext(columnType) {
  if (!columnType) {
    return null;
  }
  return {
    type: columnType.type,
    config: columnType.config || {},
  };
}

function defaultExpressionForContext(expression) {
  if (expression === null || typeof expression === "undefined") {
    return null;
  }
  return expression;
}

function columnFormControlContext(column, isPk, columnType, options) {
  options = options || {};
  var pageData = tablePageData();
  var defaultExpression = defaultExpressionForContext(
    options.defaultExpression,
  );
  return {
    mode: options.mode || "edit",
    database: options.database || pageData.database || null,
    table:
      options.table ||
      pageData.table ||
      (tableInsertData() && tableInsertData().table_name) ||
      null,
    tableUrl: options.tableUrl || pageData.tableUrl || null,
    column: column,
    columnType: columnTypeForContext(columnType),
    sqliteType: options.sqliteType || null,
    notNull: !!options.notnull,
    isPk: !!isPk,
    defaultExpression: defaultExpression,
    form: options.form || null,
    dialog: options.dialog || null,
  };
}

function makeColumnField(manager, context) {
  if (!manager || !manager.makeColumnField) {
    return null;
  }
  return manager.makeColumnField(context);
}

function createColumnFieldApi(options) {
  var control = options.control;
  var context = options.context;
  var field = {
    context: context,
    id: options.id,
    labelId: options.labelId,
    descriptionId: options.descriptionId,
    root: null,
    form: options.form || null,
    dialog: options.dialog || null,
    input: control,
    control: control,
    meta: options.meta || null,
    validationMessageElement: null,
    getValue: function () {
      return valueFromRowEditControl(control);
    },
    setValue: function (value) {
      if (
        value !== null &&
        typeof value !== "undefined" &&
        typeof value === "object"
      ) {
        throw new TypeError(
          "field.setValue() accepts strings, numbers, booleans or null; serialize objects before setting the field value",
        );
      }
      field.stopUsingSqliteDefault();
      control.value = valueToEditText(value);
      control.dataset.currentValueKind = rowEditValueKind(value);
    },
    getInitialValue: function () {
      return initialValueFromRowEditControl(control);
    },
    hasChanged: function () {
      return rowEditControlHasChanged(control);
    },
    clearValue: function () {
      field.setValue(null);
    },
    isUsingSqliteDefault: function () {
      return control.dataset.useSqliteDefault === "1";
    },
    useSqliteDefault: function () {
      if (
        context.defaultExpression === null ||
        typeof context.defaultExpression === "undefined"
      ) {
        return;
      }
      control.dataset.useSqliteDefault = "1";
      control.disabled = true;
      control.value = "";
      control.dataset.currentValueKind = "null";
      field.syncSqliteDefaultUi();
    },
    stopUsingSqliteDefault: function () {
      if (control.dataset.useSqliteDefault !== "1") {
        return;
      }
      control.dataset.useSqliteDefault = "0";
      control.disabled = false;
      field.syncSqliteDefaultUi();
    },
    syncSqliteDefaultUi: function () {},
    markClean: function () {
      markRowEditControlClean(control);
    },
    setValidity: function (message) {
      message = message || "";
      control.setCustomValidity(message);
      if (message) {
        control.setAttribute("aria-invalid", "true");
      } else {
        control.removeAttribute("aria-invalid");
      }
      var validationMessage = ensureColumnFieldValidationMessage(field);
      if (validationMessage) {
        validationMessage.textContent = message;
        validationMessage.hidden = !message;
      }
    },
    clearValidity: function () {
      field.setValidity("");
    },
  };
  field.markClean();
  return field;
}

function ensureColumnFieldValidationMessage(field) {
  if (field.validationMessageElement) {
    return field.validationMessageElement;
  }
  if (!field.meta) {
    return null;
  }
  var validationMessage = document.createElement("span");
  validationMessage.id = field.id + "-validation-error";
  validationMessage.className = "row-edit-field-validation-error";
  validationMessage.hidden = true;
  validationMessage.setAttribute("role", "alert");
  field.meta.appendChild(validationMessage);
  field.validationMessageElement = validationMessage;
  return validationMessage;
}

function renderColumnField(pluginControl, fieldApi) {
  if (!pluginControl || !pluginControl.render) {
    return null;
  }
  var pluginWrap = document.createElement("div");
  pluginWrap.className = "row-edit-plugin-control";
  pluginWrap.dataset.pluginName = pluginControl.pluginName || "";
  pluginWrap.dataset.column = fieldApi.context.column;
  if (fieldApi.context.columnType && fieldApi.context.columnType.type) {
    pluginWrap.dataset.columnType = fieldApi.context.columnType.type;
  }
  fieldApi.root = pluginWrap;
  try {
    var rendered = pluginControl.render(fieldApi);
    if (rendered && rendered.nodeType) {
      pluginWrap.appendChild(rendered);
    }
  } catch (error) {
    console.error("Error rendering column form control", error);
    return null;
  }
  pluginWrap._datasetteColumnField = pluginControl;
  pluginWrap._datasetteColumnFormField = fieldApi;
  return pluginWrap;
}

function validateJsonColumnField(field) {
  var value = field.input.value;
  if (value.trim() === "") {
    field.clearValidity();
    return true;
  }
  try {
    JSON.parse(value);
    field.clearValidity();
    return true;
  } catch (error) {
    field.setValidity(
      "Invalid JSON" + (error && error.message ? ": " + error.message : ""),
    );
    return false;
  }
}

function registerBuiltinColumnFieldPlugins(manager) {
  if (!manager || !manager.registerPlugin) {
    return;
  }
  manager.registerPlugin("datasette-json-column", {
    version: "1.0",
    makeColumnField: function (context) {
      if (!context.columnType || context.columnType.type !== "json") {
        return;
      }
      return {
        useTextarea: true,
        render: function (field) {
          field.input.addEventListener("input", function () {
            validateJsonColumnField(field);
          });
          field.input.addEventListener("change", function () {
            validateJsonColumnField(field);
          });
          validateJsonColumnField(field);
          return field.input;
        },
        focus: function (field) {
          field.input.focus();
        },
      };
    },
  });
}

function focusRowEditPluginControl(field) {
  var pluginWrap = field.querySelector(".row-edit-plugin-control");
  if (!pluginWrap) {
    return false;
  }
  var pluginControl = pluginWrap._datasetteColumnField;
  var fieldApi = pluginWrap._datasetteColumnFormField;
  if (pluginControl && pluginControl.focus) {
    try {
      pluginControl.focus(fieldApi);
      return true;
    } catch (error) {
      console.error("Error focusing column form control", error);
    }
  }
  return false;
}

function focusFirstRowEditControl(state, options) {
  options = options || {};
  var fields = state.fields.querySelectorAll(".row-edit-field");
  for (var i = 0; i < fields.length; i += 1) {
    var field = fields[i];
    var control = field.querySelector(".row-edit-input");
    if (!control) {
      continue;
    }
    if (options.skipReadonly && (control.readOnly || control.disabled)) {
      continue;
    }
    if (focusRowEditPluginControl(field)) {
      return true;
    }
    control.focus();
    return true;
  }
  return false;
}

function destroyRowEditFields(state) {
  if (!state || !state.fields) {
    return;
  }
  state.fields
    .querySelectorAll(".row-edit-plugin-control")
    .forEach(function (pluginWrap) {
      var pluginControl = pluginWrap._datasetteColumnField;
      var fieldApi = pluginWrap._datasetteColumnFormField;
      if (pluginControl && pluginControl.destroy) {
        try {
          pluginControl.destroy(fieldApi);
        } catch (error) {
          console.error("Error destroying column form control", error);
        }
      }
    });
  state.fields.innerHTML = "";
}

function createRowEditField(column, value, isPk, columnType, index, options) {
  options = options || {};
  var field = document.createElement("div");
  field.className = "row-edit-field";
  var defaultExpression = defaultExpressionForContext(
    options.defaultExpression,
  );
  var hasDefaultExpression = defaultExpression !== null;
  var useSqliteDefault = hasDefaultExpression && options.useSqliteDefault;

  var fieldId = "row-edit-field-" + index;
  var metaId = "row-edit-field-meta-" + index;
  var labelId = "row-edit-field-label-" + index;
  var label = document.createElement("label");
  label.className = "row-edit-label";
  label.id = labelId;
  label.setAttribute("for", fieldId);
  label.textContent = column;

  var controlWrap = document.createElement("div");
  controlWrap.className = "row-edit-control-wrap";

  var context = columnFormControlContext(column, isPk, columnType, options);
  var pluginControl = makeColumnField(options.manager, context);
  var useTextarea =
    (pluginControl && pluginControl.useTextarea === true) ||
    shouldUseTextarea(value, columnType);
  var control = useTextarea
    ? document.createElement("textarea")
    : document.createElement("input");
  control.className = "row-edit-input";
  control.id = fieldId;
  control.name = column;
  control.value = valueToEditText(value);
  control.setAttribute("aria-describedby", metaId);
  control.dataset.initialValue = valueToEditText(value);
  control.dataset.initialValueKind =
    options.valueKind || rowEditValueKind(value);
  control.dataset.primaryKey = isPk ? "1" : "0";
  control.dataset.currentValueKind = control.dataset.initialValueKind;
  if (hasDefaultExpression) {
    control.dataset.useSqliteDefault = useSqliteDefault ? "1" : "0";
  }
  if (useSqliteDefault) {
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
  if (options.autocompleteUrl) {
    meta.classList.add("row-edit-field-meta-autocomplete");
    meta.dataset.reserveSpace = "1";
  }
  var metaParts = [];
  if (isPk) {
    metaParts.push("Primary key");
  }
  if (options.notnull) {
    metaParts.push("Required");
  }
  if (hasDefaultExpression && !useSqliteDefault) {
    metaParts.push("SQLite default: " + defaultExpression);
  }
  if (value === null) {
    metaParts.push("Current value: NULL");
    control.placeholder = "NULL";
  }
  if (columnType && columnType.type) {
    metaParts.push("Custom type: " + columnType.type);
  }
  meta.dataset.baseMeta = metaParts.join(" · ");
  var baseMeta = document.createElement("span");
  baseMeta.className = "row-edit-base-meta";
  baseMeta.textContent = meta.dataset.baseMeta;
  meta.appendChild(baseMeta);
  if (options.autocompleteUrl) {
    var foreignKeyLinkWrap = document.createElement("span");
    foreignKeyLinkWrap.className = "row-edit-fk-link-wrap";
    foreignKeyLinkWrap.hidden = true;
    var foreignKeySeparator = document.createElement("span");
    foreignKeySeparator.className = "row-edit-fk-separator";
    foreignKeySeparator.textContent = meta.dataset.baseMeta ? " · " : "";
    foreignKeySeparator.hidden = !meta.dataset.baseMeta;
    foreignKeyLinkWrap.appendChild(foreignKeySeparator);
    var foreignKeyPk = document.createElement("span");
    foreignKeyPk.className = "row-edit-fk-pk";
    foreignKeyLinkWrap.appendChild(foreignKeyPk);
    foreignKeyLinkWrap.appendChild(document.createTextNode(" "));
    var foreignKeyLink = document.createElement("a");
    foreignKeyLink.className = "row-edit-fk-link";
    foreignKeyLink.target = "_blank";
    foreignKeyLink.rel = "noopener noreferrer";
    foreignKeyLinkWrap.appendChild(foreignKeyLink);
    meta.appendChild(foreignKeyLinkWrap);
    updateRowEditFieldMetaHidden(meta);
  }
  var fieldApi = createColumnFieldApi({
    id: fieldId,
    labelId: labelId,
    descriptionId: metaId,
    control: control,
    meta: meta,
    input: control,
    form: options.form || null,
    dialog: options.dialog || null,
    context: context,
  });
  field._datasetteColumnFormField = fieldApi;
  var pluginControlElement = renderColumnField(pluginControl, fieldApi);
  var controlElement =
    pluginControlElement ||
    rowEditControlElement(control, options.autocompleteUrl);
  if (options.autocompleteUrl && !pluginControlElement) {
    control.addEventListener("input", function () {
      setForeignKeyMetaLink(meta, options.autocompleteUrl, null);
    });
    control.addEventListener("change", function () {
      resolveForeignKeyMetaLink(control, options.autocompleteUrl, meta);
    });
    controlElement.addEventListener(
      "datasette-autocomplete-select",
      function (ev) {
        setForeignKeyMetaLink(
          meta,
          options.autocompleteUrl,
          ev.detail && ev.detail.row,
        );
      },
    );
    resolveForeignKeyMetaLink(control, options.autocompleteUrl, meta);
  }

  if (hasDefaultExpression) {
    var defaultBlock = document.createElement("div");
    defaultBlock.className = "row-edit-default";
    defaultBlock.setAttribute("aria-describedby", metaId);

    var defaultText = document.createElement("span");
    defaultText.className = "row-edit-default-text";
    defaultText.appendChild(document.createTextNode("default "));
    var defaultCode = document.createElement("code");
    defaultCode.className = "row-edit-default-code";
    defaultCode.textContent = defaultExpression;
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

    var useSqliteDefaultButton = document.createElement("button");
    useSqliteDefaultButton.type = "button";
    useSqliteDefaultButton.className = "row-edit-default-button";
    useSqliteDefaultButton.textContent = "Use default";
    useSqliteDefaultButton.setAttribute(
      "aria-label",
      "Use SQLite default for " + column,
    );

    setValueButton.addEventListener("click", function () {
      fieldApi.stopUsingSqliteDefault();
      control.focus();
    });

    useSqliteDefaultButton.addEventListener("click", function () {
      fieldApi.useSqliteDefault();
      setValueButton.focus();
    });

    defaultBlock.appendChild(defaultText);
    defaultBlock.appendChild(setValueButton);
    customWrap.appendChild(controlElement);
    customWrap.appendChild(useSqliteDefaultButton);
    controlWrap.appendChild(defaultBlock);
    controlWrap.appendChild(customWrap);
    fieldApi.syncSqliteDefaultUi = function () {
      var usingDefault = fieldApi.isUsingSqliteDefault();
      defaultBlock.hidden = !usingDefault;
      customWrap.hidden = usingDefault;
    };
    fieldApi.syncSqliteDefaultUi();
  } else {
    controlWrap.appendChild(controlElement);
  }
  if (meta.textContent || options.autocompleteUrl) {
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
  state.saveButton.disabled =
    state.isLoading ||
    state.isSaving ||
    !state.hasLoaded ||
    state.submitDelayActive;
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

function clearRowEditDialogSubmitDelay(state) {
  if (state.submitDelayTimer) {
    clearTimeout(state.submitDelayTimer);
  }
  state.submitDelayTimer = null;
  state.submitDelayActive = false;
}

function setRowEditDialogSubmitDelay(state, delayMs) {
  clearRowEditDialogSubmitDelay(state);
  delayMs = Number(delayMs) || 0;
  if (delayMs <= 0) {
    updateRowEditDialogButtons(state);
    return;
  }
  state.submitDelayActive = true;
  state.submitDelayTimer = setTimeout(function () {
    state.submitDelayTimer = null;
    state.submitDelayActive = false;
    updateRowEditDialogButtons(state);
  }, delayMs);
  updateRowEditDialogButtons(state);
}

function valueFromRowEditControl(control) {
  var value = control.value;
  return valueFromRowEditText(
    control.name,
    value,
    rowEditControlValueKind(control),
  );
}

function valueFromRowEditText(name, value, initialValueKind) {
  var trimmed = value.trim();

  if (initialValueKind === "null" && value === "") {
    return null;
  }
  if (initialValueKind === "number") {
    if (trimmed === "") {
      return null;
    }
    var numberValue = Number(trimmed);
    if (Number.isNaN(numberValue)) {
      throw new Error(name + " must be a number");
    }
    return numberValue;
  }
  if (initialValueKind === "boolean") {
    if (/^(true|1|yes)$/i.test(trimmed)) {
      return true;
    }
    if (/^(false|0|no)$/i.test(trimmed)) {
      return false;
    }
    throw new Error(name + " must be true or false");
  }
  return value;
}

function initialValueFromRowEditControl(control) {
  return valueFromRowEditText(
    control.name,
    control.dataset.initialValue || "",
    control.dataset.initialValueKind || "string",
  );
}

function rowEditControlValueKind(control) {
  return (
    control.dataset.currentValueKind ||
    control.dataset.initialValueKind ||
    "string"
  );
}

function rowEditControlCleanValue(control) {
  if (Object.prototype.hasOwnProperty.call(control.dataset, "cleanValue")) {
    return control.dataset.cleanValue;
  }
  return control.dataset.initialValue || "";
}

function rowEditControlCleanValueKind(control) {
  return (
    control.dataset.cleanValueKind ||
    control.dataset.initialValueKind ||
    "string"
  );
}

function rowEditControlCleanUsesSqliteDefault(control) {
  if (
    Object.prototype.hasOwnProperty.call(
      control.dataset,
      "cleanUseSqliteDefault",
    )
  ) {
    return control.dataset.cleanUseSqliteDefault === "1";
  }
  return false;
}

function markRowEditControlClean(control) {
  control.dataset.cleanValue = control.value;
  control.dataset.cleanValueKind = rowEditControlValueKind(control);
  control.dataset.cleanUseSqliteDefault =
    control.dataset.useSqliteDefault === "1" ? "1" : "0";
}

function cleanValueFromRowEditControl(control) {
  return valueFromRowEditText(
    control.name,
    rowEditControlCleanValue(control),
    rowEditControlCleanValueKind(control),
  );
}

function rowEditValuesMatch(left, right) {
  if (left === right) {
    return true;
  }
  if (left && right && typeof left === "object" && typeof right === "object") {
    return JSON.stringify(left) === JSON.stringify(right);
  }
  return false;
}

function rowEditControlHasChanged(control) {
  var usingSqliteDefault = control.dataset.useSqliteDefault === "1";
  var cleanUsesSqliteDefault = rowEditControlCleanUsesSqliteDefault(control);
  if (usingSqliteDefault || cleanUsesSqliteDefault) {
    return usingSqliteDefault !== cleanUsesSqliteDefault;
  }
  if (
    control.value === rowEditControlCleanValue(control) &&
    rowEditControlValueKind(control) === rowEditControlCleanValueKind(control)
  ) {
    return false;
  }
  try {
    return !rowEditValuesMatch(
      valueFromRowEditControl(control),
      cleanValueFromRowEditControl(control),
    );
  } catch (_error) {
    return true;
  }
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
    if (control.dataset.useSqliteDefault === "1") {
      return;
    }
    if (control.dataset.omitIfBlank === "1" && control.value === "") {
      return;
    }
    if (
      state.mode === "edit" &&
      control.value === (control.dataset.initialValue || "") &&
      (control.dataset.currentValueKind ||
        control.dataset.initialValueKind ||
        "string") === (control.dataset.initialValueKind || "string")
    ) {
      return;
    }
    var value = valueFromRowEditControl(control);
    if (state.mode === "edit") {
      try {
        if (
          rowEditValuesMatch(value, initialValueFromRowEditControl(control))
        ) {
          return;
        }
      } catch (_error) {
        // If the original value cannot be parsed using the field's current
        // type, treat the field as changed and submit the corrected value.
      }
    }
    values[control.name] = value;
  });
  return values;
}

function rowEditDialogHasChanges(state) {
  if (!state || !state.hasLoaded || state.isLoading) {
    return false;
  }
  var fields = state.fields.querySelectorAll(".row-edit-field");
  for (var i = 0; i < fields.length; i += 1) {
    var fieldApi = fields[i]._datasetteColumnFormField;
    if (fieldApi && fieldApi.hasChanged && fieldApi.hasChanged()) {
      return true;
    }
  }
  return false;
}

function confirmDiscardRowEditChanges(state) {
  if (!rowEditDialogHasChanges(state)) {
    return true;
  }
  var message =
    state.mode === "insert"
      ? "Discard this new row?"
      : "Discard unsaved changes to this row?";
  return window.confirm(message);
}

function closeRowEditDialogIfConfirmed(state) {
  if (!state || state.isSaving) {
    return false;
  }
  if (!confirmDiscardRowEditChanges(state)) {
    return false;
  }
  state.shouldRestoreFocus = true;
  state.dialog.close();
  return true;
}

function scheduleCloseRowEditDialogIfConfirmed(state) {
  // Fix for an issue in Safari where hitting Esc would show
  // the confirm() prompt asking if state should be discarded
  // but the Esc key press would then cancel that dialog too.
  // Wait for keyup, then move the confirm() to a fresh timer tick.
  if (!state || state.isSaving || state.isClosePending) {
    return false;
  }
  if (!rowEditDialogHasChanges(state)) {
    state.shouldRestoreFocus = true;
    state.dialog.close();
    return true;
  }
  state.isClosePending = true;
  var closeAfterKeyup = function () {
    if (!state.isClosePending) {
      return;
    }
    state.isClosePending = false;
    closeRowEditDialogIfConfirmed(state);
  };
  var onKeyup = function (ev) {
    if (ev.key !== "Escape") {
      return;
    }
    document.removeEventListener("keyup", onKeyup, true);
    setTimeout(closeAfterKeyup, 0);
  };
  document.addEventListener("keyup", onKeyup, true);
  return true;
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

function rowUrlById(rowId, state) {
  if (!rowId) {
    return null;
  }
  var url = tableBaseUrl(state);
  url.pathname = url.pathname.replace(/\/$/, "") + "/" + rowId;
  return url.toString();
}

function insertDialogResult(state, row, rowId) {
  var result = {
    ok: true,
    status: "inserted",
    database: state.currentDatabase || null,
    table: state.currentTable || null,
    row: row || null,
  };
  if (rowId) {
    result.row_id = tildeDecode(rowId);
    result.row_path = rowId;
    result.row_url = rowUrlById(rowId, state);
  }
  return result;
}

function resolveInsertDialog(state, result) {
  if (!state.currentInsertDialogResolve) {
    return;
  }
  var resolve = state.currentInsertDialogResolve;
  state.currentInsertDialogResolve = null;
  resolve(result);
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
  if (
    state.isLoading ||
    state.isSaving ||
    !state.hasLoaded ||
    state.submitDelayActive
  ) {
    return;
  }
  clearRowEditDialogError(state);
  setRowEditDialogSaving(state, true);

  try {
    var url =
      state.mode === "insert" ? state.currentInsertUrl : state.currentUpdateUrl;
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
      var insertData = state.currentInsertData || tableInsertData() || {};
      var insertedRowData =
        data && data.rows && data.rows.length ? data.rows[0] : null;
      var insertedRowId = rowPathFromRowData(
        insertedRowData,
        insertData.primary_keys || [],
      );
      var result = insertDialogResult(state, insertedRowData, insertedRowId);
      state.shouldRestoreFocus = false;
      if (!state.refreshAfterInsert) {
        resolveInsertDialog(state, result);
        state.dialog.close();
        var status = showRowMutationStatus(
          state.manager,
          insertedRowId
            ? insertedRowStatusMessage(tildeDecode(insertedRowId), null)
            : "Inserted row.",
          false,
        );
        status.focus();
        return;
      }
      if (!insertedRowId) {
        resolveInsertDialog(state, result);
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
      state.currentFragmentUrl = rowFragmentUrlById(insertedRowId, state);
      var insertedRow = null;
      try {
        insertedRow = await fetchUpdatedRowElement(state);
      } catch (_error) {
        resolveInsertDialog(state, result);
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
        var insertedStatusMessage = insertedRowStatusMessage(
          tildeDecode(insertedRowId),
          rowTitleLabel(insertedRow),
        );
        var addedRow = addInsertedRowToPage(insertedRow);
        resolveInsertDialog(state, result);
        state.dialog.close();
        showRowMutationStatus(state.manager, insertedStatusMessage, false);
        if (addedRow) {
          var insertedFocusTarget =
            addedRow.querySelector('button[data-row-action="edit"]') ||
            addedRow;
          insertedFocusTarget.focus();
        }
      } else {
        resolveInsertDialog(state, result);
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

    if (isRowPage()) {
      state.shouldRestoreFocus = false;
      state.dialog.close();
      location.reload();
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
        importedRow.querySelector('button[data-row-action="edit"]') ||
        importedRow;
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

  destroyRowEditFields(state);
  columns.forEach(function (column, index) {
    state.fields.appendChild(
      createRowEditField(
        column,
        row ? row[column] : null,
        primaryKeys.indexOf(column) !== -1,
        columnTypes[column],
        index,
        {
          autocompleteUrl: foreignKeyAutocompleteUrl(column, state),
          dialog: state.dialog,
          form: state.form,
          manager: state.manager,
          mode: state.mode,
          primaryKeyReadonly: true,
        },
      ),
    );
  });

  state.hasLoaded = true;
  updateRowEditDialogButtons(state);
  if (!focusFirstRowEditControl(state, { skipReadonly: true })) {
    focusFirstRowEditControl(state) || state.cancelButton.focus();
  }
}

function renderRowInsertFields(state, data, suggestedRow) {
  var columns = data.columns || [];
  suggestedRow = suggestedRow || {};

  destroyRowEditFields(state);
  columns.forEach(function (column, index) {
    var hasSuggestedValue = Object.prototype.hasOwnProperty.call(
      suggestedRow,
      column.name,
    );
    var value = hasSuggestedValue ? suggestedRow[column.name] : "";
    var useSqliteDefault = column.default !== null && !hasSuggestedValue;
    state.fields.appendChild(
      createRowEditField(
        column.name,
        value,
        !!column.is_pk,
        column.column_type,
        index,
        {
          autocompleteUrl: foreignKeyAutocompleteUrl(column.name, state),
          database: state.currentDatabase,
          dialog: state.dialog,
          form: state.form,
          defaultExpression: column.default,
          manager: state.manager,
          mode: state.mode,
          notnull: column.notnull,
          primaryKeyReadonly: false,
          sqliteType: column.sqlite_type,
          table: state.currentTable,
          tableUrl: state.currentTableUrl,
          useSqliteDefault: useSqliteDefault,
          valueKind: hasSuggestedValue
            ? rowEditValueKind(value)
            : column.value_kind,
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
  var firstDefaultButton = state.fields.querySelector(
    ".row-edit-default-set-value",
  );
  if (firstDefaultButton) {
    firstDefaultButton.focus();
  } else {
    focusFirstRowEditControl(state, { skipReadonly: true }) ||
      state.saveButton.focus();
  }
}

function setRowDialogTitle(title, text, codeText, labelText) {
  title.textContent = "";
  var action = document.createElement("span");
  action.className = "row-dialog-action";
  action.textContent = text;
  title.appendChild(action);
  if (!codeText) {
    return;
  }
  title.appendChild(document.createTextNode(" "));
  var code = document.createElement("code");
  code.textContent = codeText;
  title.appendChild(code);
  if (labelText && labelText !== codeText) {
    title.appendChild(document.createTextNode(" "));
    var label = document.createElement("span");
    label.className = "row-dialog-label";
    label.textContent = labelText;
    title.appendChild(label);
  }
}

function setRowEditDialogSummary(state, message) {
  if (message) {
    state.summary.hidden = false;
    state.summary.textContent = message;
    state.dialog.setAttribute("aria-describedby", state.summary.id);
  } else {
    state.summary.hidden = true;
    state.summary.textContent = "";
    state.dialog.removeAttribute("aria-describedby");
  }
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
    currentDatabase: null,
    currentTable: null,
    currentTableUrl: null,
    currentForeignKeys: null,
    currentInsertData: null,
    currentInsertDialogResolve: null,
    currentInsertUrl: null,
    currentUpdateUrl: null,
    currentFragmentUrl: null,
    mode: "edit",
    refreshAfterInsert: false,
    loadId: 0,
    manager: manager,
    isLoading: false,
    isSaving: false,
    isClosePending: false,
    hasLoaded: false,
    submitDelayActive: false,
    submitDelayTimer: null,
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
    if (ev.target === dialog) {
      closeRowEditDialogIfConfirmed(rowEditDialogState);
    }
  });

  dialog.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") {
      return;
    }
    ev.preventDefault();
    scheduleCloseRowEditDialogIfConfirmed(rowEditDialogState);
  });

  dialog.addEventListener("cancel", function (ev) {
    ev.preventDefault();
    scheduleCloseRowEditDialogIfConfirmed(rowEditDialogState);
  });

  dialog.addEventListener("close", function () {
    var state = rowEditDialogState;
    if (state.currentInsertDialogResolve) {
      resolveInsertDialog(state, {
        ok: false,
        status: "cancelled",
        database: state.currentDatabase || null,
        table: state.currentTable || null,
      });
    }
    state.loadId += 1;
    state.isClosePending = false;
    clearRowEditDialogError(state);
    state.hasLoaded = false;
    clearRowEditDialogSubmitDelay(state);
    destroyRowEditFields(state);
    setRowEditDialogLoading(state, false);
    setRowEditDialogSaving(state, false);
    state.currentRow = null;
    state.currentRowId = null;
    state.currentPkPath = null;
    state.currentDatabase = null;
    state.currentTable = null;
    state.currentTableUrl = null;
    state.currentForeignKeys = null;
    state.currentInsertData = null;
    state.currentInsertUrl = null;
    state.currentUpdateUrl = null;
    state.currentFragmentUrl = null;
    state.refreshAfterInsert = false;
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
  var row = rowElementForActionButton(button);
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
  state.currentDatabase = tablePageData().database || null;
  state.currentTable = tablePageData().table || null;
  state.currentTableUrl = tablePageData().tableUrl || null;
  state.currentForeignKeys = tableForeignKeys();
  state.currentInsertData = null;
  state.currentInsertDialogResolve = null;
  state.currentInsertUrl = null;
  state.currentUpdateUrl = rowUpdateUrl(row);
  state.currentFragmentUrl = rowFragmentUrl(row);
  state.refreshAfterInsert = false;
  if (state.currentUpdateUrl) {
    state.form.action = new URL(
      state.currentUpdateUrl,
      location.href,
    ).toString();
  } else {
    state.form.removeAttribute("action");
  }
  state.shouldRestoreFocus = true;
  state.hasLoaded = false;
  state.loadId += 1;
  var loadId = state.loadId;

  clearRowEditDialogError(state);
  setRowEditDialogLoading(state, true);
  destroyRowEditFields(state);
  setRowDialogTitle(
    state.title,
    "Edit row",
    state.currentPkPath || "this row",
    rowTitleLabel(row),
  );
  setRowEditDialogSummary(state, "");

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

function validateSuggestedInsertRow(insertData, row) {
  row = row || {};
  var columns = (insertData && insertData.columns) || [];
  var columnNames = {};
  columns.forEach(function (column) {
    columnNames[column.name] = true;
  });
  var unknownColumns = Object.keys(row).filter(function (column) {
    return !columnNames[column];
  });
  if (unknownColumns.length) {
    throw new Error("Unknown column: " + unknownColumns.join(", "));
  }
}

function insertDialogMetadataUrl(manager, database, table) {
  return datasetteManagerPath(
    manager,
    "/" + tildeEncode(database) + "/" + tildeEncode(table) + "/-/insert",
  );
}

async function fetchInsertDialogMetadata(manager, database, table) {
  var response = await fetch(
    insertDialogMetadataUrl(manager, database, table),
    {
      headers: {
        Accept: "application/json",
      },
    },
  );
  var data = null;
  try {
    data = await response.json();
  } catch (_error) {
    data = null;
  }
  if (!response.ok || (data && data.ok === false)) {
    throw rowMutationRequestError(response, data);
  }
  if (!data || !data.insert_row) {
    throw new Error("Insert dialog metadata was not returned");
  }
  return data;
}

function hasForeignKeyAutocomplete(foreignKeys) {
  return foreignKeys && Object.keys(foreignKeys).length > 0;
}

async function ensureAutocompleteLoaded(manager, foreignKeys) {
  if (!hasForeignKeyAutocomplete(foreignKeys)) {
    return;
  }
  if (window.customElements && customElements.get("datasette-autocomplete")) {
    return;
  }
  if (manager && manager.loadAutocomplete) {
    await manager.loadAutocomplete();
  }
}

function openRowInsertDialogWithData(options) {
  var insertData = options.insertData;
  if (!insertData) {
    return false;
  }
  var state = ensureRowEditDialog(options.manager);
  if (!state) {
    return false;
  }
  if (state.dialog.open) {
    return false;
  }

  var pageData = tablePageData();
  state.manager = options.manager;
  state.mode = "insert";
  state.currentButton = options.button || null;
  state.currentRow = null;
  state.currentRowId = null;
  state.currentPkPath = null;
  state.currentDatabase = options.database || pageData.database || null;
  state.currentTable =
    options.table || insertData.table_name || pageData.table || null;
  state.currentTableUrl = options.tableUrl || pageData.tableUrl || null;
  state.currentForeignKeys = options.foreignKeys || tableForeignKeys();
  state.currentInsertData = insertData;
  state.currentInsertDialogResolve = options.resolve || null;
  state.currentInsertUrl = tableInsertUrl(insertData, {
    flashMessage: options.flashMessage,
  });
  state.currentUpdateUrl = null;
  state.currentFragmentUrl = null;
  state.refreshAfterInsert = !!options.refreshAfterInsert;
  state.shouldRestoreFocus = !!options.button;
  state.hasLoaded = false;
  state.loadId += 1;

  if (state.currentInsertUrl) {
    state.form.action = new URL(
      state.currentInsertUrl,
      location.href,
    ).toString();
  } else {
    state.form.removeAttribute("action");
  }

  clearRowEditDialogError(state);
  setRowEditDialogLoading(state, false);
  destroyRowEditFields(state);
  setRowEditDialogSubmitDelay(state, options.submitDelayMs);
  setRowDialogTitle(
    state.title,
    insertData.table_name ? "Insert row into" : "Insert row",
    insertData.table_name,
  );
  setRowEditDialogSummary(state, options.message || "");

  if (!state.dialog.open) {
    state.dialog.showModal();
  }
  renderRowInsertFields(state, insertData, options.suggestedRow || {});
  return true;
}

function openRowInsertDialog(button, manager) {
  openRowInsertDialogWithData({
    button: button,
    manager: manager,
    insertData: tableInsertData(),
    database: tablePageData().database,
    table: tablePageData().table,
    tableUrl: tablePageData().tableUrl,
    foreignKeys: tableForeignKeys(),
    refreshAfterInsert: true,
  });
}

async function insertDialog(manager, database, table, row, message, options) {
  options = options || {};
  if (typeof database !== "string" || !database) {
    throw new Error("database must be a string");
  }
  if (typeof table !== "string" || !table) {
    throw new Error("table must be a string");
  }
  if (!row) {
    row = {};
  }
  if (typeof row !== "object" || Array.isArray(row)) {
    throw new Error("row must be an object");
  }
  var metadata = await fetchInsertDialogMetadata(manager, database, table);
  var insertData = metadata.insert_row;
  var foreignKeys = metadata.foreign_keys || {};
  validateSuggestedInsertRow(insertData, row);
  await ensureAutocompleteLoaded(manager, foreignKeys);
  return new Promise(function (resolve, reject) {
    var opened = openRowInsertDialogWithData({
      manager: manager,
      insertData: insertData,
      database: metadata.table && metadata.table.database,
      table: metadata.table && metadata.table.name,
      tableUrl: metadata.table && metadata.table.url,
      foreignKeys: foreignKeys,
      suggestedRow: row,
      message: message || "",
      resolve: resolve,
      flashMessage: !!options.flashMessage,
      refreshAfterInsert: false,
      submitDelayMs: options.submitDelayMs,
    });
    if (!opened) {
      reject(new Error("Could not open the insert dialog"));
    }
  });
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

function installEditTools(manager) {
  if (!manager || manager.__editToolsInstalled) {
    return;
  }
  manager.__editToolsInstalled = true;

  registerBuiltinColumnFieldPlugins(manager);
  initRowInsertActions(manager);
  initRowEditActions(manager);
  initRowDeleteActions(manager);
}

window.__DATASETTE_EDIT_TOOLS__ = {
  install: installEditTools,
  insertDialog: insertDialog,
};

if (window.__DATASETTE__) {
  installEditTools(window.__DATASETTE__);
}

document.addEventListener("datasette_init", function (evt) {
  installEditTools(evt.detail);
});
