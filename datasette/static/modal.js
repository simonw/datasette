// Shared lifecycle for native modal dialogs.
(() => {
  class DatasetteModal extends HTMLElement {
    constructor() {
      super();
      this.beforeClose = null;
      this._busy = false;
      this._restoreFocus = true;
      this._trigger = null;
      this._escapeCleanup = null;
      this._escapeTimer = null;
    }

    static create() {
      const modal = document.createElement("datasette-modal");
      modal.appendChild(document.createElement("dialog"));
      return modal;
    }

    get dialog() {
      return this.querySelector(":scope > dialog");
    }

    get busy() {
      return this._busy;
    }

    set busy(value) {
      this._busy = !!value;
      if (this.dialog) {
        this.dialog.setAttribute("aria-busy", String(this._busy));
      }
    }

    connectedCallback() {
      const dialog = this.dialog;
      if (!dialog) return;
      dialog.classList.add("datasette-modal");
      this._listeners?.abort();
      this._listeners = new AbortController();
      const options = { signal: this._listeners.signal };
      let backdropPointerDown = false;
      const outside = (event) => {
        const rect = dialog.getBoundingClientRect();
        return (
          event.target === dialog &&
          (event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom)
        );
      };
      dialog.addEventListener(
        "pointerdown",
        (event) => {
          backdropPointerDown = outside(event);
        },
        options,
      );
      dialog.addEventListener(
        "click",
        (event) => {
          if (backdropPointerDown && outside(event))
            this.requestClose("backdrop");
          backdropPointerDown = false;
        },
        options,
      );
      dialog.addEventListener(
        "keydown",
        (event) => {
          if (event.key !== "Escape" || event.defaultPrevented) return;
          // A nested native dialog or plugin picker gets first refusal.
          if (event.target.closest("dialog") !== dialog) return;
          event.preventDefault();
          if (this.busy || this._escapeCleanup || this._escapeTimer !== null)
            return;
          // Safari can otherwise use this Escape press to cancel confirm() too.
          // Only keyboard dismissals wait for keyup; native cancel events needn't.
          const onKeyup = (up) => {
            if (up.key !== "Escape") return;
            this._escapeCleanup();
            this._escapeCleanup = null;
            this._escapeTimer = setTimeout(() => {
              this._escapeTimer = null;
              this.requestClose("escape");
            }, 0);
          };
          this.ownerDocument.addEventListener("keyup", onKeyup, true);
          this._escapeCleanup = () =>
            this.ownerDocument.removeEventListener("keyup", onKeyup, true);
        },
        options,
      );
      dialog.addEventListener(
        "cancel",
        (event) => {
          if (event.target !== dialog) return;
          event.preventDefault();
          if (!this._escapeCleanup && this._escapeTimer === null)
            this.requestClose("escape");
        },
        options,
      );
      dialog.addEventListener(
        "close",
        (event) => {
          if (event.target !== dialog || dialog.open) return;
          this._clearPendingClose();
          this.busy = false;
          if (this._restoreFocus && this._trigger?.isConnected) {
            // Menu actions may have become hidden while the dialog was open.
            const details = this._trigger.closest("details:not([open])");
            const target = details?.querySelector("summary") || this._trigger;
            target.focus({ preventScroll: true });
          }
          this._trigger = null;
        },
        options,
      );
    }

    disconnectedCallback() {
      this._listeners?.abort();
      this._clearPendingClose();
      this._trigger = null;
      if (this.dialog?.open) this.dialog.close();
      this.busy = false;
    }

    _clearPendingClose() {
      this._escapeCleanup?.();
      this._escapeCleanup = null;
      clearTimeout(this._escapeTimer);
      this._escapeTimer = null;
    }

    show({ trigger, initialFocus } = {}) {
      const dialog = this.dialog;
      if (!dialog.open) {
        this._clearPendingClose();
        this._trigger = trigger || this.ownerDocument.activeElement;
        this._restoreFocus = true;
        dialog.showModal();
      }
      if (typeof initialFocus === "function") initialFocus();
      else initialFocus?.focus();
    }

    requestClose(reason = "cancel") {
      if (!this.dialog.open || this.busy) return false;
      if (this.beforeClose && this.beforeClose(reason) === false) return false;
      this.close();
      return true;
    }

    close({ restoreFocus = true } = {}) {
      this._clearPendingClose();
      this._restoreFocus = restoreFocus;
      this.dialog.close();
    }
  }

  customElements.define("datasette-modal", DatasetteModal);
  window.DatasetteModal = DatasetteModal;
})();
