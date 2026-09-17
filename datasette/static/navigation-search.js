let navigationSearchInstanceCounter = 0;

class NavigationSearch extends HTMLElement {
  constructor() {
    super();
    this.instanceId = ++navigationSearchInstanceCounter;
    this.inputId = `navigation-search-input-${this.instanceId}`;
    this.instructionsId = `navigation-search-instructions-${this.instanceId}`;
    this.listboxId = `navigation-search-results-${this.instanceId}`;
    this.recentHeadingId = `navigation-search-recent-${this.instanceId}`;
    this.statusId = `navigation-search-status-${this.instanceId}`;
    this.titleId = `navigation-search-title-${this.instanceId}`;
    this.selectedIndex = -1;
    this.matches = [];
    this.renderedMatches = [];
    this.debounceTimer = null;
  }

  connectedCallback() {
    if (this._initialized) return;
    this._initialized = true;
    this.render();
    this.setupEventListeners();
  }

  render() {
    this.innerHTML = `
      <datasette-modal><dialog aria-modal="true" aria-labelledby="${this.titleId}">
                <div class="search-container">
                    <h2 id="${this.titleId}" class="visually-hidden">Jump to</h2>
                    <p id="${this.instructionsId}" class="visually-hidden">Type to search. Use up and down arrow keys to move through results, Enter to select a result, and Escape to close this menu.</p>
                    <div id="${this.statusId}" class="visually-hidden" aria-live="polite" aria-atomic="true"></div>
                    <div class="search-input-wrapper">
                        <input 
                            id="${this.inputId}"
                            type="text" 
                            class="search-input" 
                            placeholder="Jump to..."
                            aria-label="Jump to"
                            aria-describedby="${this.instructionsId}"
                            role="combobox"
                            aria-autocomplete="list"
                            aria-controls="${this.listboxId}"
                            aria-expanded="false"
                            autocomplete="off"
                            spellcheck="false"
                        >
                        <button type="button" class="close-search" aria-label="Close jump menu">&times;</button>
                    </div>
                    <div class="modal-body results-container"></div>
                    <div class="hint-text">
                        <span><kbd>↑</kbd> <kbd>↓</kbd> Navigate</span>
                        <span><kbd>Enter</kbd> Select</span>
                        <span><kbd>Esc</kbd> Close</span>
                    </div>
                </div>
            </dialog></datasette-modal>
        `;
  }

  setupEventListeners() {
    const dialog = this.querySelector("dialog");
    const input = this.querySelector(".search-input");
    const closeButton = this.querySelector(".close-search");
    const resultsContainer = this.querySelector(".results-container");

    // Global keyboard listener for "/"
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && !this.isInputFocused() && !dialog.open) {
        e.preventDefault();
        this.openMenu();
      }
    });

    document.addEventListener("click", (e) => {
      const trigger = e.target.closest("[data-navigation-search-open]");
      if (trigger) {
        e.preventDefault();
        const details = trigger.closest("details");
        const restoreTarget = details?.querySelector("summary") || trigger;
        details?.removeAttribute("open");
        this.openMenu(restoreTarget);
      }
    });

    // Input event
    input.addEventListener("input", (e) => {
      this.handleSearch(e.target.value);
    });

    // Keyboard navigation
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        this.moveSelection(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        this.moveSelection(-1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        this.selectCurrentItem();
      }
    });

    closeButton.addEventListener("click", () => {
      this.closeMenu();
    });

    // Click on result item
    resultsContainer.addEventListener("click", (e) => {
      const clearRecent = e.target.closest("[data-clear-recent-items]");
      if (clearRecent) {
        e.preventDefault();
        this.clearRecentItems();
        return;
      }

      const item = e.target.closest(".result-item");
      if (item) {
        const index = parseInt(item.dataset.index);
        this.selectItem(index);
      }
    });

    dialog.addEventListener("close", () => {
      this.onMenuClosed();
    });

    // Initial load
    this.loadInitialData();
  }

  isInputFocused() {
    const activeElement = document.activeElement;
    return (
      activeElement &&
      (activeElement.tagName === "INPUT" ||
        activeElement.tagName === "TEXTAREA" ||
        activeElement.isContentEditable)
    );
  }

  setElementAttribute(element, name, value) {
    if (!element) {
      return;
    }
    if (typeof element.setAttribute === "function") {
      element.setAttribute(name, value);
    } else {
      element[name] = String(value);
    }
  }

  removeElementAttribute(element, name) {
    if (!element) {
      return;
    }
    if (typeof element.removeAttribute === "function") {
      element.removeAttribute(name);
    } else {
      delete element[name];
    }
  }

  setNavigationTriggersExpanded(expanded) {
    if (typeof document.querySelectorAll !== "function") {
      return;
    }
    document
      .querySelectorAll("[data-navigation-search-open]")
      .forEach((trigger) => {
        this.setElementAttribute(
          trigger,
          "aria-expanded",
          expanded ? "true" : "false",
        );
      });
  }

  resultOptionId(index) {
    return `${this.listboxId}-option-${index}`;
  }

  updateComboboxState() {
    const dialog = this.querySelector("dialog");
    const input = this.querySelector(".search-input");
    const matches = this.renderedMatches || [];
    this.setElementAttribute(
      input,
      "aria-expanded",
      dialog && dialog.open && matches.length > 0 ? "true" : "false",
    );

    if (
      dialog &&
      dialog.open &&
      this.selectedIndex >= 0 &&
      this.selectedIndex < matches.length
    ) {
      this.setElementAttribute(
        input,
        "aria-activedescendant",
        this.resultOptionId(this.selectedIndex),
      );
    } else {
      this.removeElementAttribute(input, "aria-activedescendant");
    }
  }

  setStatus(message) {
    const status = this.querySelector(`#${this.statusId}`);
    if (status) {
      status.textContent = message || "";
    }
  }

  resultsStatus(count, truncated) {
    if (truncated) {
      return "More than 100 results. Keep typing to narrow the list.";
    }
    if (count === 0) {
      return "No results found.";
    }
    if (count === 1) {
      return "1 result.";
    }
    return `${count} results.`;
  }

  loadInitialData() {
    const itemsAttr = this.getAttribute("items");
    if (itemsAttr) {
      try {
        this.allItems = JSON.parse(itemsAttr);
        this.matches = this.allItems;
      } catch (e) {
        console.error("Failed to parse items attribute:", e);
        this.allItems = [];
        this.matches = [];
      }
    }
  }

  handleSearch(query) {
    clearTimeout(this.debounceTimer);
    if (query.trim()) {
      this.setStatus("Searching...");
    } else {
      this.setStatus("");
    }

    this.debounceTimer = setTimeout(() => {
      const url = this.getAttribute("url");

      if (url) {
        // Fetch from API
        this.fetchResults(url, query);
      } else {
        // Filter local items
        this.filterLocalItems(query);
      }
    }, 200);
  }

  async fetchResults(url, query) {
    try {
      const searchUrl = `${url}?q=${encodeURIComponent(query)}`;
      const response = await fetch(searchUrl);
      const data = await response.json();
      this.matches = data.matches || [];
      this.selectedIndex = this.matches.length > 0 ? 0 : -1;
      this.renderResults();
      if (query.trim()) {
        this.setStatus(this.resultsStatus(this.matches.length, data.truncated));
      } else {
        this.setStatus("");
      }
    } catch (e) {
      console.error("Failed to fetch search results:", e);
      this.matches = [];
      this.renderResults();
      this.setStatus("Search failed.");
    }
  }

  filterLocalItems(query) {
    if (!query.trim()) {
      this.matches = this.allItems || [];
    } else {
      const lowerQuery = query.toLowerCase();
      this.matches = (this.allItems || []).filter(
        (item) =>
          item.name.toLowerCase().includes(lowerQuery) ||
          (item.display_name || "").toLowerCase().includes(lowerQuery) ||
          item.url.toLowerCase().includes(lowerQuery),
      );
    }
    this.selectedIndex = this.matches.length > 0 ? 0 : -1;
    this.renderResults();
    if (query.trim()) {
      this.setStatus(this.resultsStatus(this.matches.length, false));
    } else {
      this.setStatus("");
    }
  }

  recentItemsStorageKey() {
    return "datasette.navigationSearch.recentItems";
  }

  loadRecentItems() {
    if (typeof localStorage === "undefined") {
      return [];
    }

    try {
      const raw = localStorage.getItem(this.recentItemsStorageKey());
      if (!raw) {
        return [];
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed
        .filter((item) => item && item.name && item.url)
        .map((item) => ({
          name: String(item.name),
          display_name: item.display_name ? String(item.display_name) : "",
          url: String(item.url),
          type: item.type ? String(item.type) : "",
          description: item.description ? String(item.description) : "",
        }))
        .slice(0, 5);
    } catch (e) {
      return [];
    }
  }

  saveRecentItem(match) {
    if (
      typeof localStorage === "undefined" ||
      !match ||
      !match.name ||
      !match.url
    ) {
      return;
    }

    try {
      const item = {
        name: String(match.name),
        display_name: match.display_name ? String(match.display_name) : "",
        url: String(match.url),
        type: match.type ? String(match.type) : "",
        description: match.description ? String(match.description) : "",
      };
      const recentItems = this.loadRecentItems().filter(
        (recentItem) => recentItem.url !== item.url,
      );
      localStorage.setItem(
        this.recentItemsStorageKey(),
        JSON.stringify([item, ...recentItems].slice(0, 5)),
      );
    } catch (e) {
      // localStorage may be unavailable, full, or disabled.
    }
  }

  clearRecentItems() {
    if (typeof localStorage === "undefined") {
      return;
    }

    try {
      localStorage.removeItem(this.recentItemsStorageKey());
    } catch (e) {
      localStorage.setItem(this.recentItemsStorageKey(), "[]");
    }
    this.renderResults();
    this.setStatus("Recent items cleared.");
  }

  jumpSections() {
    const manager = window.__DATASETTE__;
    if (!manager || typeof manager.makeJumpSections !== "function") {
      return [];
    }
    const sections = manager.makeJumpSections({
      navigationSearch: this,
    });
    return Array.isArray(sections)
      ? sections.filter(
          (section) => section && typeof section.render === "function",
        )
      : [];
  }

  jumpSectionsHtml(jumpSections) {
    return jumpSections
      .map((section, index) => {
        const id = section.id
          ? ` data-jump-section-id="${this.escapeHtml(section.id)}"`
          : "";
        return `<div class="jump-start-content" data-jump-section-index="${index}"${id}></div>`;
      })
      .join("");
  }

  renderJumpSections(container, jumpSections) {
    jumpSections.forEach((section, index) => {
      const node = container.querySelector(
        `[data-jump-section-index="${index}"]`,
      );
      if (!node) {
        return;
      }
      section.render(node, {
        navigationSearch: this,
        container,
        input: this.querySelector(".search-input"),
      });
    });
  }

  resultItemHtml(match, index) {
    const displayName = match.display_name || match.name;
    const label =
      match.display_name && match.display_name !== match.name
        ? `<div class="result-label">${this.escapeHtml(match.name)}</div>`
        : "";
    const type = match.type
      ? `<div class="result-type">${this.escapeHtml(match.type)}</div>`
      : "";
    const description = match.description
      ? `<div class="result-description">${this.escapeHtml(
          match.description,
        )}</div>`
      : "";
    return `
            <div
                id="${this.resultOptionId(index)}"
                class="result-item ${index === this.selectedIndex ? "selected" : ""}"
                data-index="${index}"
                role="option"
                aria-selected="${index === this.selectedIndex}"
            >
                <div>
                    ${type}
                    <div class="result-name">${this.escapeHtml(displayName)}</div>
                    ${label}
                    <div class="result-url">${this.escapeHtml(match.url)}</div>
                    ${description}
                </div>
            </div>
        `;
  }

  renderResults() {
    const container = this.querySelector(".results-container");
    const input = this.querySelector(".search-input");
    const showStartContent = !input.value.trim();
    const jumpSections = showStartContent ? this.jumpSections() : [];
    const startBlock = showStartContent
      ? this.jumpSectionsHtml(jumpSections)
      : "";
    const recentItems = showStartContent ? this.loadRecentItems() : [];
    const defaultMatches = showStartContent ? [] : this.matches;
    const renderedMatches = [...recentItems, ...defaultMatches];
    this.renderedMatches = renderedMatches;
    const emptyListbox = `<div id="${this.listboxId}" class="results-list" role="listbox" aria-label="Jump results"></div>`;

    if (renderedMatches.length) {
      if (
        this.selectedIndex < 0 ||
        this.selectedIndex >= renderedMatches.length
      ) {
        this.selectedIndex = 0;
      }
    } else {
      this.selectedIndex = -1;
    }

    if (renderedMatches.length === 0) {
      if (startBlock) {
        container.innerHTML = startBlock + emptyListbox;
        this.renderJumpSections(container, jumpSections);
      } else if (showStartContent) {
        container.innerHTML = emptyListbox;
      } else {
        const message = input.value.trim()
          ? "No results found"
          : "Start typing to search...";
        container.innerHTML = `${emptyListbox}<div class="no-results">${message}</div>`;
      }
      this.updateComboboxState();
      return;
    }

    const recentHeading = recentItems.length
      ? `<div class="results-heading" id="${this.recentHeadingId}">Recent</div>`
      : "";
    const recentGroup = recentItems.length
      ? `<div role="group" aria-labelledby="${this.recentHeadingId}">${recentItems
          .map((match, index) => this.resultItemHtml(match, index))
          .join("")}</div>`
      : "";
    const recentActions = recentItems.length
      ? `<div class="recent-actions"><button type="button" class="clear-recent" data-clear-recent-items>Clear recent</button></div>`
      : "";
    const defaultHtml = defaultMatches
      .map((match, index) =>
        this.resultItemHtml(match, recentItems.length + index),
      )
      .join("");
    container.innerHTML =
      startBlock +
      recentHeading +
      `<div id="${this.listboxId}" class="results-list" role="listbox" aria-label="Jump results">${recentGroup}${defaultHtml}</div>` +
      recentActions;
    this.renderJumpSections(container, jumpSections);
    this.updateComboboxState();

    // Scroll selected item into view
    if (this.selectedIndex >= 0) {
      const selectedItem = container.querySelector(
        `.result-item[data-index="${this.selectedIndex}"]`,
      );
      if (selectedItem) {
        selectedItem.scrollIntoView({ block: "nearest" });
      }
    }
  }

  moveSelection(direction) {
    const matches = this.renderedMatches || this.matches;
    const newIndex = this.selectedIndex + direction;
    if (newIndex >= 0 && newIndex < matches.length) {
      this.selectedIndex = newIndex;
      this.renderResults();
    }
  }

  selectCurrentItem() {
    const matches = this.renderedMatches || this.matches;
    if (this.selectedIndex >= 0 && this.selectedIndex < matches.length) {
      this.selectItem(this.selectedIndex);
    }
  }

  selectItem(index) {
    const matches = this.renderedMatches || this.matches;
    const match = matches[index];
    if (match) {
      this.saveRecentItem(match);

      // Dispatch custom event
      this.dispatchEvent(
        new CustomEvent("select", {
          detail: match,
          bubbles: true,
          composed: true,
        }),
      );

      // Navigate to URL
      window.location.href = match.url;

      this.closeMenu({ restoreFocus: false });
    }
  }

  openMenu(trigger) {
    const input = this.querySelector(".search-input");

    this.querySelector("datasette-modal").show({
      trigger,
      initialFocus: input,
    });
    this.setNavigationTriggersExpanded(true);
    input.value = "";

    // Reset state, then populate the default jump list.
    this.matches = [];
    this.selectedIndex = -1;
    this.renderResults();
    this.setStatus("");
  }

  closeMenu(options = {}) {
    this.querySelector("datasette-modal").close(options);
  }

  onMenuClosed() {
    const input = this.querySelector(".search-input");
    this.setElementAttribute(input, "aria-expanded", "false");
    this.removeElementAttribute(input, "aria-activedescendant");
    this.setNavigationTriggersExpanded(false);
    this.setStatus("");
  }

  escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : text;
    return div.innerHTML;
  }
}

// Register the custom element
customElements.define("navigation-search", NavigationSearch);
