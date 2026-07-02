var MOBILE_COLUMN_BREAKPOINT = 576;
var MOBILE_COLUMN_DIALOG_ID = "mobile-column-actions-dialog";
var MOBILE_COLUMN_DIALOG_TITLE_ID = "mobile-column-actions-title";

function mobileColumnHeaders(manager) {
  return Array.from(
    document.querySelectorAll(manager.selectors.tableHeaders),
  ).filter((th) => th.dataset.column && th.dataset.isLinkColumn !== "1");
}

function mobileColumnMetaText(th) {
  var parts = [];
  if (th.dataset.columnType) {
    parts.push(th.dataset.columnType);
  }
  if (th.dataset.isPk === "1") {
    parts.push("pk");
  }
  if (th.dataset.columnNotNull === "1") {
    parts.push("not null");
  }
  return parts.join(", ");
}

function createMobileColumnActionNode(itemConfig, closeDialog) {
  var actionNode;
  if (itemConfig.href) {
    actionNode = document.createElement("a");
    actionNode.href = itemConfig.href;
  } else {
    actionNode = document.createElement("button");
    actionNode.type = "button";
  }
  actionNode.textContent = itemConfig.label;

  if (itemConfig.onClick) {
    actionNode.addEventListener("click", function (ev) {
      try {
        itemConfig.onClick.call(actionNode, ev);
      } finally {
        closeDialog({ restoreFocus: false });
      }
    });
  }

  return actionNode;
}

function initMobileColumnActions(manager) {
  var triggerButton = document.querySelector(".column-actions-mobile");
  if (!triggerButton) {
    return;
  }

  if (
    !window.URLSearchParams ||
    !window.DatasetteModal ||
    !window.DatasetteModal.supported ||
    !manager.columnActions
  ) {
    triggerButton.style.display = "none";
    return;
  }

  if (!mobileColumnHeaders(manager).length) {
    triggerButton.style.display = "none";
    return;
  }

  var modal = window.DatasetteModal.create({
    id: MOBILE_COLUMN_DIALOG_ID,
    className: "mobile-column-actions-dialog",
    title: "Column actions",
    titleId: MOBILE_COLUMN_DIALOG_TITLE_ID,
    content: `
    <div class="list-wrap mobile-column-list"></div>
    <div class="modal-footer">
      <span class="footer-info">Tap a column to reveal actions.</span>
      <button type="button" class="btn btn-ghost mobile-column-actions-done">Done</button>
    </div>
  `,
  });
  var dialog = modal.dialog;

  triggerButton.setAttribute("aria-haspopup", "dialog");
  triggerButton.setAttribute("aria-controls", MOBILE_COLUMN_DIALOG_ID);
  triggerButton.setAttribute("aria-expanded", "false");

  var listWrap = dialog.querySelector(".mobile-column-list");
  var doneButton = dialog.querySelector(".mobile-column-actions-done");
  var expandedSectionId = null;

  function updateExpandedSection() {
    Array.from(dialog.querySelectorAll(".col-header")).forEach((button) => {
      var controlsId = button.getAttribute("aria-controls");
      var actionList = dialog.querySelector("#" + controlsId);
      var isExpanded = controlsId === expandedSectionId;
      button.setAttribute("aria-expanded", isExpanded ? "true" : "false");
      actionList.hidden = !isExpanded;
      actionList.classList.toggle("expanded", isExpanded);
    });
  }

  function scrollExpandedSectionIntoView(section) {
    var sectionTop = section.offsetTop;
    var sectionBottom = sectionTop + section.offsetHeight;
    var visibleTop = listWrap.scrollTop;
    var visibleBottom = visibleTop + listWrap.clientHeight;
    var sectionHeight = section.offsetHeight;

    if (sectionTop < visibleTop) {
      listWrap.scrollTop = sectionTop;
      return;
    }

    if (sectionBottom <= visibleBottom) {
      return;
    }

    if (sectionHeight <= listWrap.clientHeight) {
      listWrap.scrollTop = sectionBottom - listWrap.clientHeight;
    } else {
      listWrap.scrollTop = sectionTop;
    }
  }

  function closeDialog(options) {
    options = options || {};
    var restoreFocus = options.restoreFocus !== false;
    if (modal.open) {
      modal.close({ restoreFocus: restoreFocus });
    } else {
      triggerButton.setAttribute("aria-expanded", "false");
      if (restoreFocus) {
        triggerButton.focus();
      }
    }
  }

  function renderDialog() {
    var headers = mobileColumnHeaders(manager);
    if (!headers.length) {
      closeDialog({ restoreFocus: false });
      triggerButton.style.display = "none";
      return false;
    }

    if (
      !headers.some(
        (_th, index) => `mobile-column-actions-${index}` === expandedSectionId,
      )
    ) {
      expandedSectionId = null;
    }

    modal.setMeta(`${headers.length} column${headers.length === 1 ? "" : "s"}`);
    listWrap.innerHTML = "";

    if (manager.columnActions.shouldShowShowAllColumns()) {
      var topActions = document.createElement("div");
      topActions.className = "mobile-column-top-actions";

      var showAllColumns = document.createElement("a");
      showAllColumns.className = "btn btn-ghost mobile-column-top-action";
      showAllColumns.href = manager.columnActions.showAllColumnsUrl();
      showAllColumns.textContent = "Show all columns";

      topActions.appendChild(showAllColumns);
      listWrap.appendChild(topActions);
    }

    headers.forEach((th, index) => {
      var sectionId = `mobile-column-actions-${index}`;
      var actionState = manager.columnActions.buildColumnActionState(th, {
        includeChooseColumns: false,
        includeShowAllColumns: false,
      });
      var section = document.createElement("section");
      section.className = "mobile-column-section";

      var headerButton = document.createElement("button");
      headerButton.type = "button";
      headerButton.className = "col-header";
      headerButton.setAttribute("aria-controls", sectionId);
      headerButton.setAttribute("aria-expanded", "false");

      var headerText = document.createElement("span");
      headerText.className = "mobile-column-header-text";

      var name = document.createElement("span");
      name.className = "mobile-column-name";
      name.textContent = th.dataset.column;
      headerText.appendChild(name);

      var metaText = mobileColumnMetaText(th);
      if (metaText) {
        var meta = document.createElement("span");
        meta.className = "mobile-column-meta";
        meta.textContent = metaText;
        headerText.appendChild(meta);
      }

      var chevron = document.createElement("span");
      chevron.className = "mobile-column-chevron";
      chevron.setAttribute("aria-hidden", "true");
      chevron.textContent = "▾";

      headerButton.appendChild(headerText);
      headerButton.appendChild(chevron);
      headerButton.addEventListener("click", function () {
        expandedSectionId = expandedSectionId === sectionId ? null : sectionId;
        updateExpandedSection();
        if (expandedSectionId === sectionId) {
          scrollExpandedSectionIntoView(section);
        }
      });

      var actionContainer = document.createElement("div");
      actionContainer.id = sectionId;
      actionContainer.className = "col-actions";
      actionContainer.hidden = true;

      if (actionState.columnDescription) {
        var description = document.createElement("p");
        description.className = "mobile-column-description";
        description.textContent = actionState.columnDescription;
        actionContainer.appendChild(description);
      }

      if (actionState.actionItems.length) {
        var actionList = document.createElement("ul");
        actionState.actionItems.forEach((itemConfig) => {
          var actionItem = document.createElement("li");
          actionItem.appendChild(
            createMobileColumnActionNode(itemConfig, closeDialog),
          );
          actionList.appendChild(actionItem);
        });
        actionContainer.appendChild(actionList);
      } else {
        var noActions = document.createElement("p");
        noActions.className = "mobile-column-no-actions";
        noActions.textContent = "No actions available";
        actionContainer.appendChild(noActions);
      }

      section.appendChild(headerButton);
      section.appendChild(actionContainer);
      listWrap.appendChild(section);
    });

    updateExpandedSection();
    return true;
  }

  function openDialog() {
    if (window.innerWidth > MOBILE_COLUMN_BREAKPOINT) {
      return;
    }
    if (!renderDialog()) {
      return;
    }
    modal.showModal({ trigger: triggerButton });
    triggerButton.setAttribute("aria-expanded", "true");
    var focusTarget =
      dialog.querySelector(".mobile-column-top-action") ||
      dialog.querySelector(".col-header") ||
      doneButton;
    focusTarget.focus();
  }

  triggerButton.addEventListener("click", function () {
    if (modal.open) {
      closeDialog();
    } else {
      openDialog();
    }
  });

  doneButton.addEventListener("click", function () {
    closeDialog();
  });

  modal.addEventListener("datasette-modal-close", function () {
    triggerButton.setAttribute("aria-expanded", "false");
  });

  window.addEventListener("resize", function () {
    if (window.innerWidth > MOBILE_COLUMN_BREAKPOINT && modal.open) {
      closeDialog({ restoreFocus: false });
    }
  });
}

document.addEventListener("datasette_init", function (evt) {
  initMobileColumnActions(evt.detail);
});
