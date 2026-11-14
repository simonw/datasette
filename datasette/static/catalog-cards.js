// Table cards on the homepage and database page: "+N more" shows the rest of
// a table's columns in its card, and then offers to hide them again.
// Delegated, because the homepage replaces its results as you type.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-catalog-columns-more]");
  if (!button) {
    return;
  }
  const rest = button.parentElement.querySelector(".catalog-columns-rest");
  if (!rest) {
    return;
  }
  const expand = rest.hidden;
  rest.hidden = !expand;
  button.setAttribute("aria-expanded", String(expand));
  button.textContent = expand
    ? button.dataset.labelFewer
    : button.dataset.labelMore;
});
