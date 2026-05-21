class NavigationSearch extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.selectedIndex = -1;
    this.matches = [];
    this.debounceTimer = null;

    this.render();
    this.setupEventListeners();
  }

  render() {
    this.shadowRoot.innerHTML = `
            <style>
                :host {
                    display: contents;
                }

                dialog {
                    border: none;
                    border-radius: var(--modal-border-radius, 0.75rem);
                    padding: 0;
                    max-width: 90vw;
                    width: 600px;
                    max-height: 80vh;
                    box-shadow: var(--modal-shadow, 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04));
                    animation: slideIn var(--modal-animation-duration, 0.2s) ease-out;
                }

                dialog::backdrop {
                    background: var(--modal-backdrop-bg, rgba(0, 0, 0, 0.5));
                    backdrop-filter: var(--modal-backdrop-blur, blur(4px));
                    -webkit-backdrop-filter: var(--modal-backdrop-blur, blur(4px));
                    animation: fadeIn var(--modal-animation-duration, 0.2s) ease-out;
                }

                @keyframes slideIn {
                    from {
                        opacity: 0;
                        transform: translateY(-20px) scale(0.95);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0) scale(1);
                    }
                }

                @keyframes fadeIn {
                    from { opacity: 0; }
                    to { opacity: 1; }
                }

                .search-container {
                    display: flex;
                    flex-direction: column;
                }

                .search-input-wrapper {
                    padding: 1.25rem;
                    border-bottom: 1px solid #e5e7eb;
                }

                .search-input {
                    width: 100%;
                    padding: 0.75rem 1rem;
                    font-size: 1rem;
                    border: 2px solid #e5e7eb;
                    border-radius: 0.5rem;
                    outline: none;
                    transition: border-color 0.2s;
                    box-sizing: border-box;
                }

                .search-input:focus {
                    border-color: #2563eb;
                }

                .results-container {
                    overflow-y: auto;
                    height: calc(80vh - 180px);
                    padding: 0.5rem;
                }

                .result-item {
                    padding: 0.875rem 1rem;
                    cursor: pointer;
                    border-radius: 0.5rem;
                    transition: background-color 0.15s;
                    display: flex;
                    align-items: center;
                    gap: 0.75rem;
                }

                .result-item:hover {
                    background-color: #f3f4f6;
                }

                .result-item.selected {
                    background-color: #dbeafe;
                }

                .jump-start-content {
                    border-bottom: 1px solid #e5e7eb;
                    margin-bottom: 0.5rem;
                    padding: 0.5rem 0.5rem 1rem;
                }

                .jump-start-content:empty {
                    display: none;
                }

                .result-name {
                    font-weight: 500;
                    color: #111827;
                }

                .result-label {
                    font-size: 0.875rem;
                    color: #4b5563;
                }

                .result-description {
                    color: #4b5563;
                    font-size: 0.75rem;
                    font-weight: 600;
                    text-transform: uppercase;
                }

                .result-url {
                    font-size: 0.875rem;
                    color: #6b7280;
                }

                .results-heading {
                    color: #4b5563;
                    font-size: 0.75rem;
                    font-weight: 600;
                    letter-spacing: 0;
                    padding: 0.5rem 1rem 0.25rem;
                    text-transform: uppercase;
                }

                .recent-actions {
                    padding: 0.25rem 1rem 0.75rem;
                }

                .clear-recent {
                    background: transparent;
                    border: 0;
                    color: #2563eb;
                    cursor: pointer;
                    font: inherit;
                    font-size: 0.875rem;
                    padding: 0;
                }

                .clear-recent:hover {
                    text-decoration: underline;
                }

                .no-results {
                    padding: 2rem;
                    text-align: center;
                    color: #6b7280;
                }

                .hint-text {
                    padding: 0.75rem 1.25rem;
                    font-size: 0.875rem;
                    color: #6b7280;
                    border-top: 1px solid #e5e7eb;
                    display: flex;
                    gap: 1rem;
                    flex-wrap: wrap;
                }

                .hint-text kbd {
                    background: #f3f4f6;
                    padding: 0.125rem 0.375rem;
                    border-radius: 0.25rem;
                    font-size: 0.75rem;
                    border: 1px solid #d1d5db;
                    font-family: monospace;
                }

                /* Mobile optimizations */
                @media (max-width: 640px) {
                    dialog {
                        width: 95vw;
                        max-height: 85vh;
                        border-radius: 0.5rem;
                    }

                    .search-input-wrapper {
                        padding: 1rem;
                    }

                    .search-input {
                        font-size: 16px; /* Prevents zoom on iOS */
                    }

                    .result-item {
                        padding: 1rem 0.75rem;
                    }

                    .hint-text {
                        font-size: 0.8rem;
                        padding: 0.5rem 1rem;
                    }
                }
            </style>

            <dialog>
                <div class="search-container">
                    <div class="search-input-wrapper">
                        <input 
                            type="text" 
                            class="search-input" 
                            placeholder="Jump to..."
                            aria-label="Jump to"
                            autocomplete="off"
                            spellcheck="false"
                        >
                    </div>
                    <div class="results-container" role="listbox"></div>
                    <div class="hint-text">
                        <span><kbd>↑</kbd> <kbd>↓</kbd> Navigate</span>
                        <span><kbd>Enter</kbd> Select</span>
                        <span><kbd>Esc</kbd> Close</span>
                    </div>
                </div>
            </dialog>
        `;
  }

  setupEventListeners() {
    const dialog = this.shadowRoot.querySelector("dialog");
    const input = this.shadowRoot.querySelector(".search-input");
    const resultsContainer =
      this.shadowRoot.querySelector(".results-container");

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
        trigger.closest("details")?.removeAttribute("open");
        this.openMenu();
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
      } else if (e.key === "Escape") {
        this.closeMenu();
      }
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

    // Close on backdrop click
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) {
        this.closeMenu();
      }
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
    } catch (e) {
      console.error("Failed to fetch search results:", e);
      this.matches = [];
      this.renderResults();
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
    if (typeof localStorage === "undefined" || !match || !match.name || !match.url) {
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
  }

  startContentHtml() {
    const template = this.querySelector("template[data-jump-start]");
    return template ? template.innerHTML.trim() : "";
  }

  resultItemHtml(match, index) {
    const displayName = match.display_name || match.name;
    const label =
      match.display_name && match.display_name !== match.name
        ? `<div class="result-label">${this.escapeHtml(match.name)}</div>`
        : "";
    const description = match.description
      ? `<div class="result-description">${this.escapeHtml(
          match.description,
        )}</div>`
      : "";
    return `
            <div
                class="result-item ${index === this.selectedIndex ? "selected" : ""}"
                data-index="${index}"
                role="option"
                aria-selected="${index === this.selectedIndex}"
            >
                <div>
                    ${description}
                    <div class="result-name">${this.escapeHtml(displayName)}</div>
                    ${label}
                    <div class="result-url">${this.escapeHtml(match.url)}</div>
                </div>
            </div>
        `;
  }

  renderResults() {
    const container = this.shadowRoot.querySelector(".results-container");
    const input = this.shadowRoot.querySelector(".search-input");
    const showStartContent = !input.value.trim();
    const startContent = showStartContent ? this.startContentHtml() : "";
    const startBlock = startContent
      ? `<div class="jump-start-content">${startContent}</div>`
      : "";
    const recentItems = showStartContent ? this.loadRecentItems() : [];
    const defaultMatches = showStartContent ? [] : this.matches;
    const renderedMatches = [...recentItems, ...defaultMatches];
    this.renderedMatches = renderedMatches;

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
        container.innerHTML = startBlock;
      } else if (showStartContent) {
        container.innerHTML = "";
      } else {
        const message = input.value.trim()
          ? "No results found"
          : "Start typing to search...";
        container.innerHTML = `<div class="no-results">${message}</div>`;
      }
      return;
    }

    const recentHtml = recentItems.length
      ? `<div class="results-heading">Recent</div>${recentItems
          .map((match, index) => this.resultItemHtml(match, index))
          .join("")}<div class="recent-actions"><button type="button" class="clear-recent" data-clear-recent-items>Clear recent</button></div>`
      : "";
    const defaultHtml = defaultMatches
      .map((match, index) =>
        this.resultItemHtml(match, recentItems.length + index),
      )
      .join("");
    container.innerHTML = startBlock + recentHtml + defaultHtml;

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

      this.closeMenu();
    }
  }

  openMenu() {
    const dialog = this.shadowRoot.querySelector("dialog");
    const input = this.shadowRoot.querySelector(".search-input");

    if (!dialog.open) {
      dialog.showModal();
    }
    input.value = "";
    input.focus();

    // Reset state, then populate the default jump list.
    this.matches = [];
    this.selectedIndex = -1;
    this.renderResults();
  }

  closeMenu() {
    const dialog = this.shadowRoot.querySelector("dialog");
    dialog.close();
  }

  escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : text;
    return div.innerHTML;
  }
}

// Register the custom element
customElements.define("navigation-search", NavigationSearch);
