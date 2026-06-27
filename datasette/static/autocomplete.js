(function () {
  function autocompleteValueFromRow(row) {
    var pks = (row && row.pks) || {};
    var keys = Object.keys(pks);
    if (!keys.length) {
      return "";
    }
    if (keys.length === 1) {
      return String(pks[keys[0]]);
    }
    return keys
      .map(function (key) {
        return key + "=" + pks[key];
      })
      .join(", ");
  }

  function autocompleteLabelFromRow(row) {
    var value = autocompleteValueFromRow(row);
    if (row.label && String(row.label) !== value) {
      return row.label + " (" + value + ")";
    }
    return value;
  }

  if (!window.customElements || customElements.get("datasette-autocomplete")) {
    return;
  }

  class DatasetteAutocomplete extends HTMLElement {
    constructor() {
      super();
      this.input = null;
      this.listbox = null;
      this.status = null;
      this.results = [];
      this.activeIndex = -1;
      this.fetchId = 0;
      this.searchTimer = null;
      this.boundInput = this.handleInput.bind(this);
      this.boundKeydown = this.handleKeydown.bind(this);
      this.boundBlur = this.handleBlur.bind(this);
      this.boundFocus = this.handleFocus.bind(this);
      this.boundPositionListbox = this.positionListbox.bind(this);
    }

    connectedCallback() {
      if (this.input) {
        return;
      }
      this.input = this.querySelector("input");
      if (!this.input) {
        return;
      }

      var inputId =
        this.input.id ||
        "datasette-autocomplete-" + Math.random().toString(36).slice(2);
      this.input.id = inputId;
      var listboxId = inputId + "-listbox";
      var statusId = inputId + "-status";

      this.classList.add("datasette-autocomplete");
      this.input.setAttribute("role", "combobox");
      this.input.setAttribute("aria-autocomplete", "list");
      this.input.setAttribute("aria-expanded", "false");
      this.input.setAttribute("aria-controls", listboxId);
      this.input.setAttribute("autocomplete", "off");

      this.listbox = document.createElement("div");
      this.listbox.className = "datasette-autocomplete-list";
      this.listbox.id = listboxId;
      this.listbox.setAttribute("role", "listbox");
      this.listbox.hidden = true;

      this.status = document.createElement("span");
      this.status.className = "datasette-autocomplete-status";
      this.status.id = statusId;
      this.status.setAttribute("role", "status");
      this.status.setAttribute("aria-live", "polite");

      this.input.setAttribute(
        "aria-describedby",
        [this.input.getAttribute("aria-describedby"), statusId]
          .filter(Boolean)
          .join(" "),
      );

      this.appendChild(this.listbox);
      this.appendChild(this.status);

      this.input.addEventListener("input", this.boundInput);
      this.input.addEventListener("keydown", this.boundKeydown);
      this.input.addEventListener("blur", this.boundBlur);
      this.input.addEventListener("focus", this.boundFocus);
    }

    disconnectedCallback() {
      if (!this.input) {
        return;
      }
      this.input.removeEventListener("input", this.boundInput);
      this.input.removeEventListener("keydown", this.boundKeydown);
      this.input.removeEventListener("blur", this.boundBlur);
      this.input.removeEventListener("focus", this.boundFocus);
    }

    handleInput() {
      this.scheduleSearch();
    }

    handleFocus() {
      if (this.input.value.trim() || this.hasAttribute("suggest-on-focus")) {
        this.scheduleSearch();
      }
    }

    handleBlur() {
      window.setTimeout(() => this.close(), 150);
    }

    handleKeydown(ev) {
      if (ev.key === "Escape") {
        if (!this.listbox.hidden) {
          ev.preventDefault();
          this.close();
        }
        return;
      }
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        if (this.listbox.hidden) {
          this.scheduleSearch();
        } else {
          this.setActiveIndex(this.activeIndex + 1);
        }
        return;
      }
      if (ev.key === "ArrowUp") {
        ev.preventDefault();
        if (!this.listbox.hidden) {
          this.setActiveIndex(this.activeIndex - 1);
        }
        return;
      }
      if (ev.key === "Enter" && !this.listbox.hidden && this.activeIndex >= 0) {
        ev.preventDefault();
        this.chooseIndex(this.activeIndex);
      }
    }

    scheduleSearch() {
      window.clearTimeout(this.searchTimer);
      this.searchTimer = window.setTimeout(() => this.search(), 150);
    }

    async search() {
      var query = this.input.value.trim();
      var initial = !query && this.hasAttribute("suggest-on-focus");
      if (!query && !initial) {
        this.close();
        this.status.textContent = "";
        return;
      }
      var src = this.getAttribute("src");
      if (!src) {
        return;
      }

      var url = new URL(src, location.href);
      url.searchParams.set("q", query);
      if (initial) {
        url.searchParams.set("_initial", "1");
      } else {
        url.searchParams.delete("_initial");
      }
      var fetchId = this.fetchId + 1;
      this.fetchId = fetchId;
      this.status.textContent = "Searching...";

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
        if (fetchId !== this.fetchId) {
          return;
        }
        this.results = (data && data.rows) || [];
        this.render();
      } catch (_error) {
        if (fetchId !== this.fetchId) {
          return;
        }
        this.results = [];
        this.close();
        this.status.textContent = "Could not load suggestions";
      }
    }

    render() {
      this.listbox.textContent = "";
      this.activeIndex = -1;
      if (!this.results.length) {
        this.close();
        this.status.textContent = "No matches";
        return;
      }

      this.results.forEach((row, index) => {
        var option = document.createElement("div");
        option.className = "datasette-autocomplete-option";
        option.id = this.input.id + "-option-" + index;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        option.dataset.index = String(index);
        option.dataset.value = autocompleteValueFromRow(row);
        option.textContent = autocompleteLabelFromRow(row);
        option.addEventListener("mousedown", (ev) => {
          ev.preventDefault();
          this.chooseIndex(index);
        });
        this.listbox.appendChild(option);
      });

      this.listbox.hidden = false;
      this.input.setAttribute("aria-expanded", "true");
      this.status.textContent =
        this.results.length + (this.results.length === 1 ? " match" : " matches");
      this.positionListbox();
      this.setActiveIndex(0);
    }

    positionListbox() {
      if (!this.input || !this.listbox || this.listbox.hidden) {
        return;
      }

      var gap = 3;
      var margin = 8;
      var inputRect = this.input.getBoundingClientRect();
      this.listbox.style.maxHeight = "";
      var defaultMaxHeight = parseFloat(
        window.getComputedStyle(this.listbox).maxHeight,
      );
      if (!Number.isFinite(defaultMaxHeight)) {
        defaultMaxHeight = 256;
      }
      var scrollHeight = Math.ceil(this.listbox.scrollHeight);
      var desiredHeight = Math.min(scrollHeight, defaultMaxHeight);
      var availableBelow = Math.max(
        0,
        (window.innerHeight || document.documentElement.clientHeight) -
          inputRect.bottom -
          gap -
          margin,
      );

      this.listbox.style.left = inputRect.left + "px";
      this.listbox.style.top = inputRect.bottom + gap + "px";
      this.listbox.style.width = inputRect.width + "px";
      if (scrollHeight <= defaultMaxHeight && scrollHeight <= availableBelow) {
        this.listbox.style.maxHeight = "none";
      } else {
        this.listbox.style.maxHeight =
          Math.min(defaultMaxHeight, desiredHeight, availableBelow || defaultMaxHeight) +
          "px";
      }
      window.addEventListener("resize", this.boundPositionListbox);
      document.addEventListener("scroll", this.boundPositionListbox, true);
    }

    setActiveIndex(index) {
      var options = this.listbox.querySelectorAll("[role='option']");
      if (!options.length) {
        this.activeIndex = -1;
        this.input.removeAttribute("aria-activedescendant");
        return;
      }
      if (index < 0) {
        index = options.length - 1;
      }
      if (index >= options.length) {
        index = 0;
      }
      options.forEach((option, optionIndex) => {
        option.setAttribute(
          "aria-selected",
          optionIndex === index ? "true" : "false",
        );
      });
      this.activeIndex = index;
      this.input.setAttribute("aria-activedescendant", options[index].id);
    }

    chooseIndex(index) {
      var row = this.results[index];
      if (!row) {
        return;
      }
      var value = autocompleteValueFromRow(row);
      var label = autocompleteLabelFromRow(row);
      this.input.value = value;
      this.input.dispatchEvent(new Event("change", { bubbles: true }));
      this.close();
      this.status.textContent = "Selected " + label;
      this.dispatchEvent(
        new CustomEvent("datasette-autocomplete-select", {
          bubbles: true,
          detail: {
            row: row,
            value: value,
            label: label,
          },
        }),
      );
    }

    close() {
      if (this.listbox) {
        this.listbox.hidden = true;
        this.listbox.textContent = "";
        this.listbox.style.left = "";
        this.listbox.style.maxHeight = "";
        this.listbox.style.top = "";
        this.listbox.style.width = "";
      }
      if (this.input) {
        this.input.setAttribute("aria-expanded", "false");
        this.input.removeAttribute("aria-activedescendant");
      }
      window.removeEventListener("resize", this.boundPositionListbox);
      document.removeEventListener("scroll", this.boundPositionListbox, true);
      this.activeIndex = -1;
    }
  }

  customElements.define("datasette-autocomplete", DatasetteAutocomplete);
})();
