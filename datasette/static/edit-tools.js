var ROW_DELETE_DIALOG_ID = "row-delete-dialog";
var rowDeleteDialogState = null;
var ROW_EDIT_DIALOG_ID = "row-edit-dialog";
var rowEditDialogState = null;
var ROW_EDIT_BINARY_IMAGE_PREVIEW_MAX_BYTES = 10 * 1024 * 1024;
var TABLE_CREATE_DIALOG_ID = "table-create-dialog";
var tableCreateDialogState = null;
var TABLE_ALTER_DIALOG_ID = "table-alter-dialog";
var tableAlterDialogState = null;

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

function databaseCreateTableData() {
  return (
    window._datasetteDatabaseData && window._datasetteDatabaseData.createTable
  );
}

function tableCreateColumnTypes() {
  var data = databaseCreateTableData() || {};
  return data.columnTypes && data.columnTypes.length
    ? data.columnTypes
    : ["text", "integer", "float", "blob"];
}

function tableCreateDefaultExpressions() {
  var data = databaseCreateTableData() || {};
  return data.defaultExpressions || [];
}

var SQLITE_COLUMN_TYPE_LABELS = {
  float: "floating point number",
  real: "floating point number",
  blob: "blob - binary data",
};

function sqliteColumnTypeLabel(type) {
  return SQLITE_COLUMN_TYPE_LABELS[type] || type;
}

function populateSqliteColumnTypeSelect(select, type, options) {
  options.forEach(function (option) {
    var optionElement = document.createElement("option");
    optionElement.value = option;
    optionElement.textContent = sqliteColumnTypeLabel(option);
    select.appendChild(optionElement);
  });
  select.value = options.indexOf(type) === -1 ? options[0] : type;
}

function updateSelectPlaceholder(select, placeholderClass) {
  select.classList.toggle(placeholderClass, !select.value);
}

function createCustomColumnTypeSelect(options, className, placeholderClass) {
  var select = document.createElement("select");
  select.className = className;
  select.setAttribute("aria-label", "Custom column type");
  var blankOption = document.createElement("option");
  blankOption.value = "";
  blankOption.textContent = "- custom type -";
  select.appendChild(blankOption);
  options.forEach(function (option) {
    var optionElement = document.createElement("option");
    optionElement.value = option.name;
    optionElement.textContent = option.description
      ? option.description + " (" + option.name + ")"
      : option.name;
    select.appendChild(optionElement);
  });
  updateSelectPlaceholder(select, placeholderClass);
  return select;
}

function normalizeDefaultExpressionOption(option) {
  if (typeof option === "string") {
    return {
      value: option,
      label: option.replace(/_/g, " "),
      sqliteType: "",
    };
  }
  option = option || {};
  return {
    value: option.value || "",
    label: option.label || option.value || "",
    sqliteType: option.sqliteType || "",
  };
}

function defaultExpressionOptionForValue(options, value) {
  var match = null;
  (options || []).some(function (option) {
    var normalized = normalizeDefaultExpressionOption(option);
    if (normalized.value === value) {
      match = normalized;
      return true;
    }
    return false;
  });
  return match;
}

function defaultExpressionLabelForValue(options, value) {
  var option = defaultExpressionOptionForValue(options, value);
  return option && option.label
    ? option.label
    : value
      ? value.replace(/_/g, " ")
      : "";
}

function applyDefaultExpressionColumnType(row, prefix, options, columnTypes) {
  var defaultExprSelect = row.querySelector("." + prefix + "-default-expr");
  var typeSelect = row.querySelector("." + prefix + "-column-type");
  if (!defaultExprSelect || !typeSelect || !defaultExprSelect.value) {
    return false;
  }
  var option = defaultExpressionOptionForValue(
    options,
    defaultExprSelect.value,
  );
  if (
    option &&
    option.sqliteType &&
    (columnTypes || []).indexOf(option.sqliteType) !== -1 &&
    typeSelect.value !== option.sqliteType
  ) {
    typeSelect.value = option.sqliteType;
    return true;
  }
  return false;
}

function createDefaultExpressionSelect(
  options,
  className,
  placeholderClass,
  value,
) {
  var select = document.createElement("select");
  select.className = className;
  var blankOption = document.createElement("option");
  blankOption.value = "";
  blankOption.textContent = "- default expr -";
  select.appendChild(blankOption);
  options.forEach(function (option) {
    var normalized = normalizeDefaultExpressionOption(option);
    if (!normalized.value) {
      return;
    }
    var optionElement = document.createElement("option");
    optionElement.value = normalized.value;
    optionElement.textContent = normalized.label;
    select.appendChild(optionElement);
  });
  select.value = value || "";
  updateSelectPlaceholder(select, placeholderClass);
  return select;
}

function createSchemaDialogDefaultControls(prefix, index, expressions, column) {
  var defaultDetails = document.createElement("details");
  defaultDetails.className = prefix + "-default-options";
  defaultDetails.open = !!(
    column &&
    (column.defaultValue || column.defaultExpr)
  );
  var summary = document.createElement("summary");
  summary.textContent = "Set a default value";
  defaultDetails.appendChild(summary);

  var defaultGrid = document.createElement("div");
  defaultGrid.className = prefix + "-default-grid";

  var defaultExprId = prefix + "-column-default-expr-" + index;
  var defaultExprField = document.createElement("div");
  defaultExprField.className = prefix + "-detail-field";
  var defaultExprLabel = document.createElement("label");
  defaultExprLabel.className = prefix + "-detail-label";
  defaultExprLabel.setAttribute("for", defaultExprId);
  defaultExprLabel.textContent = "Default expression";
  var defaultExprSelect = createDefaultExpressionSelect(
    expressions,
    prefix + "-input " + prefix + "-default-expr",
    prefix + "-input-placeholder",
    column && column.defaultExpr,
  );
  defaultExprSelect.id = defaultExprId;
  defaultExprSelect.setAttribute("aria-label", "Default expression");
  defaultExprField.appendChild(defaultExprLabel);
  defaultExprField.appendChild(defaultExprSelect);

  var defaultId = prefix + "-column-default-" + index;
  var defaultField = document.createElement("div");
  defaultField.className = prefix + "-detail-field";
  var defaultLabel = document.createElement("label");
  defaultLabel.className = prefix + "-detail-label";
  defaultLabel.setAttribute("for", defaultId);
  defaultLabel.textContent = "or default to a specific value";
  var defaultInput = document.createElement("input");
  defaultInput.id = defaultId;
  defaultInput.className = prefix + "-input " + prefix + "-default";
  defaultInput.type = "text";
  defaultInput.autocomplete = "off";
  defaultInput.placeholder = "default";
  defaultInput.setAttribute("aria-label", "or default to a specific value");
  defaultInput.value = column && column.defaultValue ? column.defaultValue : "";
  defaultField.appendChild(defaultLabel);
  defaultField.appendChild(defaultInput);

  defaultGrid.appendChild(defaultExprField);
  defaultGrid.appendChild(defaultField);
  defaultDetails.appendChild(defaultGrid);

  return {
    controls: defaultDetails,
    defaultInput: defaultInput,
    defaultExprSelect: defaultExprSelect,
  };
}

function syncSchemaDialogDefaultControls(row, prefix) {
  if (!row) {
    return;
  }
  var defaultInput = row.querySelector("." + prefix + "-default");
  var defaultExprSelect = row.querySelector("." + prefix + "-default-expr");
  if (!defaultInput || !defaultExprSelect) {
    return;
  }
  updateSelectPlaceholder(defaultExprSelect, prefix + "-input-placeholder");
}

var COLUMN_MOVE_ICONS = {
  top: '<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m18 17-6-6-6 6"></path><path d="m18 11-6-6-6 6"></path></svg>',
  up: '<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6"></path></svg>',
  down: '<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"></path></svg>',
  bottom:
    '<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 7 6 6 6-6"></path><path d="m6 13 6 6 6-6"></path></svg>',
  remove:
    '<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path></svg>',
};

function createSchemaDialogIconButton(prefix, modifier, ariaLabel, title, svg) {
  var button = document.createElement("button");
  button.type = "button";
  button.className = prefix + "-icon-button " + prefix + "-" + modifier;
  button.setAttribute("aria-label", ariaLabel);
  button.title = title;
  button.dataset.defaultTitle = title;
  button.innerHTML = svg;
  return button;
}

function createSchemaDialogMoveControls(prefix) {
  var moveControls = document.createElement("div");
  moveControls.className = prefix + "-move-controls";

  var moveTopButton = createSchemaDialogIconButton(
    prefix,
    "move-top",
    "Move column to top",
    "Move column to top",
    COLUMN_MOVE_ICONS.top,
  );
  var moveUpButton = createSchemaDialogIconButton(
    prefix,
    "move-up",
    "Move column up",
    "Move column up",
    COLUMN_MOVE_ICONS.up,
  );
  var moveDownButton = createSchemaDialogIconButton(
    prefix,
    "move-down",
    "Move column down",
    "Move column down",
    COLUMN_MOVE_ICONS.down,
  );
  var moveBottomButton = createSchemaDialogIconButton(
    prefix,
    "move-bottom",
    "Move column to bottom",
    "Move column to bottom",
    COLUMN_MOVE_ICONS.bottom,
  );

  moveControls.appendChild(moveTopButton);
  moveControls.appendChild(moveUpButton);
  moveControls.appendChild(moveDownButton);
  moveControls.appendChild(moveBottomButton);

  return {
    controls: moveControls,
    topButton: moveTopButton,
    upButton: moveUpButton,
    downButton: moveDownButton,
    bottomButton: moveBottomButton,
  };
}

function createSchemaDialogMoreOptionsButton(prefix, details) {
  var expandButton = document.createElement("button");
  expandButton.type = "button";
  expandButton.className = prefix + "-more-options";
  expandButton.setAttribute("aria-label", "Toggle column settings");
  expandButton.setAttribute("aria-controls", details.id);
  expandButton.setAttribute("aria-expanded", details.hidden ? "false" : "true");
  updateSchemaDialogMoreOptionsButton(expandButton);
  return expandButton;
}

function updateSchemaDialogMoreOptionsButton(button) {
  var isExpanded = button.getAttribute("aria-expanded") === "true";
  button.textContent = isExpanded ? "v Hide options" : "> Advanced options";
  button.title = isExpanded ? "Hide column settings" : "Show column settings";
}

function toggleSchemaDialogMoreOptions(button, details) {
  var isExpanded = button.getAttribute("aria-expanded") === "true";
  details.hidden = isExpanded;
  button.setAttribute("aria-expanded", isExpanded ? "false" : "true");
  updateSchemaDialogMoreOptionsButton(button);
}

function schemaDialogRows(state, prefix) {
  return Array.prototype.slice.call(
    state.columnList.querySelectorAll("." + prefix + "-column-row"),
  );
}

function schemaDialogRowIsPrimaryKey(row, prefix) {
  var input = row && row.querySelector("." + prefix + "-primary-key-input");
  return !!(input && input.checked);
}

function schemaDialogFirstNonPrimaryRow(state, prefix) {
  var rows = schemaDialogRows(state, prefix);
  for (var i = 0; i < rows.length; i += 1) {
    if (!schemaDialogRowIsPrimaryKey(rows[i], prefix)) {
      return rows[i];
    }
  }
  return null;
}

function updateSchemaDialogMoveButtons(state, prefix) {
  if (!state || !state.columnList) {
    return;
  }
  var firstNonPrimary = schemaDialogFirstNonPrimaryRow(state, prefix);
  var rows = schemaDialogRows(state, prefix);
  var hasPrimaryKeys = rows.some(function (row) {
    return schemaDialogRowIsPrimaryKey(row, prefix);
  });
  var primaryKeyMoveTitle = "Primary key columns are always listed first";
  rows.forEach(function (row) {
    var isPrimaryKey = schemaDialogRowIsPrimaryKey(row, prefix);
    var previous = row.previousElementSibling;
    var next = row.nextElementSibling;
    row
      .querySelectorAll("." + prefix + "-move-controls button")
      .forEach(function (button) {
        button.title = button.dataset.defaultTitle || button.title;
        button.disabled = state.isSaving || isPrimaryKey;
        if (isPrimaryKey) {
          button.title = primaryKeyMoveTitle;
        }
      });
    if (!isPrimaryKey) {
      var topButton = row.querySelector("." + prefix + "-move-top");
      var upButton = row.querySelector("." + prefix + "-move-up");
      var downButton = row.querySelector("." + prefix + "-move-down");
      var bottomButton = row.querySelector("." + prefix + "-move-bottom");
      topButton.disabled =
        state.isSaving || !firstNonPrimary || row === firstNonPrimary;
      upButton.disabled =
        state.isSaving ||
        !previous ||
        schemaDialogRowIsPrimaryKey(previous, prefix);
      downButton.disabled = state.isSaving || !next;
      bottomButton.disabled = state.isSaving || !next;
      if (hasPrimaryKeys && row === firstNonPrimary) {
        topButton.title = primaryKeyMoveTitle;
        upButton.title = primaryKeyMoveTitle;
      }
    }
  });
}

function normalizeSchemaDialogPrimaryKeyRows(state, prefix) {
  var rows = schemaDialogRows(state, prefix);
  rows
    .filter(function (row) {
      return schemaDialogRowIsPrimaryKey(row, prefix);
    })
    .concat(
      rows.filter(function (row) {
        return !schemaDialogRowIsPrimaryKey(row, prefix);
      }),
    )
    .forEach(function (row) {
      state.columnList.appendChild(row);
    });
}

function tableCreateCustomColumnTypes() {
  var data = databaseCreateTableData() || {};
  return data.customColumnTypes || [];
}

function tableCreateCustomColumnType(name) {
  var options = tableCreateCustomColumnTypes();
  for (var i = 0; i < options.length; i += 1) {
    if (options[i].name === name) {
      return options[i];
    }
  }
  return null;
}

function tableCreateCustomTypeAppliesToSqliteType(option, sqliteType) {
  return (
    option &&
    option.sqliteTypes &&
    option.sqliteTypes.indexOf(sqliteType) !== -1
  );
}

function tableCreateDialogRows(state) {
  return schemaDialogRows(state, "table-create");
}

function tableCreateRowIsPrimaryKey(row) {
  return schemaDialogRowIsPrimaryKey(row, "table-create");
}

function tableCreateFirstNonPrimaryRow(state) {
  return schemaDialogFirstNonPrimaryRow(state, "table-create");
}

function updateTableCreateMoveButtons(state) {
  updateSchemaDialogMoveButtons(state, "table-create");
}

function schemaDialogTypeAffinity(type) {
  if (type === "float") {
    return "real";
  }
  return type;
}

function foreignKeyTypesCompatible(sourceAffinity, targetAffinity) {
  if (sourceAffinity === targetAffinity) {
    return true;
  }
  var numericAffinities = ["integer", "real", "numeric"];
  if (sourceAffinity === "numeric") {
    return numericAffinities.indexOf(targetAffinity) !== -1;
  }
  if (targetAffinity === "numeric") {
    return numericAffinities.indexOf(sourceAffinity) !== -1;
  }
  return false;
}

function foreignKeyTargetKey(target) {
  return target.fk_table + "\u001f" + target.fk_column;
}

function foreignKeyTargetLabel(target) {
  return (
    target.fk_table +
    "." +
    target.fk_column +
    " (" +
    sqliteColumnTypeLabel(target.type) +
    ")"
  );
}

function appendForeignKeyTargetOption(select, target) {
  var optionElement = document.createElement("option");
  optionElement.value = foreignKeyTargetKey(target);
  optionElement.dataset.fkTable = target.fk_table;
  optionElement.dataset.fkColumn = target.fk_column;
  optionElement.dataset.fkType = target.type;
  optionElement.textContent = foreignKeyTargetLabel(target);
  select.appendChild(optionElement);
  return optionElement;
}

function sqliteColumnTypeForForeignKeyTarget(type) {
  var affinity = schemaDialogTypeAffinity(type);
  if (affinity === "real" || affinity === "numeric") {
    return "float";
  }
  if (["text", "integer", "blob"].indexOf(affinity) !== -1) {
    return affinity;
  }
  return "";
}

function selectedSchemaDialogForeignKeyOption(foreignKeySelect) {
  return foreignKeySelect && foreignKeySelect.selectedOptions
    ? foreignKeySelect.selectedOptions[0]
    : null;
}

function setBlankSchemaDialogColumnNameFromForeignKey(
  row,
  prefix,
  foreignKeyOption,
) {
  var nameInput = row.querySelector("." + prefix + "-column-name");
  if (
    nameInput &&
    !nameInput.value.trim() &&
    foreignKeyOption &&
    foreignKeyOption.dataset.fkTable &&
    foreignKeyOption.dataset.fkColumn
  ) {
    nameInput.value =
      foreignKeyOption.dataset.fkTable +
      "_" +
      foreignKeyOption.dataset.fkColumn;
  }
}

function tableCreateForeignKeyTargetsUrl() {
  var data = databaseCreateTableData() || {};
  if (data.foreignKeyTargetsPath) {
    return data.foreignKeyTargetsPath;
  }
  if (!data.path) {
    return null;
  }
  return data.path.replace(/\/-\/create$/, "/-/foreign-key-targets");
}

function populateSchemaDialogForeignKeySelect(
  select,
  state,
  prefix,
  sourceType,
  options,
) {
  options = options || {};
  var previousKey = select.value || select.dataset.selectedKey || "";
  select.textContent = "";

  var blankOption = document.createElement("option");
  blankOption.value = "";
  blankOption.textContent = "- no foreign key -";
  select.appendChild(blankOption);

  if (state.foreignKeyTargetsLoading) {
    var loadingOption = document.createElement("option");
    loadingOption.value = "";
    loadingOption.disabled = true;
    loadingOption.textContent = "Loading foreign keys...";
    select.appendChild(loadingOption);
  } else if (state.foreignKeyTargetsError) {
    var errorOption = document.createElement("option");
    errorOption.value = "";
    errorOption.disabled = true;
    errorOption.textContent = "Could not load foreign keys";
    select.appendChild(errorOption);
  } else {
    var sourceAffinity = schemaDialogTypeAffinity(sourceType);
    (state.foreignKeyTargets || []).forEach(function (target) {
      if (
        options.filterByType !== false &&
        !foreignKeyTypesCompatible(sourceAffinity, target.type)
      ) {
        return;
      }
      appendForeignKeyTargetOption(select, target);
    });
  }

  select.value = previousKey;
  if (
    previousKey &&
    select.value !== previousKey &&
    select.dataset.currentFkTable &&
    select.dataset.currentFkColumn
  ) {
    appendForeignKeyTargetOption(select, {
      fk_table: select.dataset.currentFkTable,
      fk_column: select.dataset.currentFkColumn,
      type: select.dataset.currentFkType || sourceType,
    });
    select.value = previousKey;
  }
  if (select.value !== previousKey) {
    select.value = "";
  }
  select.dataset.selectedKey = select.value;
  select.disabled = state.isSaving || select.options.length <= 1;
  updateSelectPlaceholder(select, prefix + "-input-placeholder");
}

function syncSchemaDialogForeignKeyOptions(row, state, prefix, options) {
  var typeSelect = row.querySelector("." + prefix + "-column-type");
  var foreignKeySelect = row.querySelector(
    "." + prefix + "-foreign-key-target",
  );
  if (!typeSelect || !foreignKeySelect) {
    return;
  }
  populateSchemaDialogForeignKeySelect(
    foreignKeySelect,
    state,
    prefix,
    typeSelect.value,
    options,
  );
}

function syncSchemaDialogCustomTypeAndForeignKey(row, state, prefix) {
  var customTypeSelect = row.querySelector(
    "." + prefix + "-custom-column-type",
  );
  var foreignKeySelect = row.querySelector(
    "." + prefix + "-foreign-key-target",
  );
  if (!foreignKeySelect) {
    return;
  }

  var hasCustomType = customTypeSelect && !!customTypeSelect.value;
  var hasForeignKey = !!foreignKeySelect.value;

  if (customTypeSelect && hasForeignKey) {
    customTypeSelect.value = "";
    updateSelectPlaceholder(customTypeSelect, prefix + "-input-placeholder");
    hasCustomType = false;
  }

  if (hasCustomType) {
    foreignKeySelect.value = "";
    foreignKeySelect.dataset.selectedKey = "";
    updateSelectPlaceholder(foreignKeySelect, prefix + "-input-placeholder");
    hasForeignKey = false;
  }

  if (customTypeSelect) {
    customTypeSelect.disabled = state.isSaving;
  }
  foreignKeySelect.disabled =
    state.isSaving || foreignKeySelect.options.length <= 1;
}

function handleSchemaDialogForeignKeyChange(row, state, prefix, options) {
  options = options || {};
  var foreignKeySelect = row.querySelector(
    "." + prefix + "-foreign-key-target",
  );
  var typeSelect = row.querySelector("." + prefix + "-column-type");
  var customTypeSelect = row.querySelector(
    "." + prefix + "-custom-column-type",
  );
  if (!foreignKeySelect) {
    return;
  }
  foreignKeySelect.dataset.selectedKey = foreignKeySelect.value;
  updateSelectPlaceholder(foreignKeySelect, prefix + "-input-placeholder");

  var foreignKeyOption = selectedSchemaDialogForeignKeyOption(foreignKeySelect);
  setBlankSchemaDialogColumnNameFromForeignKey(row, prefix, foreignKeyOption);

  var columnTypes = options.columnTypes || [];
  var foreignKeyColumnType =
    foreignKeyOption && foreignKeyOption.dataset.fkType
      ? sqliteColumnTypeForForeignKeyTarget(foreignKeyOption.dataset.fkType)
      : "";
  if (
    options.matchType &&
    typeSelect &&
    foreignKeyColumnType &&
    columnTypes.indexOf(foreignKeyColumnType) !== -1 &&
    typeSelect.value !== foreignKeyColumnType
  ) {
    typeSelect.value = foreignKeyColumnType;
    syncSchemaDialogForeignKeyOptions(
      row,
      state,
      prefix,
      options.foreignKeyOptions,
    );
  }

  if (customTypeSelect && foreignKeySelect.value) {
    customTypeSelect.value = "";
    updateSelectPlaceholder(customTypeSelect, prefix + "-input-placeholder");
  }
  syncSchemaDialogCustomTypeAndForeignKey(row, state, prefix);
}

function refreshSchemaDialogForeignKeyControls(state, prefix, options) {
  schemaDialogRows(state, prefix).forEach(function (row) {
    syncSchemaDialogForeignKeyOptions(row, state, prefix, options);
    syncSchemaDialogCustomTypeAndForeignKey(row, state, prefix);
  });
}

async function loadSchemaDialogForeignKeyTargets(state, prefix, url, options) {
  if (!url || !window.fetch) {
    state.foreignKeyTargets = [];
    state.foreignKeyTargetsLoading = false;
    refreshSchemaDialogForeignKeyControls(state, prefix, options);
    return;
  }
  state.foreignKeyTargets = [];
  state.foreignKeyTargetsError = null;
  state.foreignKeyTargetsLoading = true;
  refreshSchemaDialogForeignKeyControls(state, prefix, options);
  try {
    var response = await fetch(url, {
      headers: {
        Accept: "application/json",
      },
    });
    var data = await response.json();
    if (!response.ok || data.ok === false) {
      throw rowMutationRequestError(response, data);
    }
    state.foreignKeyTargets = data.targets || [];
  } catch (error) {
    state.foreignKeyTargets = [];
    state.foreignKeyTargetsError = error;
  } finally {
    state.foreignKeyTargetsLoading = false;
    refreshSchemaDialogForeignKeyControls(state, prefix, options);
  }
}

function syncTableCreateForeignKeyOptions(row, state) {
  syncSchemaDialogForeignKeyOptions(row, state, "table-create", {
    filterByType: false,
  });
}

function syncTableCreateCustomTypeAndForeignKey(row, state) {
  syncSchemaDialogCustomTypeAndForeignKey(row, state, "table-create");
}

function refreshTableCreateForeignKeyControls(state) {
  tableCreateDialogRows(state).forEach(function (row) {
    syncTableCreateForeignKeyOptions(row, state);
    syncTableCreateCustomTypeAndForeignKey(row, state);
  });
}

function updateTableCreateColumnRules(state) {
  normalizeSchemaDialogPrimaryKeyRows(state, "table-create");
  tableCreateDialogRows(state).forEach(function (row) {
    syncTableCreateForeignKeyOptions(row, state);
    syncTableCreateCustomTypeAndForeignKey(row, state);
    syncSchemaDialogDefaultControls(row, "table-create");
  });
  updateTableCreateMoveButtons(state);
}

async function loadTableCreateForeignKeyTargets(state) {
  return loadSchemaDialogForeignKeyTargets(
    state,
    "table-create",
    tableCreateForeignKeyTargetsUrl(),
    { filterByType: false },
  );
}

function tableCreateDialogSignature(state) {
  if (!state || !state.form) {
    return "";
  }
  return JSON.stringify({
    table: state.tableName.value,
    columns: tableCreateDialogRows(state).map(function (row) {
      return {
        name: row.querySelector(".table-create-column-name").value,
        type: row.querySelector(".table-create-column-type").value,
        customType:
          (
            row.querySelector(".table-create-custom-column-type") || {
              value: "",
            }
          ).value || "",
        pk: row.querySelector(".table-create-primary-key-input").checked,
        notNull: row.querySelector(".table-create-not-null-input").checked,
        defaultValue: row.querySelector(".table-create-default").value,
        defaultExpr: row.querySelector(".table-create-default-expr").value,
        foreignKey:
          (
            row.querySelector(".table-create-foreign-key-target") || {
              value: "",
            }
          ).value || "",
      };
    }),
  });
}

function tableCreateDialogHasChanges(state) {
  return (
    !!state &&
    !state.isSaving &&
    tableCreateDialogSignature(state) !== state.initialSignature
  );
}

function clearTableCreateDialogError(state) {
  state.error.hidden = true;
  state.error.textContent = "";
  state.dialog.removeAttribute("aria-describedby");
}

function showTableCreateDialogError(state, message) {
  state.error.hidden = false;
  state.error.textContent = message;
  state.dialog.setAttribute("aria-describedby", "table-create-error");
  state.error.focus();
}

function setTableCreateDialogSaving(state, isSaving) {
  state.isSaving = isSaving;
  state.cancelButton.disabled = isSaving;
  state.saveButton.disabled = isSaving;
  state.addColumnButton.disabled = isSaving;
  state.saveButton.textContent = isSaving ? "Creating..." : "Create table";
  state.columnList
    .querySelectorAll("input, select, button")
    .forEach(function (control) {
      control.disabled = isSaving;
    });
  if (!isSaving) {
    updateTableCreateColumnRules(state);
  }
  updateTableCreateMoveButtons(state);
}

function tableCreateSelectTypeValue(select, type) {
  var options = tableCreateColumnTypes();
  populateSqliteColumnTypeSelect(select, type, options);
}

function updateTableCreateCustomColumnTypePlaceholder(select) {
  updateSelectPlaceholder(select, "table-create-input-placeholder");
}

function createTableCustomColumnTypeSelect() {
  var options = tableCreateCustomColumnTypes();
  return createCustomColumnTypeSelect(
    options,
    "table-create-input table-create-custom-column-type",
    "table-create-input-placeholder",
  );
}

function syncTableCreateCustomTypeForSqliteType(row) {
  var typeSelect = row.querySelector(".table-create-column-type");
  var customTypeSelect = row.querySelector(".table-create-custom-column-type");
  if (!typeSelect || !customTypeSelect || !customTypeSelect.value) {
    return;
  }
  var option = tableCreateCustomColumnType(customTypeSelect.value);
  if (!tableCreateCustomTypeAppliesToSqliteType(option, typeSelect.value)) {
    customTypeSelect.value = "";
    updateTableCreateCustomColumnTypePlaceholder(customTypeSelect);
  }
}

function createTableColumnRow(state, column) {
  var index = state.nextColumnIndex;
  state.nextColumnIndex += 1;

  var row = document.createElement("div");
  row.className = "table-create-column-row";

  var main = document.createElement("div");
  main.className = "table-create-column-main";

  var details = document.createElement("div");
  details.className = "table-create-column-details";
  details.id = "table-create-column-details-" + index;
  details.hidden = !(column && column.expanded);

  var expandButton = createSchemaDialogMoreOptionsButton(
    "table-create",
    details,
  );

  var nameId = "table-create-column-name-" + index;
  var nameLabel = document.createElement("label");
  nameLabel.className = "table-create-column-label";
  nameLabel.setAttribute("for", nameId);
  nameLabel.textContent = "Column";

  var nameInput = document.createElement("input");
  nameInput.id = nameId;
  nameInput.className = "table-create-input table-create-column-name";
  nameInput.type = "text";
  nameInput.required = true;
  nameInput.autocomplete = "off";
  nameInput.placeholder = "column name";
  nameInput.value = column && column.name ? column.name : "";

  var typeSelect = document.createElement("select");
  typeSelect.className = "table-create-input table-create-column-type";
  typeSelect.setAttribute("aria-label", "Column type");
  tableCreateSelectTypeValue(typeSelect, column && column.type);

  var customTypeSelect = null;
  var customTypeField = null;
  if (tableCreateCustomColumnTypes().length) {
    var customTypeId = "table-create-column-custom-type-" + index;
    customTypeField = document.createElement("div");
    customTypeField.className =
      "table-create-detail-field table-create-custom-type-field";
    var customTypeLabel = document.createElement("label");
    customTypeLabel.className = "table-create-detail-label";
    customTypeLabel.setAttribute("for", customTypeId);
    customTypeLabel.textContent = "Custom type";
    var customTypeHelpId = "table-create-column-custom-type-help-" + index;
    var customTypeHelp = document.createElement("p");
    customTypeHelp.id = customTypeHelpId;
    customTypeHelp.className = "table-create-detail-help";
    customTypeHelp.textContent =
      "Controls how Datasette displays and edits this column";
    customTypeSelect = createTableCustomColumnTypeSelect();
    customTypeSelect.id = customTypeId;
    customTypeSelect.setAttribute("aria-describedby", customTypeHelpId);
    customTypeSelect.value =
      column && column.customType ? column.customType : "";
    updateTableCreateCustomColumnTypePlaceholder(customTypeSelect);
    customTypeField.appendChild(customTypeLabel);
    customTypeField.appendChild(customTypeHelp);
    customTypeField.appendChild(customTypeSelect);
  }

  var pkLabel = document.createElement("label");
  pkLabel.className = "table-create-detail-check table-create-primary-key";
  var pkInput = document.createElement("input");
  pkInput.type = "checkbox";
  pkInput.className = "table-create-primary-key-input";
  pkInput.checked = !!(column && column.primaryKey);
  var pkText = document.createElement("span");
  var pkStrong = document.createElement("strong");
  pkStrong.textContent = "Primary key";
  pkText.appendChild(pkStrong);
  pkText.appendChild(
    document.createTextNode(" This ID uniquely identifies the record"),
  );
  pkLabel.appendChild(pkInput);
  pkLabel.appendChild(pkText);

  var foreignKeyId = "table-create-column-foreign-key-" + index;
  var foreignKeyHelpId = "table-create-column-foreign-key-help-" + index;
  var foreignKeyField = document.createElement("div");
  foreignKeyField.className =
    "table-create-detail-field table-create-foreign-key-field";
  var foreignKeyLabel = document.createElement("label");
  foreignKeyLabel.className = "table-create-detail-label";
  foreignKeyLabel.setAttribute("for", foreignKeyId);
  foreignKeyLabel.textContent = "Foreign key";
  var foreignKeyHelp = document.createElement("p");
  foreignKeyHelp.id = foreignKeyHelpId;
  foreignKeyHelp.className = "table-create-detail-help";
  foreignKeyHelp.textContent = "Link this column to another table.";
  var foreignKeySelect = document.createElement("select");
  foreignKeySelect.id = foreignKeyId;
  foreignKeySelect.className =
    "table-create-input table-create-foreign-key-target";
  foreignKeySelect.setAttribute("aria-label", "Foreign key target");
  foreignKeySelect.setAttribute("aria-describedby", foreignKeyHelpId);
  foreignKeyField.appendChild(foreignKeyLabel);
  foreignKeyField.appendChild(foreignKeyHelp);
  foreignKeyField.appendChild(foreignKeySelect);

  var notNullLabel = document.createElement("label");
  notNullLabel.className = "table-create-detail-check table-create-not-null";
  var notNullInput = document.createElement("input");
  notNullInput.type = "checkbox";
  notNullInput.className = "table-create-not-null-input";
  notNullInput.checked = !!(column && column.notNull);
  var notNullText = document.createElement("span");
  var notNullStrong = document.createElement("strong");
  notNullStrong.textContent = "Not null";
  notNullText.appendChild(notNullStrong);
  notNullText.appendChild(
    document.createTextNode(" This value cannot be left unset"),
  );
  notNullLabel.appendChild(notNullInput);
  notNullLabel.appendChild(notNullText);

  var defaultControls = createSchemaDialogDefaultControls(
    "table-create",
    index,
    tableCreateDefaultExpressions(),
    {
      defaultValue: column && column.defaultValue,
      defaultExpr: column && column.defaultExpr,
    },
  );

  var moveControls = createSchemaDialogMoveControls("table-create");

  var removeButton = createSchemaDialogIconButton(
    "table-create",
    "remove-column",
    "Remove column",
    "Remove column",
    COLUMN_MOVE_ICONS.remove,
  );

  main.appendChild(nameLabel);
  main.appendChild(nameInput);
  main.appendChild(typeSelect);
  main.appendChild(moveControls.controls);
  main.appendChild(removeButton);
  main.appendChild(expandButton);

  if (customTypeField) {
    details.appendChild(customTypeField);
  }
  details.appendChild(defaultControls.controls);
  details.appendChild(notNullLabel);
  details.appendChild(pkLabel);
  details.appendChild(foreignKeyField);
  row.appendChild(main);
  row.appendChild(details);

  removeButton.addEventListener("click", function () {
    if (state.isSaving) {
      return;
    }
    row.remove();
    clearTableCreateDialogError(state);
    updateTableCreateColumnRules(state);
    var nextInput = state.columnList.querySelector(".table-create-column-name");
    if (nextInput) {
      nextInput.focus();
    } else {
      state.addColumnButton.focus();
    }
  });

  nameInput.addEventListener("input", function () {
    clearTableCreateDialogError(state);
  });
  typeSelect.addEventListener("change", function () {
    clearTableCreateDialogError(state);
    syncTableCreateCustomTypeForSqliteType(row);
    syncTableCreateForeignKeyOptions(row, state);
    syncTableCreateCustomTypeAndForeignKey(row, state);
  });
  if (customTypeSelect) {
    customTypeSelect.addEventListener("change", function () {
      clearTableCreateDialogError(state);
      updateTableCreateCustomColumnTypePlaceholder(customTypeSelect);
      if (customTypeSelect.value) {
        foreignKeySelect.value = "";
        foreignKeySelect.dataset.selectedKey = "";
      }
      var option = tableCreateCustomColumnType(customTypeSelect.value);
      if (
        option &&
        option.fixedSqliteType &&
        tableCreateColumnTypes().indexOf(option.fixedSqliteType) !== -1
      ) {
        typeSelect.value = option.fixedSqliteType;
        syncTableCreateForeignKeyOptions(row, state);
      }
      syncTableCreateCustomTypeAndForeignKey(row, state);
    });
  }
  pkInput.addEventListener("change", function () {
    clearTableCreateDialogError(state);
    updateTableCreateColumnRules(state);
  });
  notNullInput.addEventListener("change", function () {
    clearTableCreateDialogError(state);
  });
  defaultControls.defaultInput.addEventListener("input", function () {
    if (defaultControls.defaultInput.value) {
      defaultControls.defaultExprSelect.value = "";
      syncSchemaDialogDefaultControls(row, "table-create");
    }
    clearTableCreateDialogError(state);
  });
  defaultControls.defaultExprSelect.addEventListener("change", function () {
    if (defaultControls.defaultExprSelect.value) {
      defaultControls.defaultInput.value = "";
    }
    if (
      applyDefaultExpressionColumnType(
        row,
        "table-create",
        tableCreateDefaultExpressions(),
        tableCreateColumnTypes(),
      )
    ) {
      syncTableCreateCustomTypeForSqliteType(row);
      syncTableCreateForeignKeyOptions(row, state);
      syncTableCreateCustomTypeAndForeignKey(row, state);
    }
    syncSchemaDialogDefaultControls(row, "table-create");
    clearTableCreateDialogError(state);
  });
  foreignKeySelect.addEventListener("change", function () {
    clearTableCreateDialogError(state);
    handleSchemaDialogForeignKeyChange(row, state, "table-create", {
      columnTypes: tableCreateColumnTypes(),
      foreignKeyOptions: { filterByType: false },
      matchType: true,
    });
  });

  expandButton.addEventListener("click", function () {
    toggleSchemaDialogMoreOptions(expandButton, details);
  });

  moveControls.topButton.addEventListener("click", function () {
    var first = tableCreateFirstNonPrimaryRow(state);
    if (
      state.isSaving ||
      tableCreateRowIsPrimaryKey(row) ||
      !first ||
      first === row
    ) {
      return;
    }
    state.columnList.insertBefore(row, first);
    clearTableCreateDialogError(state);
    updateTableCreateColumnRules(state);
    row.querySelector(".table-create-column-name").focus();
  });

  moveControls.upButton.addEventListener("click", function () {
    var previous = row.previousElementSibling;
    if (
      state.isSaving ||
      tableCreateRowIsPrimaryKey(row) ||
      !previous ||
      tableCreateRowIsPrimaryKey(previous)
    ) {
      return;
    }
    state.columnList.insertBefore(row, previous);
    clearTableCreateDialogError(state);
    updateTableCreateColumnRules(state);
    row.querySelector(".table-create-column-name").focus();
  });

  moveControls.downButton.addEventListener("click", function () {
    var next = row.nextElementSibling;
    if (state.isSaving || tableCreateRowIsPrimaryKey(row) || !next) {
      return;
    }
    state.columnList.insertBefore(next, row);
    clearTableCreateDialogError(state);
    updateTableCreateColumnRules(state);
    row.querySelector(".table-create-column-name").focus();
  });

  moveControls.bottomButton.addEventListener("click", function () {
    var last = state.columnList.lastElementChild;
    if (
      state.isSaving ||
      tableCreateRowIsPrimaryKey(row) ||
      !last ||
      last === row
    ) {
      return;
    }
    state.columnList.appendChild(row);
    clearTableCreateDialogError(state);
    updateTableCreateColumnRules(state);
    row.querySelector(".table-create-column-name").focus();
  });

  syncSchemaDialogDefaultControls(row, "table-create");
  return row;
}

function addTableCreateColumn(state, column) {
  var row = createTableColumnRow(state, column || { type: "text" });
  state.columnList.appendChild(row);
  updateTableCreateColumnRules(state);
  return row;
}

function resetTableCreateDialog(state) {
  state.nextColumnIndex = 0;
  state.tableName.value = "";
  state.columnList.textContent = "";
  addTableCreateColumn(state, {
    name: "id",
    type: "integer",
    primaryKey: true,
  });
  addTableCreateColumn(state, {
    name: "",
    type: "text",
    primaryKey: false,
  });
  updateTableCreateColumnRules(state);
  state.initialSignature = tableCreateDialogSignature(state);
}

function collectTableCreatePayload(state) {
  var payload = {
    table: state.tableName.value.trim(),
    columns: [],
  };
  var primaryKeys = [];
  tableCreateDialogRows(state).forEach(function (row) {
    var name = row.querySelector(".table-create-column-name").value.trim();
    var type = row.querySelector(".table-create-column-type").value;
    var column = { name: name, type: type };
    var foreignKeySelect = row.querySelector(
      ".table-create-foreign-key-target",
    );
    var foreignKeyOption =
      foreignKeySelect && foreignKeySelect.selectedOptions
        ? foreignKeySelect.selectedOptions[0]
        : null;
    if (
      foreignKeyOption &&
      foreignKeyOption.dataset.fkTable &&
      foreignKeyOption.dataset.fkColumn
    ) {
      column.fk_table = foreignKeyOption.dataset.fkTable;
      column.fk_column = foreignKeyOption.dataset.fkColumn;
    }
    if (row.querySelector(".table-create-not-null-input").checked) {
      column.not_null = true;
    }
    var defaultExpr = row.querySelector(".table-create-default-expr").value;
    var defaultValue = row.querySelector(".table-create-default").value;
    if (defaultExpr) {
      column.default_expr = defaultExpr;
    } else if (defaultValue) {
      column.default = defaultValue;
    }
    payload.columns.push(column);
    if (row.querySelector(".table-create-primary-key-input").checked) {
      primaryKeys.push(name);
    }
  });
  if (primaryKeys.length === 1) {
    payload.pk = primaryKeys[0];
  } else if (primaryKeys.length > 1) {
    payload.pks = primaryKeys;
  }
  return payload;
}

function collectTableCreateColumnTypeAssignments(state) {
  var assignments = [];
  tableCreateDialogRows(state).forEach(function (row) {
    var customTypeSelect = row.querySelector(
      ".table-create-custom-column-type",
    );
    if (!customTypeSelect || !customTypeSelect.value) {
      return;
    }
    assignments.push({
      column: row.querySelector(".table-create-column-name").value.trim(),
      columnType: customTypeSelect.value,
      sqliteType: row.querySelector(".table-create-column-type").value,
    });
  });
  return assignments;
}

function validateTableCreatePayload(payload) {
  if (!payload.table) {
    return "Table name is required.";
  }
  if (payload.table.indexOf("\n") !== -1) {
    return "Table name cannot contain newlines.";
  }
  if (/^sqlite_/i.test(payload.table)) {
    return "Table name cannot start with sqlite_.";
  }
  if (!payload.columns.length) {
    return "At least one column is required.";
  }
  var seen = {};
  var supportedTypes = tableCreateColumnTypes();
  for (var i = 0; i < payload.columns.length; i += 1) {
    var column = payload.columns[i];
    if (!column.name) {
      return "Column name is required.";
    }
    if (column.name.indexOf("\n") !== -1) {
      return "Column names cannot contain newlines.";
    }
    var columnKey = column.name.toLowerCase();
    if (seen[columnKey]) {
      return "Duplicate column name: " + column.name;
    }
    seen[columnKey] = true;
    if (supportedTypes.indexOf(column.type) === -1) {
      return "Unsupported column type: " + column.type;
    }
    if (column.default && column.default_expr) {
      return "Use either a default value or a default expression.";
    }
  }
  return null;
}

function validateTableCreateColumnTypeAssignments(assignments) {
  for (var i = 0; i < assignments.length; i += 1) {
    var assignment = assignments[i];
    var option = tableCreateCustomColumnType(assignment.columnType);
    if (!option) {
      return "Unknown custom column type: " + assignment.columnType;
    }
    if (
      !tableCreateCustomTypeAppliesToSqliteType(option, assignment.sqliteType)
    ) {
      return (
        "Custom type " +
        assignment.columnType +
        " cannot be used with SQLite type " +
        assignment.sqliteType +
        "."
      );
    }
  }
  return null;
}

function fallbackTableUrl(tableName) {
  var data = databaseCreateTableData() || {};
  if (!data.path) {
    return null;
  }
  return data.path.replace(/\/-\/create$/, "/" + encodeURIComponent(tableName));
}

function tableCreateSetColumnTypeUrl(responseData, payload) {
  var tableUrl =
    responseData.table_url ||
    fallbackTableUrl(responseData.table || payload.table);
  if (!tableUrl) {
    return null;
  }
  var url = new URL(tableUrl, location.href);
  url.hash = "";
  url.search = "";
  url.pathname = url.pathname.replace(/\/$/, "") + "/-/set-column-type";
  return url.toString();
}

async function assignTableCreateColumnTypes(
  responseData,
  payload,
  assignments,
) {
  if (!assignments.length) {
    return;
  }
  var url = tableCreateSetColumnTypeUrl(responseData, payload);
  if (!url) {
    throw new Error("Could not find the set column type URL.");
  }
  for (var i = 0; i < assignments.length; i += 1) {
    var assignment = assignments[i];
    var response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        column: assignment.column,
        column_type: {
          type: assignment.columnType,
        },
      }),
    });
    var data = null;
    try {
      data = await response.json();
    } catch (_error) {
      data = null;
    }
    if (!response.ok || (data && data.ok === false)) {
      var error = rowMutationRequestError(response, data);
      throw new Error(
        "Created table, but could not set custom type for " +
          assignment.column +
          ": " +
          error.message,
      );
    }
  }
}

async function saveTableCreateDialog(state) {
  if (state.isSaving) {
    return;
  }
  var data = databaseCreateTableData();
  if (!data || !data.path) {
    showTableCreateDialogError(state, "Could not find the create table URL.");
    return;
  }
  clearTableCreateDialogError(state);
  var payload = collectTableCreatePayload(state);
  var columnTypeAssignments = collectTableCreateColumnTypeAssignments(state);
  var validationError = validateTableCreatePayload(payload);
  if (validationError) {
    showTableCreateDialogError(state, validationError);
    return;
  }
  var columnTypeValidationError = validateTableCreateColumnTypeAssignments(
    columnTypeAssignments,
  );
  if (columnTypeValidationError) {
    showTableCreateDialogError(state, columnTypeValidationError);
    return;
  }
  setTableCreateDialogSaving(state, true);
  try {
    var response = await fetch(data.path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    var responseData = null;
    try {
      responseData = await response.json();
    } catch (_error) {
      responseData = null;
    }
    if (!response.ok || (responseData && responseData.ok === false)) {
      throw rowMutationRequestError(response, responseData);
    }
    await assignTableCreateColumnTypes(
      responseData,
      payload,
      columnTypeAssignments,
    );
    var tableUrl =
      responseData.table_url ||
      fallbackTableUrl(responseData.table || payload.table);
    state.shouldRestoreFocus = false;
    state.dialog.close();
    if (tableUrl) {
      location.href = tableUrl;
    } else {
      location.reload();
    }
  } catch (error) {
    setTableCreateDialogSaving(state, false);
    showTableCreateDialogError(
      state,
      error.message || "Could not create table",
    );
  }
}

function confirmDiscardTableCreateChanges(state) {
  if (!tableCreateDialogHasChanges(state)) {
    return true;
  }
  return window.confirm("Discard this new table?");
}

function closeTableCreateDialogIfConfirmed(state) {
  if (!state || state.isSaving) {
    return false;
  }
  if (!confirmDiscardTableCreateChanges(state)) {
    return false;
  }
  state.shouldRestoreFocus = true;
  state.dialog.close();
  return true;
}

function ensureTableCreateDialog(manager) {
  if (tableCreateDialogState) {
    return tableCreateDialogState;
  }
  if (!window.HTMLDialogElement) {
    return null;
  }

  var dialog = document.createElement("dialog");
  dialog.id = TABLE_CREATE_DIALOG_ID;
  dialog.className = "table-create-dialog";
  dialog.setAttribute("aria-labelledby", "table-create-title");
  dialog.innerHTML = `
    <div class="modal-header">
      <span class="modal-title" id="table-create-title">Create table</span>
    </div>
    <form class="table-create-form" method="post" novalidate>
      <p class="table-create-error" id="table-create-error" role="alert" tabindex="-1" hidden></p>
      <div class="table-create-fields">
        <div class="table-create-field">
          <label class="table-create-label" for="table-create-name">Table name</label>
          <input class="table-create-input table-create-table-name" id="table-create-name" type="text" name="table" required autocomplete="off">
        </div>
        <div class="table-create-columns">
          <div class="table-create-column-headings" aria-hidden="true">
            <span>Column</span>
            <span>Type</span>
            <span>Move</span>
            <span></span>
          </div>
          <div class="table-create-column-list"></div>
          <button type="button" class="table-create-add-column"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"></path><path d="M12 5v14"></path></svg><span>Add column</span></button>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-ghost table-create-cancel">Cancel</button>
        <button type="submit" class="btn btn-primary table-create-save">Create table</button>
      </div>
    </form>
  `;
  document.body.appendChild(dialog);

  tableCreateDialogState = {
    dialog: dialog,
    form: dialog.querySelector(".table-create-form"),
    title: dialog.querySelector(".modal-title"),
    error: dialog.querySelector(".table-create-error"),
    fields: dialog.querySelector(".table-create-fields"),
    tableName: dialog.querySelector(".table-create-table-name"),
    columnList: dialog.querySelector(".table-create-column-list"),
    addColumnButton: dialog.querySelector(".table-create-add-column"),
    cancelButton: dialog.querySelector(".table-create-cancel"),
    saveButton: dialog.querySelector(".table-create-save"),
    currentButton: null,
    shouldRestoreFocus: true,
    isSaving: false,
    initialSignature: "",
    nextColumnIndex: 0,
    foreignKeyTargets: [],
    foreignKeyTargetsError: null,
    foreignKeyTargetsLoading: false,
    manager: manager,
  };

  tableCreateDialogState.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    saveTableCreateDialog(tableCreateDialogState);
  });

  tableCreateDialogState.addColumnButton.addEventListener("click", function () {
    if (tableCreateDialogState.isSaving) {
      return;
    }
    var row = addTableCreateColumn(tableCreateDialogState, { type: "text" });
    clearTableCreateDialogError(tableCreateDialogState);
    row.querySelector(".table-create-column-name").focus();
  });

  tableCreateDialogState.cancelButton.addEventListener("click", function () {
    closeTableCreateDialogIfConfirmed(tableCreateDialogState);
  });

  tableCreateDialogState.tableName.addEventListener("input", function () {
    clearTableCreateDialogError(tableCreateDialogState);
  });

  dialog.addEventListener("click", function (ev) {
    if (ev.target === dialog) {
      closeTableCreateDialogIfConfirmed(tableCreateDialogState);
    }
  });

  dialog.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") {
      return;
    }
    ev.preventDefault();
    closeTableCreateDialogIfConfirmed(tableCreateDialogState);
  });

  dialog.addEventListener("cancel", function (ev) {
    ev.preventDefault();
    closeTableCreateDialogIfConfirmed(tableCreateDialogState);
  });

  dialog.addEventListener("close", function () {
    var state = tableCreateDialogState;
    clearTableCreateDialogError(state);
    setTableCreateDialogSaving(state, false);
    if (
      state.shouldRestoreFocus &&
      state.currentButton &&
      document.contains(state.currentButton)
    ) {
      state.currentButton.focus();
    }
  });

  return tableCreateDialogState;
}

function openTableCreateDialog(button, manager) {
  var data = databaseCreateTableData();
  if (!data) {
    return;
  }
  var state = ensureTableCreateDialog(manager);
  if (!state) {
    return;
  }

  var menu = button.closest("details");
  if (menu) {
    menu.open = false;
  }
  state.manager = manager;
  state.currentButton = button;
  state.shouldRestoreFocus = true;
  state.title.textContent = "Create a table in " + data.databaseName;
  clearTableCreateDialogError(state);
  resetTableCreateDialog(state);
  loadTableCreateForeignKeyTargets(state);
  if (!state.dialog.open) {
    state.dialog.showModal();
  }
  state.tableName.focus();
}

function initTableCreateActions(manager) {
  if (
    !window.fetch ||
    !window.HTMLDialogElement ||
    !databaseCreateTableData()
  ) {
    return;
  }
  document.addEventListener("click", function (ev) {
    var button = ev.target.closest(
      'button[data-database-action="create-table"]',
    );
    if (!button) {
      return;
    }
    ev.preventDefault();
    openTableCreateDialog(button, manager);
  });
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

function tableBaseUrl() {
  var tableUrl =
    window._datasetteTableData && window._datasetteTableData.tableUrl;
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

function tableAlterData() {
  return tablePageData().alterTable;
}

function tableAlterColumnTypes() {
  var data = tableAlterData() || {};
  return data.columnTypes && data.columnTypes.length
    ? data.columnTypes
    : ["text", "integer", "float", "blob"];
}

function tableAlterDefaultExpressions() {
  var data = tableAlterData() || {};
  return data.defaultExpressions || [];
}

function tableAlterForeignKeyTargetsUrl() {
  var data = tableAlterData() || {};
  return data.foreignKeyTargetsPath || null;
}

function tableAlterCustomColumnTypes() {
  var data = tableAlterData() || {};
  return data.customColumnTypes || [];
}

function tableAlterCustomColumnType(name) {
  var options = tableAlterCustomColumnTypes();
  for (var i = 0; i < options.length; i += 1) {
    if (options[i].name === name) {
      return options[i];
    }
  }
  return null;
}

function tableAlterCustomTypeAppliesToSqliteType(option, sqliteType) {
  return (
    option &&
    option.sqliteTypes &&
    option.sqliteTypes.indexOf(sqliteType) !== -1
  );
}

function tableAlterDialogRows(state) {
  return schemaDialogRows(state, "table-alter");
}

function syncTableAlterForeignKeyOptions(row, state) {
  syncSchemaDialogForeignKeyOptions(row, state, "table-alter", {
    filterByType: false,
  });
}

function tableAlterRowSignature(row) {
  var foreignKeySelect = row.querySelector(".table-alter-foreign-key-target");
  var foreignKeyValue = foreignKeySelect
    ? foreignKeySelect.value || foreignKeySelect.dataset.selectedKey || ""
    : "";
  var foreignKeyOption =
    foreignKeySelect && foreignKeySelect.selectedOptions
      ? foreignKeySelect.selectedOptions[0]
      : null;
  return {
    existing: row.dataset.existing === "1",
    originalName: row.dataset.originalName || "",
    name: row.querySelector(".table-alter-column-name").value,
    type: row.querySelector(".table-alter-column-type").value,
    customType:
      (
        row.querySelector(".table-alter-custom-column-type") || {
          value: "",
        }
      ).value || "",
    notNull: row.querySelector(".table-alter-not-null-input").checked,
    defaultValue: row.querySelector(".table-alter-default").value,
    defaultExpr: row.querySelector(".table-alter-default-expr").value,
    pk: row.querySelector(".table-alter-primary-key-input").checked,
    foreignKey: foreignKeyValue,
    foreignKeyTable:
      foreignKeyOption && foreignKeyOption.dataset.fkTable
        ? foreignKeyOption.dataset.fkTable
        : "",
    foreignKeyColumn:
      foreignKeyOption && foreignKeyOption.dataset.fkColumn
        ? foreignKeyOption.dataset.fkColumn
        : "",
  };
}

function tableAlterDialogSignature(state) {
  if (!state || !state.form) {
    return "";
  }
  return JSON.stringify({
    tableName: state.tableNameInput ? state.tableNameInput.value.trim() : "",
    columns: tableAlterDialogRows(state).map(tableAlterRowSignature),
    deletedColumns: state.deletedColumns.slice(),
  });
}

function tableAlterDialogHasChanges(state) {
  return (
    !!state &&
    !state.isSaving &&
    tableAlterDialogSignature(state) !== state.initialSignature
  );
}

function updateTableAlterSaveButtonState(state) {
  if (!state || !state.saveButton) {
    return;
  }
  state.saveButton.disabled =
    state.isSaving ||
    (state.mode !== "review" &&
      tableAlterDialogSignature(state) === state.initialSignature);
}

function tableAlterRowIsPrimaryKey(row) {
  return schemaDialogRowIsPrimaryKey(row, "table-alter");
}

function tableAlterFirstNonPrimaryRow(state) {
  return schemaDialogFirstNonPrimaryRow(state, "table-alter");
}

function updateTableAlterMoveButtons(state) {
  updateSchemaDialogMoveButtons(state, "table-alter");
}

function normalizeTableAlterPrimaryKeyRows(state) {
  normalizeSchemaDialogPrimaryKeyRows(state, "table-alter");
}

function clearTableAlterDialogError(state) {
  state.error.hidden = true;
  state.error.textContent = "";
  state.dialog.removeAttribute("aria-describedby");
}

function showTableAlterDialogError(state, message) {
  state.error.hidden = false;
  state.error.textContent = message;
  state.dialog.setAttribute("aria-describedby", "table-alter-error");
  state.error.focus();
}

function setTableAlterDialogSaving(state, isSaving) {
  state.isSaving = isSaving;
  state.cancelButton.disabled = isSaving;
  state.addColumnButton.disabled = isSaving;
  state.backButton.disabled = isSaving;
  state.dropButton.disabled = isSaving;
  state.saveButton.textContent = isSaving
    ? state.mode === "review"
      ? "Applying..."
      : "Preparing..."
    : tableAlterSaveButtonText(state);
  state.fields
    .querySelectorAll("input, select, button")
    .forEach(function (control) {
      control.disabled = isSaving;
    });
  if (!isSaving) {
    state.columnList
      .querySelectorAll(".table-alter-default-expr")
      .forEach(function (select) {
        syncSchemaDialogDefaultControls(
          select.closest(".table-alter-column-row"),
          "table-alter",
        );
      });
    refreshSchemaDialogForeignKeyControls(state, "table-alter");
  }
  updateTableAlterMoveButtons(state);
  updateTableAlterSaveButtonState(state);
}

function tableAlterSaveButtonText(state) {
  return state && state.mode === "review" ? "Apply changes" : "Review changes";
}

function tableAlterSelectTypeValue(select, type) {
  var options = tableAlterColumnTypes();
  populateSqliteColumnTypeSelect(select, type, options);
}

function updateTableAlterCustomColumnTypePlaceholder(select) {
  updateSelectPlaceholder(select, "table-alter-input-placeholder");
}

function createTableAlterCustomColumnTypeSelect() {
  var options = tableAlterCustomColumnTypes();
  return createCustomColumnTypeSelect(
    options,
    "table-alter-input table-alter-custom-column-type",
    "table-alter-input-placeholder",
  );
}

function syncTableAlterCustomTypeForSqliteType(row) {
  var typeSelect = row.querySelector(".table-alter-column-type");
  var customTypeSelect = row.querySelector(".table-alter-custom-column-type");
  if (!typeSelect || !customTypeSelect || !customTypeSelect.value) {
    return;
  }
  var option = tableAlterCustomColumnType(customTypeSelect.value);
  if (!tableAlterCustomTypeAppliesToSqliteType(option, typeSelect.value)) {
    customTypeSelect.value = "";
    updateTableAlterCustomColumnTypePlaceholder(customTypeSelect);
  }
}

function createTableAlterColumnRow(state, column) {
  var index = state.nextColumnIndex;
  state.nextColumnIndex += 1;
  var existing = !!(column && column.existing);
  var originalName = existing ? column.name || "" : "";
  var originalCustomType =
    existing && column.column_type ? column.column_type.type || "" : "";
  var originalDefaultExpr =
    existing && column.has_default && column.default_expr
      ? column.default_expr
      : "";
  var originalDefault =
    existing &&
    column.has_default &&
    !originalDefaultExpr &&
    column.default !== null
      ? String(column.default)
      : "";
  var originalForeignKey =
    existing && column.foreign_key
      ? foreignKeyTargetKey(column.foreign_key)
      : "";

  var row = document.createElement("div");
  row.className = "table-alter-column-row";
  row.dataset.existing = existing ? "1" : "0";
  row.dataset.originalName = originalName;
  row.dataset.originalType = existing ? column.type || "text" : "";
  row.dataset.originalNotNull = existing && column.notnull ? "1" : "0";
  row.dataset.originalHasDefault = existing && column.has_default ? "1" : "0";
  row.dataset.originalDefault = originalDefault;
  row.dataset.originalDefaultExpr = originalDefaultExpr;
  row.dataset.originalPk = existing && column.is_pk ? "1" : "0";
  row.dataset.originalCustomType = originalCustomType;
  row.dataset.originalForeignKey = originalForeignKey;

  var main = document.createElement("div");
  main.className = "table-alter-column-main";

  var details = document.createElement("div");
  details.className = "table-alter-column-details";
  details.id = "table-alter-column-details-" + index;
  details.hidden = !(column && column.expanded);

  var expandButton = createSchemaDialogMoreOptionsButton(
    "table-alter",
    details,
  );

  var nameId = "table-alter-column-name-" + index;
  var nameLabel = document.createElement("label");
  nameLabel.className = "table-alter-column-label";
  nameLabel.setAttribute("for", nameId);
  nameLabel.textContent = "Column";

  var nameInput = document.createElement("input");
  nameInput.id = nameId;
  nameInput.className = "table-alter-input table-alter-column-name";
  nameInput.type = "text";
  nameInput.required = true;
  nameInput.autocomplete = "off";
  nameInput.placeholder = "column name";
  nameInput.value = column && column.name ? column.name : "";

  var typeSelect = document.createElement("select");
  typeSelect.className = "table-alter-input table-alter-column-type";
  typeSelect.setAttribute("aria-label", "Column type");
  tableAlterSelectTypeValue(typeSelect, column && column.type);

  var customTypeSelect = null;
  var customTypeField = null;
  if (tableAlterCustomColumnTypes().length) {
    var customTypeId = "table-alter-column-custom-type-" + index;
    customTypeField = document.createElement("div");
    customTypeField.className =
      "table-alter-detail-field table-alter-custom-type-field";
    var customTypeLabel = document.createElement("label");
    customTypeLabel.className = "table-alter-detail-label";
    customTypeLabel.setAttribute("for", customTypeId);
    customTypeLabel.textContent = "Custom type";
    var customTypeHelpId = "table-alter-column-custom-type-help-" + index;
    var customTypeHelp = document.createElement("p");
    customTypeHelp.id = customTypeHelpId;
    customTypeHelp.className = "table-alter-detail-help";
    customTypeHelp.textContent =
      "Controls how Datasette displays and edits this column";
    customTypeSelect = createTableAlterCustomColumnTypeSelect();
    customTypeSelect.id = customTypeId;
    customTypeSelect.setAttribute("aria-describedby", customTypeHelpId);
    customTypeSelect.value = originalCustomType;
    updateTableAlterCustomColumnTypePlaceholder(customTypeSelect);
    customTypeField.appendChild(customTypeLabel);
    customTypeField.appendChild(customTypeHelp);
    customTypeField.appendChild(customTypeSelect);
  }

  var notNullLabel = document.createElement("label");
  notNullLabel.className = "table-alter-detail-check table-alter-not-null";
  var notNullInput = document.createElement("input");
  notNullInput.type = "checkbox";
  notNullInput.className = "table-alter-not-null-input";
  notNullInput.checked = !!(column && column.notnull);
  var notNullText = document.createElement("span");
  var notNullStrong = document.createElement("strong");
  notNullStrong.textContent = "Not null";
  notNullText.appendChild(notNullStrong);
  notNullText.appendChild(
    document.createTextNode(" This value cannot be left unset"),
  );
  notNullLabel.appendChild(notNullInput);
  notNullLabel.appendChild(notNullText);

  var defaultControls = createSchemaDialogDefaultControls(
    "table-alter",
    index,
    tableAlterDefaultExpressions(),
    {
      defaultValue: originalDefault,
      defaultExpr: originalDefaultExpr,
    },
  );
  var defaultInput = defaultControls.defaultInput;
  var defaultExprSelect = defaultControls.defaultExprSelect;

  var foreignKeyId = "table-alter-column-foreign-key-" + index;
  var foreignKeyHelpId = "table-alter-column-foreign-key-help-" + index;
  var foreignKeyField = document.createElement("div");
  foreignKeyField.className =
    "table-alter-detail-field table-alter-foreign-key-field";
  var foreignKeyLabel = document.createElement("label");
  foreignKeyLabel.className = "table-alter-detail-label";
  foreignKeyLabel.setAttribute("for", foreignKeyId);
  foreignKeyLabel.textContent = "Foreign key";
  var foreignKeyHelp = document.createElement("p");
  foreignKeyHelp.id = foreignKeyHelpId;
  foreignKeyHelp.className = "table-alter-detail-help";
  foreignKeyHelp.textContent = "Link this column to another table.";
  var foreignKeySelect = document.createElement("select");
  foreignKeySelect.id = foreignKeyId;
  foreignKeySelect.className =
    "table-alter-input table-alter-foreign-key-target";
  foreignKeySelect.setAttribute("aria-label", "Foreign key target");
  foreignKeySelect.setAttribute("aria-describedby", foreignKeyHelpId);
  foreignKeySelect.dataset.selectedKey = originalForeignKey;
  if (column && column.foreign_key) {
    foreignKeySelect.dataset.currentFkTable = column.foreign_key.fk_table;
    foreignKeySelect.dataset.currentFkColumn = column.foreign_key.fk_column;
    appendForeignKeyTargetOption(foreignKeySelect, {
      fk_table: column.foreign_key.fk_table,
      fk_column: column.foreign_key.fk_column,
      type: column.type || "text",
    });
    foreignKeySelect.value = originalForeignKey;
  }
  foreignKeyField.appendChild(foreignKeyLabel);
  foreignKeyField.appendChild(foreignKeyHelp);
  foreignKeyField.appendChild(foreignKeySelect);

  var pkLabel = document.createElement("label");
  pkLabel.className = "table-alter-detail-check table-alter-primary-key";
  var pkInput = document.createElement("input");
  pkInput.type = "checkbox";
  pkInput.className = "table-alter-primary-key-input";
  pkInput.checked = !!(column && column.is_pk);
  var pkText = document.createElement("span");
  var pkStrong = document.createElement("strong");
  pkStrong.textContent = "Primary key";
  pkText.appendChild(pkStrong);
  pkText.appendChild(
    document.createTextNode(" This ID uniquely identifies the record"),
  );
  pkLabel.appendChild(pkInput);
  pkLabel.appendChild(pkText);

  var moveControls = createSchemaDialogMoveControls("table-alter");

  var removeButton = createSchemaDialogIconButton(
    "table-alter",
    "remove-column",
    existing ? "Drop column" : "Remove column",
    existing ? "Drop column" : "Remove column",
    COLUMN_MOVE_ICONS.remove,
  );
  main.appendChild(nameLabel);
  main.appendChild(nameInput);
  main.appendChild(typeSelect);
  main.appendChild(moveControls.controls);
  main.appendChild(removeButton);
  main.appendChild(expandButton);

  if (customTypeField) {
    details.appendChild(customTypeField);
  }
  details.appendChild(defaultControls.controls);
  details.appendChild(notNullLabel);
  details.appendChild(pkLabel);
  details.appendChild(foreignKeyField);
  row.appendChild(main);
  row.appendChild(details);

  var controls = [
    nameInput,
    typeSelect,
    notNullInput,
    defaultInput,
    defaultExprSelect,
    pkInput,
    foreignKeySelect,
  ];
  if (customTypeSelect) {
    controls.push(customTypeSelect);
  }
  controls.forEach(function (control) {
    control.addEventListener("input", function () {
      clearTableAlterDialogError(state);
      updateTableAlterSaveButtonState(state);
    });
    control.addEventListener("change", function () {
      clearTableAlterDialogError(state);
      updateTableAlterSaveButtonState(state);
    });
  });

  defaultInput.addEventListener("input", function () {
    if (defaultInput.value) {
      defaultExprSelect.value = "";
      syncSchemaDialogDefaultControls(row, "table-alter");
    }
    updateTableAlterSaveButtonState(state);
  });
  defaultExprSelect.addEventListener("change", function () {
    if (defaultExprSelect.value) {
      defaultInput.value = "";
    }
    if (
      applyDefaultExpressionColumnType(
        row,
        "table-alter",
        tableAlterDefaultExpressions(),
        tableAlterColumnTypes(),
      )
    ) {
      syncTableAlterCustomTypeForSqliteType(row);
      syncTableAlterForeignKeyOptions(row, state);
      syncSchemaDialogCustomTypeAndForeignKey(row, state, "table-alter");
    }
    syncSchemaDialogDefaultControls(row, "table-alter");
    updateTableAlterSaveButtonState(state);
  });
  pkInput.addEventListener("change", function () {
    normalizeTableAlterPrimaryKeyRows(state);
    updateTableAlterMoveButtons(state);
    updateTableAlterSaveButtonState(state);
  });

  expandButton.addEventListener("click", function () {
    toggleSchemaDialogMoreOptions(expandButton, details);
  });

  typeSelect.addEventListener("change", function () {
    syncTableAlterCustomTypeForSqliteType(row);
    syncTableAlterForeignKeyOptions(row, state);
    syncSchemaDialogCustomTypeAndForeignKey(row, state, "table-alter");
    updateTableAlterSaveButtonState(state);
  });
  if (customTypeSelect) {
    customTypeSelect.addEventListener("change", function () {
      updateTableAlterCustomColumnTypePlaceholder(customTypeSelect);
      var option = tableAlterCustomColumnType(customTypeSelect.value);
      if (
        option &&
        option.fixedSqliteType &&
        tableAlterColumnTypes().indexOf(option.fixedSqliteType) !== -1
      ) {
        typeSelect.value = option.fixedSqliteType;
        syncTableAlterForeignKeyOptions(row, state);
      }
      syncSchemaDialogCustomTypeAndForeignKey(row, state, "table-alter");
      updateTableAlterSaveButtonState(state);
    });
  }
  foreignKeySelect.addEventListener("change", function () {
    handleSchemaDialogForeignKeyChange(row, state, "table-alter", {
      columnTypes: tableAlterColumnTypes(),
      foreignKeyOptions: { filterByType: false },
      matchType: true,
    });
    updateTableAlterSaveButtonState(state);
  });

  moveControls.topButton.addEventListener("click", function () {
    var first = tableAlterFirstNonPrimaryRow(state);
    if (
      state.isSaving ||
      tableAlterRowIsPrimaryKey(row) ||
      !first ||
      first === row
    ) {
      return;
    }
    state.columnList.insertBefore(row, first);
    clearTableAlterDialogError(state);
    updateTableAlterMoveButtons(state);
    updateTableAlterSaveButtonState(state);
    row.querySelector(".table-alter-column-name").focus();
  });

  moveControls.upButton.addEventListener("click", function () {
    var previous = row.previousElementSibling;
    if (
      state.isSaving ||
      tableAlterRowIsPrimaryKey(row) ||
      !previous ||
      tableAlterRowIsPrimaryKey(previous)
    ) {
      return;
    }
    state.columnList.insertBefore(row, previous);
    clearTableAlterDialogError(state);
    updateTableAlterMoveButtons(state);
    updateTableAlterSaveButtonState(state);
    row.querySelector(".table-alter-column-name").focus();
  });

  moveControls.downButton.addEventListener("click", function () {
    var next = row.nextElementSibling;
    if (state.isSaving || tableAlterRowIsPrimaryKey(row) || !next) {
      return;
    }
    state.columnList.insertBefore(next, row);
    clearTableAlterDialogError(state);
    updateTableAlterMoveButtons(state);
    updateTableAlterSaveButtonState(state);
    row.querySelector(".table-alter-column-name").focus();
  });

  moveControls.bottomButton.addEventListener("click", function () {
    var last = state.columnList.lastElementChild;
    if (
      state.isSaving ||
      tableAlterRowIsPrimaryKey(row) ||
      !last ||
      last === row
    ) {
      return;
    }
    state.columnList.appendChild(row);
    clearTableAlterDialogError(state);
    updateTableAlterMoveButtons(state);
    updateTableAlterSaveButtonState(state);
    row.querySelector(".table-alter-column-name").focus();
  });

  removeButton.addEventListener("click", function () {
    if (state.isSaving) {
      return;
    }
    if (row.dataset.existing === "1") {
      state.deletedColumns.push(row.dataset.originalName);
    }
    row.remove();
    clearTableAlterDialogError(state);
    updateTableAlterMoveButtons(state);
    updateTableAlterSaveButtonState(state);
    var nextInput = state.columnList.querySelector(".table-alter-column-name");
    if (nextInput) {
      nextInput.focus();
    } else {
      state.addColumnButton.focus();
    }
  });

  syncSchemaDialogDefaultControls(row, "table-alter");
  syncTableAlterForeignKeyOptions(row, state);
  syncSchemaDialogCustomTypeAndForeignKey(row, state, "table-alter");
  return row;
}

function addTableAlterColumn(state, column) {
  var row = createTableAlterColumnRow(state, column || { type: "text" });
  state.columnList.appendChild(row);
  return row;
}

function resetTableAlterDialog(state, data) {
  state.nextColumnIndex = 0;
  state.deletedColumns = [];
  state.originalTableName = data.tableName || "";
  state.tableNameInput.value = state.originalTableName;
  state.tableOptions.open = false;
  state.originalPrimaryKeys = (data.primaryKeys || []).slice();
  state.originalColumnNames = (data.columns || []).map(function (column) {
    return column.name;
  });
  state.columnList.textContent = "";
  (data.columns || []).forEach(function (column) {
    addTableAlterColumn(
      state,
      Object.assign({}, column, {
        existing: true,
      }),
    );
  });
  normalizeTableAlterPrimaryKeyRows(state);
  state.initialSignature = tableAlterDialogSignature(state);
  showTableAlterEditor(state);
}

function collectTableAlterRows(state) {
  return tableAlterDialogRows(state).map(function (row) {
    var signature = tableAlterRowSignature(row);
    signature.originalType = row.dataset.originalType || "";
    signature.originalNotNull = row.dataset.originalNotNull === "1";
    signature.originalHasDefault = row.dataset.originalHasDefault === "1";
    signature.originalDefault = row.dataset.originalDefault || "";
    signature.originalDefaultExpr = row.dataset.originalDefaultExpr || "";
    signature.originalPk = row.dataset.originalPk === "1";
    signature.originalCustomType = row.dataset.originalCustomType || "";
    signature.originalForeignKey = row.dataset.originalForeignKey || "";
    return signature;
  });
}

function validateTableAlterRows(state, rows) {
  var tableName = state.tableNameInput.value.trim();
  if (!tableName) {
    return "Table name is required.";
  }
  if (tableName.indexOf("\n") !== -1) {
    return "Table names cannot contain newlines.";
  }
  if (/^sqlite_/.test(tableName)) {
    return "Table names cannot start with sqlite_.";
  }
  if (!rows.length) {
    return "At least one column is required.";
  }
  var seen = {};
  var supportedTypes = tableAlterColumnTypes();
  for (var i = 0; i < rows.length; i += 1) {
    var row = rows[i];
    var name = row.name.trim();
    if (!name) {
      return "Column name is required.";
    }
    if (name.indexOf("\n") !== -1) {
      return "Column names cannot contain newlines.";
    }
    var columnKey = name.toLowerCase();
    if (seen[columnKey]) {
      return "Duplicate column name: " + name;
    }
    seen[columnKey] = true;
    if (supportedTypes.indexOf(row.type) === -1) {
      return "Unsupported column type: " + row.type;
    }
    if (row.customType) {
      var option = tableAlterCustomColumnType(row.customType);
      if (!option) {
        return "Unknown custom column type: " + row.customType;
      }
      if (!tableAlterCustomTypeAppliesToSqliteType(option, row.type)) {
        return (
          "Custom type " +
          row.customType +
          " cannot be used with SQLite type " +
          row.type +
          "."
        );
      }
    }
    if (row.defaultValue && row.defaultExpr) {
      return "Use either a default value or a default expression.";
    }
    if (!row.existing && row.notNull && !row.defaultValue && !row.defaultExpr) {
      return "New NOT NULL columns need a default or default expression.";
    }
  }
  var pkColumns = rows.filter(function (row) {
    return row.pk;
  });
  if (state.originalPrimaryKeys.length && !pkColumns.length) {
    return "At least one primary key column is required.";
  }
  return null;
}

function collectTableAlterColumnTypeAssignments(rows) {
  var assignments = [];
  if (!tableAlterCustomColumnTypes().length) {
    return assignments;
  }
  rows.forEach(function (row) {
    var renamed = row.existing && row.name.trim() !== row.originalName;
    if (row.customType === row.originalCustomType && !renamed) {
      return;
    }
    if (!row.customType && !row.originalCustomType) {
      return;
    }
    assignments.push({
      column: row.name.trim(),
      columnType: row.customType || null,
      sqliteType: row.type,
    });
  });
  return assignments;
}

function tableAlterPkIdentityColumns(rows) {
  return rows
    .filter(function (row) {
      return row.pk;
    })
    .map(function (row) {
      return row.existing ? row.originalName : row.name.trim();
    });
}

function tableAlterPkChanged(state, rows) {
  return (
    JSON.stringify(tableAlterPkIdentityColumns(rows)) !==
    JSON.stringify(state.originalPrimaryKeys)
  );
}

function tableAlterNaturalColumnOrder(state, rows) {
  var existingRowsByOriginalName = {};
  var newRows = [];
  rows.forEach(function (row) {
    if (row.existing) {
      existingRowsByOriginalName[row.originalName] = row;
    } else {
      newRows.push(row);
    }
  });
  var naturalOrder = [];
  state.originalColumnNames.forEach(function (originalName) {
    var row = existingRowsByOriginalName[originalName];
    if (row) {
      naturalOrder.push(row.name.trim());
    }
  });
  newRows.forEach(function (row) {
    naturalOrder.push(row.name.trim());
  });
  return naturalOrder;
}

function tableAlterColumnsReordered(state, rows) {
  var finalOrder = rows.map(function (row) {
    return row.name.trim();
  });
  return (
    JSON.stringify(finalOrder) !==
    JSON.stringify(tableAlterNaturalColumnOrder(state, rows))
  );
}

function tableAlterForeignKeyIdentity(row) {
  return [
    row.name.trim(),
    row.foreignKeyTable || "",
    row.foreignKeyColumn || "",
  ].join("\u001f");
}

function tableAlterOriginalForeignKeyIdentity(row) {
  return [row.originalName || "", row.originalForeignKey].join("\u001f");
}

function tableAlterForeignKeyRows(rows) {
  return rows
    .filter(function (row) {
      return row.foreignKey && row.foreignKeyTable && row.foreignKeyColumn;
    })
    .map(function (row) {
      return {
        column: row.name.trim(),
        fk_table: row.foreignKeyTable,
        fk_column: row.foreignKeyColumn,
      };
    });
}

function tableAlterForeignKeysChanged(rows) {
  var original = rows
    .filter(function (row) {
      return row.existing && row.originalForeignKey;
    })
    .map(tableAlterOriginalForeignKeyIdentity);
  var final = rows
    .filter(function (row) {
      return row.foreignKey && row.foreignKeyTable && row.foreignKeyColumn;
    })
    .map(tableAlterForeignKeyIdentity);
  return JSON.stringify(original) !== JSON.stringify(final);
}

function collectTableAlterPayload(state) {
  var rows = collectTableAlterRows(state);
  var validationError = validateTableAlterRows(state, rows);
  if (validationError) {
    return { error: validationError };
  }

  var operations = [];
  var tableName = state.tableNameInput.value.trim();
  if (tableName !== state.originalTableName) {
    operations.push({
      op: "rename_table",
      args: { to: tableName },
    });
  }
  var columnTypeAssignments = collectTableAlterColumnTypeAssignments(rows);
  rows.forEach(function (row) {
    var name = row.name.trim();
    if (!row.existing) {
      var addArgs = {
        name: name,
        type: row.type,
        not_null: row.notNull,
      };
      if (row.defaultExpr) {
        addArgs.default_expr = row.defaultExpr;
      } else if (row.defaultValue || row.notNull) {
        addArgs.default = row.defaultValue;
      }
      operations.push({ op: "add_column", args: addArgs });
      return;
    }

    var originalName = row.originalName;
    if (name !== originalName) {
      operations.push({
        op: "rename_column",
        args: { name: originalName, to: name },
      });
    }

    var alterArgs = { name: originalName };
    if (row.type !== row.originalType) {
      alterArgs.type = row.type;
    }
    if (row.notNull !== row.originalNotNull) {
      alterArgs.not_null = row.notNull;
    }
    if (row.defaultExpr !== row.originalDefaultExpr) {
      if (row.defaultExpr) {
        alterArgs.default_expr = row.defaultExpr;
      } else {
        alterArgs.default = row.defaultValue === "" ? null : row.defaultValue;
      }
    } else if (row.originalHasDefault) {
      if (row.defaultValue !== row.originalDefault) {
        alterArgs.default = row.defaultValue === "" ? null : row.defaultValue;
      }
    } else if (row.defaultValue) {
      alterArgs.default = row.defaultValue;
    }
    if (Object.keys(alterArgs).length > 1) {
      operations.push({ op: "alter_column", args: alterArgs });
    }
  });

  state.deletedColumns.forEach(function (name) {
    operations.push({ op: "drop_column", args: { name: name } });
  });

  var pkColumns = rows
    .filter(function (row) {
      return row.pk;
    })
    .map(function (row) {
      return row.name.trim();
    });
  if (tableAlterPkChanged(state, rows)) {
    operations.push({ op: "set_primary_key", args: { columns: pkColumns } });
  }

  if (tableAlterColumnsReordered(state, rows)) {
    operations.push({
      op: "reorder_columns",
      args: {
        columns: rows.map(function (row) {
          return row.name.trim();
        }),
      },
    });
  }

  if (tableAlterForeignKeysChanged(rows)) {
    operations.push({
      op: "set_foreign_keys",
      args: {
        foreign_keys: tableAlterForeignKeyRows(rows),
      },
    });
  }

  if (!operations.length && !columnTypeAssignments.length) {
    return { error: "No changes to apply." };
  }
  return {
    payload: operations.length ? { operations: operations } : null,
    columnTypeAssignments: columnTypeAssignments,
  };
}

function tableAlterQuotedName(name) {
  return '"' + name + '"';
}

function tableAlterReadableDefaultExpression(value) {
  return defaultExpressionLabelForValue(tableAlterDefaultExpressions(), value);
}

function tableAlterReadableValue(value) {
  if (value === null) {
    return "NULL";
  }
  return '"' + String(value) + '"';
}

function tableAlterOperationSummary(operation) {
  var args = operation.args || {};
  if (operation.op === "rename_table") {
    return {
      text: "Rename table to " + tableAlterQuotedName(args.to) + ".",
      damaging: false,
    };
  }
  if (operation.op === "add_column") {
    var addDetails = ["as " + args.type];
    if (args.not_null) {
      addDetails.push("with values required");
    }
    if (args.default_expr) {
      addDetails.push(
        "defaulting to " +
          tableAlterReadableDefaultExpression(args.default_expr),
      );
    } else if (Object.prototype.hasOwnProperty.call(args, "default")) {
      addDetails.push(
        "with default value " + tableAlterReadableValue(args.default),
      );
    }
    return {
      text:
        "Add column " +
        tableAlterQuotedName(args.name) +
        " " +
        addDetails.join(", ") +
        ".",
      damaging: false,
    };
  }
  if (operation.op === "rename_column") {
    return {
      text:
        "Rename column " +
        tableAlterQuotedName(args.name) +
        " to " +
        tableAlterQuotedName(args.to) +
        ".",
      damaging: false,
    };
  }
  if (operation.op === "alter_column") {
    var changes = [];
    if (args.type) {
      changes.push("set type to " + args.type);
    }
    if (Object.prototype.hasOwnProperty.call(args, "not_null")) {
      changes.push(
        args.not_null ? "not null (require values)" : "allow unset values",
      );
    }
    if (args.default_expr) {
      changes.push(
        "default to " + tableAlterReadableDefaultExpression(args.default_expr),
      );
    } else if (Object.prototype.hasOwnProperty.call(args, "default")) {
      changes.push(
        args.default === null
          ? "remove the default value"
          : "set default value to " + tableAlterReadableValue(args.default),
      );
    }
    return {
      text:
        "Change column " +
        tableAlterQuotedName(args.name) +
        ": " +
        changes.join(", ") +
        ".",
      damaging: false,
    };
  }
  if (operation.op === "drop_column") {
    return {
      text: "Drop column " + tableAlterQuotedName(args.name) + ".",
      damaging: true,
    };
  }
  if (operation.op === "set_primary_key") {
    return {
      text:
        "Set primary key to " +
        (args.columns || []).map(tableAlterQuotedName).join(", ") +
        ".",
      damaging: false,
    };
  }
  if (operation.op === "reorder_columns") {
    return {
      text:
        "Set column order to " +
        (args.columns || []).map(tableAlterQuotedName).join(", ") +
        ".",
      damaging: false,
    };
  }
  if (operation.op === "set_foreign_keys") {
    var foreignKeys = args.foreign_keys || [];
    return {
      text: foreignKeys.length
        ? "Set foreign keys to " +
          foreignKeys
            .map(function (foreignKey) {
              return (
                tableAlterQuotedName(foreignKey.column) +
                " -> " +
                foreignKey.fk_table +
                "." +
                foreignKey.fk_column
              );
            })
            .join(", ") +
          "."
        : "Remove all foreign keys.",
      damaging: false,
    };
  }
  return {
    text: "Run " + operation.op + ".",
    damaging: false,
  };
}

function tableAlterColumnTypeAssignmentSummary(assignment) {
  return {
    text: assignment.columnType
      ? "Set custom type for column " +
        tableAlterQuotedName(assignment.column) +
        " to " +
        assignment.columnType +
        "."
      : "Remove custom type from column " +
        tableAlterQuotedName(assignment.column) +
        ".",
    damaging: false,
  };
}

function tableAlterReviewItems(result) {
  var items = [];
  var operations = result.payload ? result.payload.operations || [] : [];
  operations.forEach(function (operation) {
    items.push(tableAlterOperationSummary(operation));
  });
  (result.columnTypeAssignments || []).forEach(function (assignment) {
    items.push(tableAlterColumnTypeAssignmentSummary(assignment));
  });
  return items;
}

function tableAlterReviewHasDamagingItems(items) {
  return items.some(function (item) {
    return item.damaging;
  });
}

function appendTableAlterReviewText(element, text) {
  text.split(/("[^"]+")/g).forEach(function (part) {
    if (!part) {
      return;
    }
    if (part.charAt(0) === '"' && part.charAt(part.length - 1) === '"') {
      var name = document.createElement("code");
      name.className = "table-alter-review-name";
      name.textContent = part.slice(1, -1);
      element.appendChild(name);
    } else {
      element.appendChild(document.createTextNode(part));
    }
  });
}

function tableAlterSetColumnTypeUrl(tableUrl) {
  if (tableUrl) {
    return tableUrl.replace(/\/$/, "") + "/-/set-column-type";
  }
  var data = tableAlterData();
  if (!data || !data.path) {
    return null;
  }
  var url = new URL(data.path, location.href);
  url.pathname = url.pathname.replace(/\/-\/alter\/?$/, "/-/set-column-type");
  return url.toString();
}

async function assignTableAlterColumnTypes(assignments, tableUrl) {
  if (!assignments.length) {
    return;
  }
  var url = tableAlterSetColumnTypeUrl(tableUrl);
  if (!url) {
    throw new Error("Could not find the set column type URL.");
  }
  for (var i = 0; i < assignments.length; i += 1) {
    var assignment = assignments[i];
    var response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        column: assignment.column,
        column_type: assignment.columnType
          ? {
              type: assignment.columnType,
            }
          : null,
      }),
    });
    var data = null;
    try {
      data = await response.json();
    } catch (_error) {
      data = null;
    }
    if (!response.ok || (data && data.ok === false)) {
      var error = rowMutationRequestError(response, data);
      throw new Error(
        "Saved schema changes, but could not set custom type for " +
          assignment.column +
          ": " +
          error.message,
      );
    }
  }
}

function tableAlterResultRenamesTable(result) {
  return !!(
    result &&
    result.payload &&
    (result.payload.operations || []).some(function (operation) {
      return operation.op === "rename_table";
    })
  );
}

function showTableAlterEditor(state) {
  state.mode = "edit";
  state.reviewResult = null;
  state.dialog.classList.remove("table-alter-reviewing");
  state.fields.hidden = false;
  state.review.hidden = true;
  state.review.textContent = "";
  state.backButton.hidden = true;
  var data = tableAlterData();
  state.dropButton.hidden = !(data && data.dropPath);
  state.saveButton.textContent = tableAlterSaveButtonText(state);
  updateTableAlterMoveButtons(state);
  updateTableAlterSaveButtonState(state);
}

function showTableAlterReview(state, result) {
  var items = tableAlterReviewItems(result);
  state.mode = "review";
  state.reviewResult = result;
  state.dialog.classList.add("table-alter-reviewing");
  state.fields.hidden = true;
  state.review.hidden = false;
  state.review.textContent = "";
  state.backButton.hidden = false;
  state.dropButton.hidden = true;
  state.saveButton.textContent = tableAlterSaveButtonText(state);
  updateTableAlterSaveButtonState(state);

  var heading = document.createElement("h3");
  heading.className = "table-alter-review-title";
  heading.tabIndex = -1;
  heading.textContent = "Review changes";
  state.review.appendChild(heading);

  var intro = document.createElement("p");
  intro.className = "table-alter-review-intro";
  intro.textContent = "These changes will be applied to the table.";
  state.review.appendChild(intro);

  if (tableAlterReviewHasDamagingItems(items)) {
    var warning = document.createElement("p");
    warning.className = "table-alter-review-warning";
    warning.setAttribute("role", "alert");
    warning.textContent =
      "Warning: data in dropped columns will be permanently lost.";
    state.review.appendChild(warning);
  }

  var list = document.createElement("ol");
  list.className = "table-alter-review-list";
  items.forEach(function (item) {
    var listItem = document.createElement("li");
    appendTableAlterReviewText(listItem, item.text);
    if (item.damaging) {
      listItem.className = "table-alter-review-damaging";
    }
    list.appendChild(listItem);
  });
  state.review.appendChild(list);
  heading.focus();
}

async function applyTableAlterChanges(state, result) {
  if (state.isSaving) {
    return;
  }
  if (!result) {
    showTableAlterDialogError(state, "Could not find the reviewed changes.");
    return;
  }
  var data = tableAlterData();
  if (!data || !data.path) {
    showTableAlterDialogError(state, "Could not find the alter table URL.");
    return;
  }
  clearTableAlterDialogError(state);
  if (result.error) {
    showTableAlterDialogError(state, result.error);
    return;
  }
  setTableAlterDialogSaving(state, true);
  try {
    var responseData = null;
    if (result.payload) {
      var response = await fetch(data.path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(result.payload),
      });
      try {
        responseData = await response.json();
      } catch (_error) {
        responseData = null;
      }
      if (!response.ok || (responseData && responseData.ok === false)) {
        throw rowMutationRequestError(response, responseData);
      }
    }
    var tableUrl = responseData && responseData.table_url;
    await assignTableAlterColumnTypes(
      result.columnTypeAssignments || [],
      tableUrl,
    );
    state.shouldRestoreFocus = false;
    state.dialog.close();
    if (tableAlterResultRenamesTable(result) && tableUrl) {
      window.location.href = tableUrl;
    } else {
      location.reload();
    }
  } catch (error) {
    setTableAlterDialogSaving(state, false);
    showTableAlterDialogError(state, error.message || "Could not alter table");
  }
}

function tableAlterDatabaseUrl() {
  var data = tableAlterData();
  if (!data || !data.path) {
    return null;
  }
  var url = new URL(data.path, location.href);
  url.pathname = url.pathname.replace(/\/[^/]+\/-\/alter\/?$/, "");
  url.search = "";
  url.hash = "";
  return url.toString();
}

async function dropTableFromAlterDialog(state) {
  if (state.isSaving) {
    return;
  }
  var data = tableAlterData();
  if (!data || !data.dropPath) {
    return;
  }
  if (
    !window.confirm(
      'Permanently delete the table "' +
        data.tableName +
        '"? This will delete all of its data and cannot be undone.',
    )
  ) {
    return;
  }
  clearTableAlterDialogError(state);
  setTableAlterDialogSaving(state, true);
  try {
    var response = await fetch(data.dropPath, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ confirm: true }),
    });
    var responseData = null;
    try {
      responseData = await response.json();
    } catch (_error) {
      responseData = null;
    }
    if (!response.ok || (responseData && responseData.ok === false)) {
      throw rowMutationRequestError(response, responseData);
    }
    state.shouldRestoreFocus = false;
    state.dialog.close();
    window.location.href = tableAlterDatabaseUrl() || "/";
  } catch (error) {
    setTableAlterDialogSaving(state, false);
    showTableAlterDialogError(state, error.message || "Could not drop table");
  }
}

async function saveTableAlterDialog(state) {
  if (state.isSaving) {
    return;
  }
  if (state.mode === "review") {
    if (!state.reviewResult) {
      showTableAlterDialogError(state, "Could not find the reviewed changes.");
      return;
    }
    await applyTableAlterChanges(state, state.reviewResult);
    return;
  }
  clearTableAlterDialogError(state);
  var result = collectTableAlterPayload(state);
  if (result.error) {
    showTableAlterDialogError(state, result.error);
    return;
  }
  showTableAlterReview(state, result);
}

function confirmDiscardTableAlterChanges(state) {
  if (!tableAlterDialogHasChanges(state)) {
    return true;
  }
  return window.confirm("Discard table changes?");
}

function closeTableAlterDialogIfConfirmed(state) {
  if (!state || state.isSaving) {
    return false;
  }
  if (!confirmDiscardTableAlterChanges(state)) {
    return false;
  }
  state.shouldRestoreFocus = true;
  state.dialog.close();
  return true;
}

function closeTableAlterDialog(state) {
  if (!state || state.isSaving) {
    return false;
  }
  state.shouldRestoreFocus = true;
  state.dialog.close();
  return true;
}

function ensureTableAlterDialog(manager) {
  if (tableAlterDialogState) {
    return tableAlterDialogState;
  }
  if (!window.HTMLDialogElement) {
    return null;
  }

  var dialog = document.createElement("dialog");
  dialog.id = TABLE_ALTER_DIALOG_ID;
  dialog.className = "table-alter-dialog";
  dialog.setAttribute("aria-labelledby", "table-alter-title");
  dialog.innerHTML = `
    <div class="modal-header">
      <span class="modal-title" id="table-alter-title">Alter table</span>
    </div>
    <form class="table-alter-form" method="post" novalidate>
      <p class="table-alter-error" id="table-alter-error" role="alert" tabindex="-1" hidden></p>
      <div class="table-alter-fields">
        <div class="table-alter-columns">
          <div class="table-alter-column-headings" aria-hidden="true">
            <span>Column</span>
            <span>Type</span>
            <span>Move</span>
            <span></span>
          </div>
          <div class="table-alter-column-list"></div>
          <button type="button" class="table-alter-add-column"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"></path><path d="M12 5v14"></path></svg><span>Add column</span></button>
        </div>
        <details class="table-alter-table-options">
          <summary>Rename table</summary>
          <div class="table-alter-table-name-field">
            <label class="table-alter-detail-label" for="table-alter-table-name">New table name</label>
            <input id="table-alter-table-name" class="table-alter-input table-alter-table-name" type="text" autocomplete="off" placeholder="table name" required>
          </div>
        </details>
      </div>
      <div class="table-alter-review" hidden></div>
      <div class="modal-footer">
        <button type="button" class="btn btn-danger table-alter-drop" hidden>Drop table</button>
        <button type="button" class="btn btn-ghost table-alter-back" hidden>Back</button>
        <button type="button" class="btn btn-ghost table-alter-cancel">Cancel</button>
        <button type="submit" class="btn btn-primary table-alter-save">Review changes</button>
      </div>
    </form>
  `;
  document.body.appendChild(dialog);

  tableAlterDialogState = {
    dialog: dialog,
    form: dialog.querySelector(".table-alter-form"),
    title: dialog.querySelector(".modal-title"),
    error: dialog.querySelector(".table-alter-error"),
    fields: dialog.querySelector(".table-alter-fields"),
    tableOptions: dialog.querySelector(".table-alter-table-options"),
    tableNameInput: dialog.querySelector(".table-alter-table-name"),
    review: dialog.querySelector(".table-alter-review"),
    columnList: dialog.querySelector(".table-alter-column-list"),
    addColumnButton: dialog.querySelector(".table-alter-add-column"),
    backButton: dialog.querySelector(".table-alter-back"),
    dropButton: dialog.querySelector(".table-alter-drop"),
    cancelButton: dialog.querySelector(".table-alter-cancel"),
    saveButton: dialog.querySelector(".table-alter-save"),
    currentButton: null,
    shouldRestoreFocus: true,
    isSaving: false,
    initialSignature: "",
    originalTableName: "",
    nextColumnIndex: 0,
    deletedColumns: [],
    originalColumnNames: [],
    originalPrimaryKeys: [],
    foreignKeyTargets: [],
    foreignKeyTargetsError: null,
    foreignKeyTargetsLoading: false,
    mode: "edit",
    reviewResult: null,
    manager: manager,
  };

  tableAlterDialogState.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    saveTableAlterDialog(tableAlterDialogState);
  });

  tableAlterDialogState.tableNameInput.addEventListener("input", function () {
    clearTableAlterDialogError(tableAlterDialogState);
    updateTableAlterSaveButtonState(tableAlterDialogState);
  });

  tableAlterDialogState.addColumnButton.addEventListener("click", function () {
    if (tableAlterDialogState.isSaving) {
      return;
    }
    var row = addTableAlterColumn(tableAlterDialogState, {
      type: "text",
      existing: false,
      expanded: true,
    });
    clearTableAlterDialogError(tableAlterDialogState);
    updateTableAlterMoveButtons(tableAlterDialogState);
    updateTableAlterSaveButtonState(tableAlterDialogState);
    row.querySelector(".table-alter-column-name").focus();
  });

  tableAlterDialogState.cancelButton.addEventListener("click", function () {
    closeTableAlterDialog(tableAlterDialogState);
  });

  tableAlterDialogState.dropButton.addEventListener("click", function () {
    dropTableFromAlterDialog(tableAlterDialogState);
  });

  tableAlterDialogState.backButton.addEventListener("click", function () {
    if (tableAlterDialogState.isSaving) {
      return;
    }
    clearTableAlterDialogError(tableAlterDialogState);
    showTableAlterEditor(tableAlterDialogState);
    var firstName = tableAlterDialogState.columnList.querySelector(
      ".table-alter-column-name",
    );
    if (firstName) {
      firstName.focus();
    }
  });

  dialog.addEventListener("click", function (ev) {
    if (ev.target === dialog) {
      closeTableAlterDialogIfConfirmed(tableAlterDialogState);
    }
  });

  dialog.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") {
      return;
    }
    ev.preventDefault();
    closeTableAlterDialogIfConfirmed(tableAlterDialogState);
  });

  dialog.addEventListener("cancel", function (ev) {
    ev.preventDefault();
    closeTableAlterDialogIfConfirmed(tableAlterDialogState);
  });

  dialog.addEventListener("close", function () {
    var state = tableAlterDialogState;
    clearTableAlterDialogError(state);
    setTableAlterDialogSaving(state, false);
    if (
      state.shouldRestoreFocus &&
      state.currentButton &&
      document.contains(state.currentButton)
    ) {
      state.currentButton.focus();
    }
  });

  return tableAlterDialogState;
}

function openTableAlterDialog(button, manager) {
  var data = tableAlterData();
  if (!data) {
    return;
  }
  var state = ensureTableAlterDialog(manager);
  if (!state) {
    return;
  }

  var menu = button.closest("details");
  if (menu) {
    menu.open = false;
  }
  state.manager = manager;
  state.currentButton = button;
  state.shouldRestoreFocus = true;
  state.title.textContent = "Alter table " + data.tableName;
  clearTableAlterDialogError(state);
  resetTableAlterDialog(state, data);
  loadSchemaDialogForeignKeyTargets(
    state,
    "table-alter",
    tableAlterForeignKeyTargetsUrl(),
    { filterByType: false },
  );
  if (!state.dialog.open) {
    state.dialog.showModal();
  }
  var firstName = state.columnList.querySelector(".table-alter-column-name");
  if (firstName) {
    firstName.focus();
  }
}

function initTableAlterActions(manager) {
  if (!window.fetch || !window.HTMLDialogElement || !tableAlterData()) {
    return;
  }
  document.addEventListener("click", function (ev) {
    var button = ev.target.closest('button[data-table-action="alter-table"]');
    if (!button) {
      return;
    }
    ev.preventDefault();
    openTableAlterDialog(button, manager);
  });
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

function foreignKeyAutocompleteUrl(column) {
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
  url.searchParams.set("_extra", "columns,column_types,column_details");
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

function isBase64JsonValue(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  var keys = Object.keys(value);
  return (
    keys.length === 2 &&
    Object.prototype.hasOwnProperty.call(value, "$base64") &&
    Object.prototype.hasOwnProperty.call(value, "encoded") &&
    value.$base64 === true &&
    typeof value.encoded === "string"
  );
}

function shouldUseBinaryControl(value, options) {
  options = options || {};
  var sqliteType = (options.sqliteType || "").toLowerCase();
  return (
    isBase64JsonValue(value) ||
    sqliteType === "blob" ||
    options.valueKind === "binary"
  );
}

function binaryEncodedValue(value) {
  return isBase64JsonValue(value) ? value.encoded : "";
}

function binaryByteLengthFromBase64(encoded) {
  encoded = (encoded || "").replace(/\s/g, "");
  if (!encoded) {
    return 0;
  }
  var padding = 0;
  if (encoded.slice(-2) === "==") {
    padding = 2;
  } else if (encoded.slice(-1) === "=") {
    padding = 1;
  }
  return Math.max(0, Math.floor((encoded.length * 3) / 4) - padding);
}

function rowEditBinaryValue(encoded) {
  return {
    $base64: true,
    encoded: encoded || "",
  };
}

function formatRowEditBinarySize(byteLength) {
  var number = byteLength.toLocaleString();
  return "Binary: " + number + " byte" + (byteLength === 1 ? "" : "s");
}

function base64ToUint8Array(encoded) {
  var binary = window.atob(encoded || "");
  var bytes = new Uint8Array(binary.length);
  for (var i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function uint8ArrayToBase64(bytes) {
  var chunks = [];
  var chunkSize = 0x8000;
  for (var i = 0; i < bytes.length; i += chunkSize) {
    chunks.push(
      String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize)),
    );
  }
  return window.btoa(chunks.join(""));
}

function rowEditBinaryImageMimeType(bytes) {
  if (!bytes || !bytes.length) {
    return null;
  }
  if (
    bytes.length >= 8 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47 &&
    bytes[4] === 0x0d &&
    bytes[5] === 0x0a &&
    bytes[6] === 0x1a &&
    bytes[7] === 0x0a
  ) {
    return "image/png";
  }
  if (
    bytes.length >= 3 &&
    bytes[0] === 0xff &&
    bytes[1] === 0xd8 &&
    bytes[2] === 0xff
  ) {
    return "image/jpeg";
  }
  if (
    bytes.length >= 6 &&
    bytes[0] === 0x47 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x38 &&
    (bytes[4] === 0x37 || bytes[4] === 0x39) &&
    bytes[5] === 0x61
  ) {
    return "image/gif";
  }
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x46 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50
  ) {
    return "image/webp";
  }
  if (
    bytes.length >= 12 &&
    bytes[4] === 0x66 &&
    bytes[5] === 0x74 &&
    bytes[6] === 0x79 &&
    bytes[7] === 0x70 &&
    bytes[8] === 0x61 &&
    bytes[9] === 0x76 &&
    bytes[10] === 0x69 &&
    (bytes[11] === 0x66 || bytes[11] === 0x73)
  ) {
    return "image/avif";
  }
  if (bytes.length >= 2 && bytes[0] === 0x42 && bytes[1] === 0x4d) {
    return "image/bmp";
  }
  return null;
}

function valueToEditText(value) {
  if (value === null || typeof value === "undefined") {
    return "";
  }
  if (isBase64JsonValue(value)) {
    return value.encoded;
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
  if (isBase64JsonValue(value)) {
    return false;
  }
  if (value && typeof value === "object") {
    return true;
  }
  var text = valueToEditText(value);
  return text.length > 80 || /[\r\n]/.test(text);
}

function rowEditValueKind(value) {
  if (isBase64JsonValue(value)) {
    return "binary";
  }
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

function revokeRowEditBinaryPreview(wrapper) {
  if (wrapper && wrapper._rowEditBinaryPreviewUrl) {
    URL.revokeObjectURL(wrapper._rowEditBinaryPreviewUrl);
    wrapper._rowEditBinaryPreviewUrl = null;
  }
}

function updateRowEditBinaryPreview(wrapper, encoded, byteLength) {
  var preview = wrapper.querySelector(".row-edit-binary-preview");
  if (!preview) {
    return;
  }
  revokeRowEditBinaryPreview(wrapper);
  preview.hidden = true;
  preview.textContent = "";
  if (
    !encoded ||
    byteLength >= ROW_EDIT_BINARY_IMAGE_PREVIEW_MAX_BYTES ||
    !window.atob ||
    !window.Blob ||
    !window.URL ||
    !URL.createObjectURL
  ) {
    return;
  }

  var bytes;
  try {
    bytes = base64ToUint8Array(encoded);
  } catch (_error) {
    return;
  }
  var mimeType = rowEditBinaryImageMimeType(bytes);
  if (!mimeType) {
    return;
  }

  var image = document.createElement("img");
  image.alt = "";
  var objectUrl = URL.createObjectURL(new Blob([bytes], { type: mimeType }));
  wrapper._rowEditBinaryPreviewUrl = objectUrl;
  image.src = objectUrl;
  preview.appendChild(image);

  var showPreview = function () {
    if (wrapper._rowEditBinaryPreviewUrl === objectUrl) {
      preview.hidden = false;
    }
  };
  var hidePreview = function () {
    if (wrapper._rowEditBinaryPreviewUrl === objectUrl) {
      revokeRowEditBinaryPreview(wrapper);
      preview.hidden = true;
      preview.textContent = "";
    }
  };
  if (image.decode) {
    image.decode().then(showPreview).catch(hidePreview);
  } else {
    image.onload = showPreview;
    image.onerror = hidePreview;
  }
}

function updateRowEditBinaryDisplay(wrapper, control, fileName) {
  var kind = rowEditControlValueKind(control);
  var size = wrapper.querySelector(".row-edit-binary-size");
  var name = wrapper.querySelector(".row-edit-binary-name");
  var clear = wrapper.querySelector(".row-edit-binary-clear");
  var byteLength =
    kind === "binary" ? binaryByteLengthFromBase64(control.value) : null;

  if (size) {
    size.textContent =
      kind === "binary" ? formatRowEditBinarySize(byteLength) : "No binary data";
  }
  if (name) {
    name.textContent = fileName || "";
    name.hidden = !fileName;
  }
  if (clear) {
    clear.hidden = control.dataset.notNull === "1" || kind !== "binary";
  }
  if (kind === "binary") {
    updateRowEditBinaryPreview(wrapper, control.value, byteLength);
  } else {
    updateRowEditBinaryPreview(wrapper, "", 0);
  }
}

function setRowEditBinaryControlValue(control, encoded, fileName) {
  control.value = encoded || "";
  control.dataset.currentValueKind = "binary";
  updateRowEditBinaryDisplay(control._rowEditBinaryWrapper, control, fileName);
  control.dispatchEvent(new Event("input", { bubbles: true }));
}

function clearRowEditBinaryControlValue(control) {
  control.value = "";
  control.dataset.currentValueKind = "null";
  updateRowEditBinaryDisplay(control._rowEditBinaryWrapper, control, "");
  control.dispatchEvent(new Event("input", { bubbles: true }));
}

function readRowEditBinaryFile(file) {
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var bytes = new Uint8Array(reader.result || new ArrayBuffer(0));
        resolve({
          encoded: uint8ArrayToBase64(bytes),
          name: file && file.name ? file.name : "",
        });
      } catch (error) {
        reject(error);
      }
    };
    reader.onerror = function () {
      reject(reader.error || new Error("Could not read file"));
    };
    reader.readAsArrayBuffer(file);
  });
}

function rowEditBinaryValueFromText(text) {
  var encoder = new TextEncoder();
  var bytes = encoder.encode(text || "");
  return {
    encoded: uint8ArrayToBase64(bytes),
    name: "Pasted text",
  };
}

function rowEditBinaryFirstClipboardFile(clipboardData) {
  if (!clipboardData) {
    return null;
  }
  if (clipboardData.files && clipboardData.files.length) {
    return clipboardData.files[0];
  }
  var items = clipboardData.items || [];
  for (var i = 0; i < items.length; i += 1) {
    if (items[i].kind === "file") {
      return items[i].getAsFile();
    }
  }
  return null;
}

function handleRowEditBinaryFile(control, file) {
  if (!file) {
    return;
  }
  readRowEditBinaryFile(file)
    .then(function (value) {
      setRowEditBinaryControlValue(control, value.encoded, value.name);
    })
    .catch(function (error) {
      console.error("Could not read binary file", error);
    });
}

function createRowEditBinaryControlElement(control, value, options, labelId) {
  var wrapper = document.createElement("div");
  wrapper.className = "row-edit-binary-control";
  wrapper.dataset.column = control.name;
  wrapper.setAttribute("role", "group");
  wrapper.setAttribute("tabindex", "0");
  wrapper.setAttribute("aria-labelledby", labelId);

  var preview = document.createElement("div");
  preview.className = "row-edit-binary-preview";
  preview.hidden = true;

  var status = document.createElement("div");
  status.className = "row-edit-binary-status";
  var size = document.createElement("span");
  size.className = "row-edit-binary-size";
  var name = document.createElement("span");
  name.className = "row-edit-binary-name";
  name.hidden = true;
  status.appendChild(size);
  status.appendChild(name);

  var actions = document.createElement("div");
  actions.className = "row-edit-binary-actions";
  var fileLabel = document.createElement("label");
  fileLabel.className = "row-edit-binary-file-button";
  fileLabel.textContent = "Attach file";
  var fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.setAttribute("aria-label", "Attach file for " + control.name);
  fileInput.addEventListener("change", function () {
    if (fileInput.files && fileInput.files.length) {
      handleRowEditBinaryFile(control, fileInput.files[0]);
    }
  });
  fileLabel.appendChild(fileInput);
  actions.appendChild(fileLabel);

  var clearButton = document.createElement("button");
  clearButton.type = "button";
  clearButton.className = "row-edit-binary-clear";
  clearButton.textContent = "Set NULL";
  clearButton.addEventListener("click", function () {
    clearRowEditBinaryControlValue(control);
    wrapper.focus();
  });
  actions.appendChild(clearButton);

  var dropTarget = document.createElement("div");
  dropTarget.className = "row-edit-binary-drop-target";
  dropTarget.textContent = "Drop or paste file contents";
  ["dragenter", "dragover"].forEach(function (eventName) {
    wrapper.addEventListener(eventName, function (ev) {
      ev.preventDefault();
      wrapper.classList.add("row-edit-binary-dragover");
    });
  });
  ["dragleave", "drop"].forEach(function (eventName) {
    wrapper.addEventListener(eventName, function () {
      wrapper.classList.remove("row-edit-binary-dragover");
    });
  });
  wrapper.addEventListener("drop", function (ev) {
    ev.preventDefault();
    var file = ev.dataTransfer && ev.dataTransfer.files[0];
    handleRowEditBinaryFile(control, file);
  });
  wrapper.addEventListener("paste", function (ev) {
    var file = rowEditBinaryFirstClipboardFile(ev.clipboardData);
    if (file) {
      ev.preventDefault();
      handleRowEditBinaryFile(control, file);
      return;
    }
    var text = ev.clipboardData && ev.clipboardData.getData("text");
    if (text) {
      ev.preventDefault();
      var pasted = rowEditBinaryValueFromText(text);
      setRowEditBinaryControlValue(control, pasted.encoded, pasted.name);
    }
  });

  control.type = "hidden";
  control._rowEditBinaryWrapper = wrapper;
  wrapper.appendChild(control);
  wrapper.appendChild(preview);
  wrapper.appendChild(status);
  wrapper.appendChild(actions);
  wrapper.appendChild(dropTarget);

  updateRowEditBinaryDisplay(wrapper, control, "");
  return wrapper;
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
  var pageData = tablePageData();
  var defaultExpression = defaultExpressionForContext(
    options.defaultExpression,
  );
  return {
    mode: options.mode || "edit",
    database: pageData.database || null,
    table:
      pageData.table ||
      (tableInsertData() && tableInsertData().tableName) ||
      null,
    tableUrl: pageData.tableUrl || null,
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
    var binaryControl = field.querySelector(".row-edit-binary-control");
    if (binaryControl) {
      binaryControl.focus();
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
  state.fields
    .querySelectorAll(".row-edit-binary-control")
    .forEach(function (binaryControl) {
      revokeRowEditBinaryPreview(binaryControl);
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
  var isBinaryField = shouldUseBinaryControl(value, options);
  var pluginControl = isBinaryField
    ? null
    : makeColumnField(options.manager, context);
  var useTextarea =
    !isBinaryField &&
    ((pluginControl && pluginControl.useTextarea === true) ||
      shouldUseTextarea(value, columnType));
  var control = useTextarea
    ? document.createElement("textarea")
    : document.createElement("input");
  var initialValue = isBinaryField
    ? binaryEncodedValue(value)
    : valueToEditText(value);
  var initialValueKind = options.valueKind || rowEditValueKind(value);
  if (isBinaryField) {
    initialValueKind = isBase64JsonValue(value) ? "binary" : "null";
  }
  control.className = "row-edit-input";
  control.id = fieldId;
  control.name = column;
  control.value = initialValue;
  control.setAttribute("aria-describedby", metaId);
  control.dataset.initialValue = initialValue;
  control.dataset.initialValueKind = initialValueKind;
  control.dataset.primaryKey = isPk ? "1" : "0";
  control.dataset.currentValueKind = control.dataset.initialValueKind;
  if (isBinaryField) {
    control.dataset.binaryField = "1";
    control.dataset.notNull = options.notnull ? "1" : "0";
  }
  if (hasDefaultExpression) {
    control.dataset.useSqliteDefault = useSqliteDefault ? "1" : "0";
  }
  if (useSqliteDefault) {
    control.disabled = true;
  }
  if (options.omitIfBlank || (isBinaryField && options.mode === "insert")) {
    control.dataset.omitIfBlank = "1";
  }

  if (isBinaryField) {
    control.type = "hidden";
  } else if (control.nodeName === "TEXTAREA") {
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
    (isBinaryField &&
      createRowEditBinaryControlElement(control, value, options, labelId)) ||
    pluginControlElement ||
    rowEditControlElement(control, options.autocompleteUrl);
  if (options.autocompleteUrl && !pluginControlElement && !isBinaryField) {
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
    state.isLoading || state.isSaving || !state.hasLoaded;
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
  if (rowEditControlValueKind(control) === "binary") {
    return rowEditBinaryValue(value);
  }
  return valueFromRowEditText(
    control.name,
    value,
    rowEditControlValueKind(control),
  );
}

function valueFromRowEditText(name, value, initialValueKind) {
  var trimmed = value.trim();

  if (initialValueKind === "binary") {
    return rowEditBinaryValue(value);
  }
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
    if (
      control.dataset.omitIfBlank === "1" &&
      control.value === "" &&
      rowEditControlValueKind(control) !== "binary"
    ) {
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
      var insertData = tableInsertData() || {};
      var insertedRowData =
        data && data.rows && data.rows.length ? data.rows[0] : null;
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
        var insertedStatusMessage = insertedRowStatusMessage(
          tildeDecode(insertedRowId),
          rowTitleLabel(insertedRow),
        );
        var addedRow = addInsertedRowToPage(insertedRow);
        state.dialog.close();
        showRowMutationStatus(state.manager, insertedStatusMessage, false);
        if (addedRow) {
          var insertedFocusTarget =
            addedRow.querySelector('button[data-row-action="edit"]') ||
            addedRow;
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
  var columnDetails = data.column_details || {};

  destroyRowEditFields(state);
  columns.forEach(function (column, index) {
    var columnDetail = columnDetails[column] || {};
    state.fields.appendChild(
      createRowEditField(
        column,
        row ? row[column] : null,
        primaryKeys.indexOf(column) !== -1,
        columnTypes[column],
        index,
        {
          autocompleteUrl: foreignKeyAutocompleteUrl(column),
          dialog: state.dialog,
          form: state.form,
          manager: state.manager,
          mode: state.mode,
          notnull: columnDetail.notnull,
          primaryKeyReadonly: true,
          sqliteType: columnDetail.sqlite_type,
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

function renderRowInsertFields(state, data) {
  var columns = data.columns || [];

  destroyRowEditFields(state);
  columns.forEach(function (column, index) {
    state.fields.appendChild(
      createRowEditField(
        column.name,
        "",
        !!column.is_pk,
        column.column_type,
        index,
        {
          autocompleteUrl: foreignKeyAutocompleteUrl(column.name),
          dialog: state.dialog,
          form: state.form,
          defaultExpression: column.default,
          manager: state.manager,
          mode: state.mode,
          notnull: column.notnull,
          primaryKeyReadonly: false,
          sqliteType: column.sqlite_type,
          useSqliteDefault: column.default !== null,
          valueKind: column.value_kind,
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
    isClosePending: false,
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
    state.loadId += 1;
    state.isClosePending = false;
    clearRowEditDialogError(state);
    state.hasLoaded = false;
    destroyRowEditFields(state);
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
  state.currentInsertUrl = null;
  state.currentUpdateUrl = rowUpdateUrl(row);
  state.currentFragmentUrl = rowFragmentUrl(row);
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
  state.dialog.removeAttribute("aria-describedby");
  setRowDialogTitle(
    state.title,
    "Edit row",
    state.currentPkPath || "this row",
    rowTitleLabel(row),
  );
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
  state.dialog.removeAttribute("aria-describedby");
  setRowDialogTitle(
    state.title,
    insertData.tableName
      ? "Insert row into " + insertData.tableName
      : "Insert row",
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

document.addEventListener("datasette_init", function (evt) {
  const { detail: manager } = evt;

  registerBuiltinColumnFieldPlugins(manager);
  initTableCreateActions(manager);
  initTableAlterActions(manager);
  initRowInsertActions(manager);
  initRowEditActions(manager);
  initRowDeleteActions(manager);
});
