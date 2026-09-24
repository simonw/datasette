document.addEventListener("datasette_init", () => {
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.textContent = "Open example dialog";
    // Indicate that this button opens a dialog:
    openButton.setAttribute("aria-haspopup", "dialog");
    // Identify which dialog it controls:
    openButton.setAttribute("aria-controls", "my-plugin-dialog");

    const modal = DatasetteModal.create();
    const dialog = modal.dialog;
    dialog.id = "my-plugin-dialog";
    // Tell screenreaders the dialog is labelled by #my-plugin-dialog-title
    dialog.setAttribute("aria-labelledby", "my-plugin-dialog-title");
    dialog.innerHTML = `
      <div class="modal-header">
        <h2 class="modal-title" id="my-plugin-dialog-title">
          Example dialog
        </h2>
      </div>
      <div class="modal-body">
        This dialog uses Datasette's shared styles and keyboard behavior.
      </div>
      <div class="modal-footer">
        <button type="button" class="modal-btn modal-btn-ghost">Close</button>
      </div>`;

    const closeButton = dialog.querySelector("button");
    closeButton.addEventListener("click", () => {
        modal.requestClose("cancel");
    });
    openButton.addEventListener("click", () => {
        modal.show({ returnFocusTo: openButton, initialFocus: closeButton });
    });

    document.body.append(modal);
    document.querySelector("section.content").append(openButton);
});
