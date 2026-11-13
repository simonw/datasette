// Shared lifecycle for native modal dialogs.
(() => {
  let confirmCount = 0;

  class DatasetteModal extends HTMLElement {
    constructor() {
      super();
      this.beforeClose = null;
      this._busy = false;
      this._restoreFocus = true;
      this._returnFocusTo = null;
      this._escapeCleanup = null;
      this._escapeTimer = null;
    }

    static create() {
      const modal = document.createElement("datasette-modal");
      modal.appendChild(document.createElement("dialog"));
      return modal;
    }

    // In-page replacement for window.confirm(), which embedded browsers can
    // dismiss without ever showing it. Resolves to true if the user confirms.
    static confirm({
      message = "",
      confirmLabel = "OK",
      cancelLabel = "Cancel",
      danger = false,
    } = {}) {
      return new Promise((resolve) => {
        const modal = DatasetteModal.create();
        const dialog = modal.dialog;
        confirmCount += 1;
        const messageId = `modal-confirm-message-${confirmCount}`;
        dialog.classList.add("modal-confirm-dialog");
        dialog.setAttribute("role", "alertdialog");
        dialog.setAttribute("aria-labelledby", messageId);

        const text = document.createElement("p");
        text.className = "modal-confirm-message";
        text.id = messageId;
        text.textContent = message;

        const cancelButton = document.createElement("button");
        cancelButton.type = "button";
        cancelButton.className =
          "modal-btn modal-btn-ghost modal-confirm-cancel";
        cancelButton.textContent = cancelLabel;

        const confirmButton = document.createElement("button");
        confirmButton.type = "button";
        confirmButton.className = `modal-btn ${
          danger ? "modal-btn-danger" : "modal-btn-primary"
        } modal-confirm-ok`;
        confirmButton.textContent = confirmLabel;

        const footer = document.createElement("div");
        footer.className = "modal-footer";
        footer.append(cancelButton, confirmButton);
        dialog.append(text, footer);

        let confirmed = false;
        // Answer as soon as the dialog is closed. The native close event
        // fires in a later task, by which time the user may have acted again.
        const close = modal.close.bind(modal);
        modal.close = (options) => {
          close(options);
          resolve(confirmed);
        };
        cancelButton.addEventListener("click", () =>
          modal.requestClose("cancel"),
        );
        confirmButton.addEventListener("click", () => {
          confirmed = true;
          modal.close();
        });
        document.body.appendChild(modal);
        // Registered after the wrapper's own close listener, so focus has
        // already been restored by the time the dialog is removed
        dialog.addEventListener("close", () => {
          modal.remove();
          resolve(confirmed);
        });
        modal.show({ initialFocus: confirmButton });
      });
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
          if (this._restoreFocus && this._returnFocusTo?.isConnected) {
            // Menu actions may have become hidden while the dialog was open.
            const details = this._returnFocusTo.closest("details:not([open])");
            const target =
              details?.querySelector("summary") || this._returnFocusTo;
            target.focus({ preventScroll: true });
          }
          this._returnFocusTo = null;
        },
        options,
      );
    }

    disconnectedCallback() {
      this._listeners?.abort();
      this._clearPendingClose();
      this._returnFocusTo = null;
      if (this.dialog?.open) this.dialog.close();
      this.busy = false;
    }

    _clearPendingClose() {
      this._escapeCleanup?.();
      this._escapeCleanup = null;
      clearTimeout(this._escapeTimer);
      this._escapeTimer = null;
    }

    show({ returnFocusTo, initialFocus } = {}) {
      const dialog = this.dialog;
      if (!dialog.open) {
        this._clearPendingClose();
        this._returnFocusTo = returnFocusTo || this.ownerDocument.activeElement;
        this._restoreFocus = true;
        dialog.showModal();
      }
      if (typeof initialFocus === "function") initialFocus();
      else initialFocus?.focus();
    }

    requestClose(source = "cancel") {
      if (!this.dialog.open || this.busy) return false;
      if (this.beforeClose && this.beforeClose(source) === false) return false;
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
