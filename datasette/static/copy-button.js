// <datasette-copy-button>: an icon button that copies text to the clipboard.
//
//   <datasette-copy-button value="text to copy" name="id"></datasette-copy-button>
//   <datasette-copy-button for="schema-sql" name="schema"></datasette-copy-button>
//
// Attributes:
//   value - the text to copy. Also available as a .value property.
//   for   - id of an element to copy the text of, instead of value. Form
//           fields are copied by their value, anything else by textContent.
//   name  - what is being copied. Used for the accessible name ("Copy id")
//           and to tell screen readers what happened ("Copied id to
//           clipboard").
//
// The element renders its own <button>, so nothing shows without JavaScript.
// It has a "copied" attribute for the moment the tick is showing. Styles are
// in app.css.
//
// Loaded on every page by base.html, and documented for plugin authors in
// docs/javascript_plugins.rst.
(() => {
  const ICONS =
    '<svg class="icon-copy" aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>' +
    '<svg class="icon-copied" aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  const TITLE = "Copy to clipboard";
  const COPIED_FOR_MS = 1500;

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard
        .writeText(text)
        .catch(() => copyTextFallback(text));
    }
    return copyTextFallback(text);
  }

  // For non-secure contexts (e.g. http:// on a LAN address) or when the
  // async clipboard API is refused
  function copyTextFallback(text) {
    return new Promise((resolve, reject) => {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand("copy") ? resolve() : reject();
      } catch (error) {
        reject(error);
      } finally {
        textarea.remove();
      }
    });
  }

  // One live region is shared by every button on the page. It is added
  // when the first button connects, since a region that is created and
  // filled in the same moment is not reliably announced.
  let status = null;
  function ensureStatus() {
    if (status && status.isConnected) {
      return;
    }
    status = document.createElement("p");
    status.className = "datasette-copy-button-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    document.body.appendChild(status);
  }

  function announce(message) {
    ensureStatus();
    // Clear first so repeating the same message is announced again
    status.textContent = "";
    setTimeout(() => {
      status.textContent = message;
    }, 50);
  }

  class DatasetteCopyButton extends HTMLElement {
    static get observedAttributes() {
      return ["name"];
    }

    constructor() {
      super();
      this._copiedTimer = null;
      this.addEventListener("click", (event) => {
        if (event.target.closest("button")) {
          this.copy();
        }
      });
    }

    get button() {
      return this.querySelector(":scope > button");
    }

    get value() {
      return this.getAttribute("value") || "";
    }

    set value(value) {
      this.setAttribute("value", value);
    }

    // The text a click would copy right now
    get text() {
      const id = this.getAttribute("for");
      if (!id) {
        return this.value;
      }
      const target = document.getElementById(id);
      if (!target) {
        return "";
      }
      const isField =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement;
      return isField ? target.value : target.textContent;
    }

    connectedCallback() {
      ensureStatus();
      if (!this.button) {
        const button = document.createElement("button");
        button.type = "button";
        button.title = TITLE;
        button.innerHTML = ICONS;
        this.appendChild(button);
      }
      this._updateLabel();
    }

    disconnectedCallback() {
      clearTimeout(this._copiedTimer);
      this._setCopied(false);
    }

    attributeChangedCallback() {
      this._updateLabel();
    }

    _updateLabel() {
      const button = this.button;
      if (button) {
        const name = this.getAttribute("name");
        button.setAttribute("aria-label", name ? `Copy ${name}` : TITLE);
      }
    }

    _setCopied(copied) {
      this.toggleAttribute("copied", copied);
      const button = this.button;
      if (button) {
        button.title = copied ? "Copied" : TITLE;
      }
    }

    // Resolves to true if the text was copied
    async copy() {
      const name = this.getAttribute("name");
      try {
        await copyText(this.text);
      } catch (error) {
        announce(name ? `Could not copy ${name}` : "Could not copy");
        return false;
      }
      this._setCopied(true);
      announce(name ? `Copied ${name} to clipboard` : "Copied to clipboard");
      clearTimeout(this._copiedTimer);
      this._copiedTimer = setTimeout(
        () => this._setCopied(false),
        COPIED_FOR_MS,
      );
      return true;
    }
  }

  if (!customElements.get("datasette-copy-button")) {
    customElements.define("datasette-copy-button", DatasetteCopyButton);
  }
})();
