// Demonstrates every shared modal CSS class, for images/modal-classes.webp
document.addEventListener("datasette_init", () => {
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.textContent = "Open export dialog";
    openButton.setAttribute("aria-haspopup", "dialog");
    openButton.setAttribute("aria-controls", "export-dialog");

    const modal = DatasetteModal.create();
    const dialog = modal.dialog;
    dialog.id = "export-dialog";
    dialog.setAttribute("aria-labelledby", "export-dialog-title");
    dialog.innerHTML = `
      <div class="modal-header">
        <h2 class="modal-title" id="export-dialog-title">Export rows</h2>
        <span class="modal-meta">3 selected</span>
      </div>
      <div class="modal-body">
        <p>Export these rows from the <strong>plants</strong> table as CSV:</p>
        <ul>
          <li>Monstera deliciosa</li>
          <li>Ficus lyrata</li>
          <li>Pilea peperomioides</li>
        </ul>
      </div>
      <div class="modal-footer">
        <span class="footer-info">CSV, UTF-8</span>
        <button type="button" class="modal-btn modal-btn-ghost">Cancel</button>
        <button type="button" class="modal-btn modal-btn-primary">Export</button>
      </div>`;

    const [cancelButton, exportButton] = dialog.querySelectorAll(".modal-footer button");
    cancelButton.addEventListener("click", () => modal.requestClose("cancel"));
    exportButton.addEventListener("click", () => modal.close());
    openButton.addEventListener("click", () => {
        modal.show({ returnFocusTo: openButton, initialFocus: exportButton });
    });

    document.body.append(modal);
    document.querySelector("section.content").append(openButton);
});
