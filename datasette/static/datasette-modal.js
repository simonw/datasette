/**
 * <datasette-modal> is Datasette's shared modal dialog Web Component.
 *
 * This element, and the DatasetteModal class exposed as
 * window.DatasetteModal, are part of Datasette's public JavaScript API
 * for plugins. See the "Modal dialogs" section of the JavaScript
 * plugins documentation.
 *
 * The component wraps a native <dialog> element and provides:
 *
 * - The standard Datasette modal frame: sizing, rounded corners,
 *   backdrop, animations, and optional .modal-header scaffolding
 * - Close-on-backdrop-click and Escape key handling
 * - A `busy` property that blocks user-initiated dismissal while an
 *   operation is in flight
 * - A `closeGuard` hook for "discard unsaved changes?" style prompts
 * - Focus restoration to the triggering element on close
 * - `datasette-modal-open` and `datasette-modal-close` events
 *
 * Markup structure once connected:
 *
 *   <datasette-modal modal-title="Example">
 *     <dialog id class aria-labelledby>
 *       <div class="modal-header">
 *         <span class="modal-title">Example</span>
 *         <span class="modal-meta" hidden></span>
 *       </div>
 *       ...consumer content, typically ending in a .modal-footer...
 *     </dialog>
 *   </datasette-modal>
 *
 * The component uses light DOM so page CSS and plugins can style the
 * dialog contents. The shared frame styles are distributed via a
 * stylesheet that the component adopts into whatever document or
 * shadow root it is connected to, which means the component also
 * works inside the shadow DOM of other web components.
 */
(function () {
  var FRAME_CSS = `
datasette-modal {
    display: contents;
}

@keyframes datasette-modal-slide-in {
    from {
        opacity: 0;
        transform: translateY(-20px) scale(0.95);
    }
    to {
        opacity: 1;
        transform: translateY(0) scale(1);
    }
}

@keyframes datasette-modal-fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
}

datasette-modal > dialog {
    --ink: #0f0f0f;
    --paper: #eef6ff;
    --muted: #6b6b6b;
    --rule: #d8e6f5;
    --accent: #1a56db;
    --card: #ffffff;
    border: none;
    border-radius: var(--datasette-modal-border-radius, var(--modal-border-radius, 0.75rem));
    padding: 0;
    margin: auto;
    width: var(--datasette-modal-width, min(520px, calc(100vw - 32px)));
    max-width: 95vw;
    max-height: var(--datasette-modal-max-height, min(720px, calc(100vh - 32px)));
    box-shadow: var(--modal-shadow, 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04));
    animation: datasette-modal-slide-in var(--modal-animation-duration, 0.2s) ease-out;
    overflow: hidden;
    font-family: system-ui, -apple-system, sans-serif;
    background: var(--card);
    color: var(--ink);
}

datasette-modal > dialog[open] {
    display: flex;
    flex-direction: column;
}

datasette-modal > dialog::backdrop {
    background: var(--modal-backdrop-bg, rgba(0, 0, 0, 0.5));
    backdrop-filter: var(--modal-backdrop-blur, blur(4px));
    -webkit-backdrop-filter: var(--modal-backdrop-blur, blur(4px));
    animation: datasette-modal-fade-in var(--modal-animation-duration, 0.2s) ease-out;
}

datasette-modal .modal-header {
    padding: 20px 24px 14px;
    border-bottom: 1px solid var(--rule);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-shrink: 0;
    min-width: 0;
}

datasette-modal .modal-title {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    min-width: 0;
    max-width: 100%;
    font-size: 1rem;
    font-weight: 600;
    color: var(--ink);
}

datasette-modal .modal-meta {
    font-family: ui-monospace, monospace;
    font-size: 0.7rem;
    color: var(--muted);
    background: var(--paper);
    padding: 3px 9px;
    border-radius: 20px;
    flex-shrink: 0;
}

datasette-modal .modal-meta[hidden] {
    display: none;
}

datasette-modal .modal-footer {
    padding: 14px 20px;
    border-top: 1px solid var(--rule);
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    flex-shrink: 0;
    background: var(--paper);
}

datasette-modal .modal-footer [hidden] {
    display: none;
}

datasette-modal .footer-info {
    flex: 1;
    font-family: ui-monospace, monospace;
    font-size: 0.68rem;
    color: var(--muted);
}

datasette-modal .btn {
    border: none;
    border-radius: 5px;
    padding: 9px 20px;
    font-size: 0.85rem;
    font-weight: 500;
    cursor: pointer;
    touch-action: manipulation;
    font-family: inherit;
    transition: background 0.12s;
}

datasette-modal .btn-ghost {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--rule);
}

datasette-modal .btn-ghost:hover {
    background: var(--rule);
    color: var(--ink);
}

datasette-modal .btn-primary {
    background: var(--accent);
    color: #fff;
}

datasette-modal .btn-primary:hover {
    background: #1949b8;
}

datasette-modal .btn-primary:disabled,
datasette-modal .btn-primary:disabled:hover {
    background: #a0aec0;
    color: #fff;
}

datasette-modal .btn-danger {
    background: #b91c1c;
    color: #fff;
    margin-right: auto;
}

datasette-modal .btn-danger:hover {
    background: #991b1b;
}

datasette-modal .btn-danger:disabled,
datasette-modal .btn-danger:disabled:hover {
    background: #d98c8c;
    color: #fff;
}

datasette-modal .btn:disabled {
    opacity: 0.55;
    cursor: not-allowed;
}

@media (max-width: 640px) {
    datasette-modal > dialog {
        width: var(--datasette-modal-small-screen-width, 95vw);
        max-height: var(--datasette-modal-small-screen-max-height, 85vh);
        border-radius: 0.5rem;
    }

    datasette-modal .modal-header {
        padding-left: 18px;
        padding-right: 18px;
    }

    datasette-modal .modal-footer {
        padding-left: 18px;
        padding-right: 18px;
    }
}
`;

  var sharedFrameSheet = null;
  var styledRoots = new WeakSet();
  var titleIdCounter = 0;

  /* Make the shared frame styles available in a document or shadow root */
  function adoptFrameStyles(rootNode) {
    if (!rootNode || styledRoots.has(rootNode)) {
      return;
    }
    styledRoots.add(rootNode);
    if (
      typeof CSSStyleSheet !== "undefined" &&
      "adoptedStyleSheets" in rootNode
    ) {
      try {
        if (!sharedFrameSheet) {
          sharedFrameSheet = new CSSStyleSheet();
          sharedFrameSheet.replaceSync(FRAME_CSS);
        }
        rootNode.adoptedStyleSheets =
          rootNode.adoptedStyleSheets.concat(sharedFrameSheet);
        return;
      } catch (_error) {
        // Fall back to a <style> element below
      }
    }
    var style = document.createElement("style");
    style.setAttribute("data-datasette-modal", "");
    style.textContent = FRAME_CSS;
    (rootNode.head || rootNode).appendChild(style);
  }

  class DatasetteModal extends HTMLElement {
    static get observedAttributes() {
      return ["modal-title", "modal-meta"];
    }

    /** True if the browser supports everything the component needs */
    static get supported() {
      return typeof window.HTMLDialogElement !== "undefined";
    }

    /**
     * Create a <datasette-modal>, append it to the document (or
     * options.parent) and return it. Returns null in browsers without
     * <dialog> support.
     *
     * Options:
     * - id: id attribute for the inner <dialog>
     * - className: class attribute for the inner <dialog>
     * - title: text for the standard header title (omit for no header)
     * - meta: text for the header meta chip
     * - titleId: id for the title element (defaults to "<id>-title")
     * - labelledBy: aria-labelledby override for the dialog
     * - describedBy: aria-describedby for the dialog
     * - content: HTML string or DOM node placed after the header
     * - parent: element to append to (defaults to document.body)
     */
    static create(options) {
      options = options || {};
      if (!DatasetteModal.supported) {
        return null;
      }
      var modal = document.createElement("datasette-modal");
      if (options.id) {
        modal.setAttribute("dialog-id", options.id);
      }
      if (options.className) {
        modal.setAttribute("dialog-class", options.className);
      }
      if (options.title !== undefined && options.title !== null) {
        modal.setAttribute("modal-title", options.title);
      }
      if (options.meta !== undefined && options.meta !== null) {
        modal.setAttribute("modal-meta", options.meta);
      }
      if (options.titleId) {
        modal.setAttribute("title-id", options.titleId);
      }
      if (options.labelledBy) {
        modal.setAttribute("labelled-by", options.labelledBy);
      }
      if (options.describedBy) {
        modal.setAttribute("described-by", options.describedBy);
      }
      if (options.content !== undefined && options.content !== null) {
        if (typeof options.content === "string") {
          modal.innerHTML = options.content;
        } else {
          modal.appendChild(options.content);
        }
      }
      (options.parent || document.body).appendChild(modal);
      return modal;
    }

    constructor() {
      super();
      this._dialog = null;
      this._titleElement = null;
      this._metaElement = null;
      this._trigger = null;
      this._restoreFocus = true;
      this._busy = false;

      /**
       * Optional function called with a reason string ("escape",
       * "backdrop" or the reason passed to requestClose()) when the
       * user tries to dismiss the modal. Return false to keep the
       * modal open. Not called for direct close() calls.
       */
      this.closeGuard = null;
    }

    connectedCallback() {
      adoptFrameStyles(this.getRootNode());
      this._build();
    }

    attributeChangedCallback(name) {
      if (!this._dialog) {
        return;
      }
      if (name === "modal-title" && this._titleElement) {
        this._titleElement.textContent = this.getAttribute("modal-title") || "";
      }
      if (name === "modal-meta" && this._metaElement) {
        this._syncMeta();
      }
    }

    /** The underlying HTMLDialogElement, or null if unsupported */
    get dialog() {
      return this._dialog;
    }

    /** True if the modal is currently open */
    get open() {
      return !!(this._dialog && this._dialog.open);
    }

    /** The .modal-title element, or null if there is no header */
    get titleElement() {
      return this._titleElement;
    }

    /** The .modal-meta element, or null if there is no header */
    get metaElement() {
      return this._metaElement;
    }

    /**
     * While true, Escape, backdrop clicks and requestClose() will not
     * close the modal. Use this while a save or delete is in flight.
     */
    get busy() {
      return this._busy;
    }

    set busy(value) {
      this._busy = !!value;
      this.toggleAttribute("busy", this._busy);
    }

    /** Set the header title text */
    setTitle(text) {
      this.setAttribute("modal-title", text == null ? "" : text);
    }

    /** Set the header meta chip text - blank hides the chip */
    setMeta(text) {
      this.setAttribute("modal-meta", text == null ? "" : text);
    }

    /**
     * Open the modal. Records options.trigger (defaults to the
     * currently focused element) so focus can be restored on close.
     */
    showModal(options) {
      options = options || {};
      if (!this.isConnected) {
        document.body.appendChild(this);
      }
      if (!this._dialog) {
        return;
      }
      if (options.trigger !== undefined) {
        this._trigger = options.trigger;
      } else if (!this._dialog.open) {
        var active = document.activeElement;
        this._trigger = active && active !== document.body ? active : null;
      }
      this._restoreFocus = true;
      if (!this._dialog.open) {
        this._dialog.showModal();
        this.dispatchEvent(
          new CustomEvent("datasette-modal-open", {
            bubbles: true,
            composed: true,
          }),
        );
      }
    }

    /**
     * Close the modal unconditionally, skipping busy and closeGuard.
     * Pass {restoreFocus: false} to leave focus where it is.
     */
    close(options) {
      options = options || {};
      if (!this._dialog) {
        return;
      }
      this._restoreFocus = options.restoreFocus !== false;
      if (this._dialog.open) {
        this._dialog.close();
      }
    }

    /**
     * Ask the modal to close on the user's behalf. Does nothing while
     * busy, and consults closeGuard if one is set. Returns true if the
     * modal was closed.
     */
    requestClose(reason) {
      if (!this._dialog || !this._dialog.open || this._busy) {
        return false;
      }
      if (
        typeof this.closeGuard === "function" &&
        !this.closeGuard(reason || "dismiss")
      ) {
        return false;
      }
      this.close();
      return true;
    }

    _build() {
      if (this._dialog || !DatasetteModal.supported) {
        return;
      }

      var dialog = document.createElement("dialog");
      var dialogId = this.getAttribute("dialog-id");
      if (dialogId) {
        dialog.id = dialogId;
      }
      var dialogClass = this.getAttribute("dialog-class");
      if (dialogClass) {
        dialog.className = dialogClass;
      }

      // Move any existing light DOM content into the dialog
      var content = document.createDocumentFragment();
      while (this.firstChild) {
        content.appendChild(this.firstChild);
      }

      if (this.hasAttribute("modal-title")) {
        var header = document.createElement("div");
        header.className = "modal-header";

        this._titleElement = document.createElement("span");
        this._titleElement.className = "modal-title";
        this._titleElement.id =
          this.getAttribute("title-id") ||
          (dialogId
            ? dialogId + "-title"
            : "datasette-modal-title-" + ++titleIdCounter);
        this._titleElement.textContent = this.getAttribute("modal-title");

        this._metaElement = document.createElement("span");
        this._metaElement.className = "modal-meta";

        header.appendChild(this._titleElement);
        header.appendChild(this._metaElement);
        dialog.appendChild(header);
        this._syncMeta();
      }

      var labelledBy =
        this.getAttribute("labelled-by") ||
        (this._titleElement ? this._titleElement.id : null);
      if (labelledBy) {
        dialog.setAttribute("aria-labelledby", labelledBy);
      }
      var describedBy = this.getAttribute("described-by");
      if (describedBy) {
        dialog.setAttribute("aria-describedby", describedBy);
      }

      dialog.appendChild(content);
      this.appendChild(dialog);
      this._dialog = dialog;

      dialog.addEventListener("click", (ev) => {
        if (ev.target === dialog) {
          this.requestClose("backdrop");
        }
      });

      dialog.addEventListener("keydown", (ev) => {
        if (ev.key !== "Escape") {
          return;
        }
        ev.preventDefault();
        this.requestClose("escape");
      });

      dialog.addEventListener("cancel", (ev) => {
        ev.preventDefault();
        this.requestClose("escape");
      });

      dialog.addEventListener("close", () => {
        var restoreFocus = this._restoreFocus;
        this._restoreFocus = true;
        if (
          restoreFocus &&
          this._trigger &&
          this._trigger.isConnected &&
          typeof this._trigger.focus === "function"
        ) {
          this._trigger.focus();
        }
        this.dispatchEvent(
          new CustomEvent("datasette-modal-close", {
            bubbles: true,
            composed: true,
          }),
        );
      });
    }

    _syncMeta() {
      var meta = this.getAttribute("modal-meta") || "";
      this._metaElement.textContent = meta;
      this._metaElement.hidden = meta === "";
    }
  }

  customElements.define("datasette-modal", DatasetteModal);
  window.DatasetteModal = DatasetteModal;
})();
