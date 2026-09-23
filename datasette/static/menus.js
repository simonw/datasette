// Shared positioning, dismissal and keyboard controls for Datasette menus.
(() => {
  let active = null;
  let positionFrame = null;
  const gutter = 12;

  function actions(panel) {
    return Array.from(
      panel.querySelectorAll("a[href], button:not([disabled])"),
    ).filter((item) => item.getClientRects().length);
  }

  function close({ restoreFocus = false } = {}) {
    if (!active) return;
    const { trigger, panel, details, tabIndexes, observer } = active;
    active = null;
    observer?.disconnect();
    if (
      panel.hasAttribute("popover") &&
      typeof panel.hidePopover === "function"
    ) {
      panel.hidePopover();
    }
    panel.removeAttribute("popover");
    panel.classList.remove("datasette-menu-floating");
    ["width", "max-height", "left", "top"].forEach((name) =>
      panel.style.removeProperty(name),
    );
    if (details) details.open = false;
    else panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    tabIndexes.forEach(([item, value]) => {
      if (value === null) item.removeAttribute("tabindex");
      else item.setAttribute("tabindex", value);
    });
    if (restoreFocus && trigger.isConnected)
      trigger.focus({ preventScroll: true });
  }

  function position() {
    if (!active) return;
    const { trigger, panel, align } = active;
    if (!trigger.isConnected || !panel.isConnected) {
      close();
      return;
    }
    const anchor = trigger.getBoundingClientRect();
    const viewport = window.visualViewport;
    const width = viewport ? viewport.width : window.innerWidth;
    const height = viewport ? viewport.height : window.innerHeight;
    const x = viewport ? viewport.offsetLeft : 0;
    const y = viewport ? viewport.offsetTop : 0;
    const narrow = width <= 600;
    const panelWidth = Math.max(
      0,
      Math.min(narrow ? 360 : 300, width - gutter * 2),
    );
    panel.style.width = panelWidth + "px";
    panel.style.maxHeight = Math.max(0, height - gutter * 2) + "px";
    const naturalHeight =
      panel.scrollHeight + panel.offsetHeight - panel.clientHeight;
    const gap = panel.classList.contains("nav-menu-inner") ? 0 : 8;
    const below = Math.max(0, y + height - anchor.bottom - gutter - gap);
    const above = Math.max(0, anchor.top - y - gutter - gap);
    // Narrow screens still anchor to the trigger when there is room beside it.
    const sheet = Math.max(above, below) < Math.min(naturalHeight, 180);
    let top;
    let left;
    if (sheet) {
      top = y + height - panel.offsetHeight - gutter;
      left = x + (width - panelWidth) / 2;
    } else {
      const down = below >= naturalHeight || below >= above;
      panel.style.maxHeight =
        Math.max(0, Math.min(height - gutter * 2, down ? below : above)) + "px";
      top = down ? anchor.bottom + gap : anchor.top - panel.offsetHeight - gap;
      left = align === "end" ? anchor.right - panelWidth : anchor.left;
    }
    panel.style.left =
      Math.max(x + gutter, Math.min(left, x + width - panelWidth - gutter)) +
      "px";
    panel.style.top =
      Math.max(
        y + gutter,
        Math.min(top, y + height - panel.offsetHeight - gutter),
      ) + "px";
  }

  function schedulePosition(event) {
    // Scrolling inside the menu must not reposition it or disturb keyboard focus.
    if (
      !active ||
      (event &&
        event.target instanceof Node &&
        active.panel.contains(event.target))
    )
      return;
    if (positionFrame !== null) return;
    positionFrame = requestAnimationFrame(() => {
      positionFrame = null;
      position();
    });
  }

  function open({
    trigger,
    panel,
    details = null,
    align = "start",
    focusLast = false,
    focusMenu = true,
  }) {
    close();
    active = { trigger, panel, details, align, tabIndexes: [] };
    if (details) details.open = true;
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    panel.classList.add("datasette-menu-floating");
    if (typeof panel.showPopover === "function") {
      panel.setAttribute("popover", "manual");
      panel.showPopover();
    }
    position();
    const items = actions(panel);
    active.tabIndexes = items.map((item) => [
      item,
      item.getAttribute("tabindex"),
    ]);
    items.forEach((item) => item.setAttribute("tabindex", "-1"));
    (focusMenu
      ? focusLast
        ? items[items.length - 1]
        : items[0]
      : trigger
    )?.focus({
      preventScroll: true,
    });
    // Plugin actions and descriptions can change while a menu is open.
    active.observer = new MutationObserver(() => schedulePosition());
    active.observer.observe(panel, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  function containsTarget(target) {
    return (
      active &&
      (active.panel.contains(target) || active.trigger.contains(target))
    );
  }

  document.addEventListener("pointerdown", (event) => {
    if (active && !containsTarget(event.target)) close();
  });
  document.addEventListener("focusin", (event) => {
    if (active && !containsTarget(event.target)) close();
  });
  // Capture before action handlers open dialogs: focus restoration then targets
  // the trigger, never an item in a hidden menu. Default link/form actions remain intact.
  document.addEventListener(
    "click",
    (event) => {
      if (!active) return;
      if (!containsTarget(event.target)) {
        close();
        return;
      }
      if (!active.panel.contains(event.target)) return;
      const item = event.target.closest("a[href], button");
      if (!item || item.disabled) return;
      close({ restoreFocus: true });
    },
    true,
  );
  document.addEventListener("keydown", (event) => {
    if (!active || event.defaultPrevented || !containsTarget(event.target))
      return;
    if (event.key === "Tab") {
      // Continue normal tab order from the trigger, including when WebKit moves
      // focus to browser chrome without dispatching focusin on another element.
      close({ restoreFocus: true });
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close({ restoreFocus: true });
      return;
    }
    const items = actions(active.panel);
    if (!items.length) return;
    const index = items.indexOf(document.activeElement);
    let next;
    if (event.key === "ArrowDown") next = (index + 1) % items.length;
    if (event.key === "ArrowUp")
      next =
        index < 0
          ? items.length - 1
          : (index - 1 + items.length) % items.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      items[next].focus();
    }
  });
  window.addEventListener("resize", schedulePosition);
  window.addEventListener("scroll", schedulePosition, true);
  window.visualViewport?.addEventListener("resize", schedulePosition);
  window.visualViewport?.addEventListener("scroll", schedulePosition);

  function attachDetails() {
    document
      .querySelectorAll("details[data-datasette-menu]")
      .forEach((details) => {
        const trigger = details.querySelector("summary");
        const panel = details.querySelector(".datasette-menu");
        if (!trigger || !panel) return;
        const options = {
          trigger,
          panel,
          details,
          align: details.classList.contains("nav-menu") ? "end" : "start",
        };
        trigger.addEventListener("click", (event) => {
          event.preventDefault();
          if (active?.trigger === trigger) close({ restoreFocus: true });
          else open({ ...options, focusMenu: event.detail === 0 });
        });
        trigger.addEventListener("keydown", (event) => {
          if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
          event.preventDefault();
          event.stopPropagation();
          open({ ...options, focusLast: event.key === "ArrowUp" });
        });
        details.addEventListener("toggle", () => {
          if (details.open && active?.details !== details) open(options);
          else if (!details.open && active?.details === details) close();
          trigger.setAttribute("aria-expanded", String(details.open));
        });
      });
  }
  document.addEventListener("DOMContentLoaded", attachDetails);
  window.DatasetteMenu = {
    open,
    close,
    isOpenFor: (trigger) => active?.trigger === trigger,
  };
})();
