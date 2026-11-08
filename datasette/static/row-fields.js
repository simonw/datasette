// Row page field list: copy-to-clipboard buttons and "Show all" toggles
// for long values. See _row_fields.html.
(function () {
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(function () {
        return copyTextFallback(text);
      });
    }
    return copyTextFallback(text);
  }

  // For non-secure contexts (e.g. http:// on a LAN address) or when the
  // async clipboard API is refused
  function copyTextFallback(text) {
    return new Promise(function (resolve, reject) {
      var textarea = document.createElement("textarea");
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

  function announce(message) {
    var status = document.querySelector(".row-field-copy-status");
    if (!status) {
      return;
    }
    // Clear first so repeating the same message is announced again
    status.textContent = "";
    setTimeout(function () {
      status.textContent = message;
    }, 50);
  }

  document.addEventListener("click", function (ev) {
    var copyButton = ev.target.closest(".row-field-copy");
    if (copyButton) {
      var field = copyButton.closest(".row-field");
      var column = field ? field.getAttribute("data-column") : "value";
      copyText(copyButton.getAttribute("data-copy-value") || "").then(
        function () {
          copyButton.classList.add("is-copied");
          copyButton.setAttribute("title", "Copied");
          announce("Copied " + column + " to clipboard");
          clearTimeout(copyButton._copiedTimer);
          copyButton._copiedTimer = setTimeout(function () {
            copyButton.classList.remove("is-copied");
            copyButton.setAttribute("title", "Copy value");
          }, 1500);
        },
        function () {
          announce("Could not copy " + column);
        },
      );
      return;
    }
    var expandButton = ev.target.closest(".row-field-expand");
    if (expandButton) {
      var wrap = expandButton.closest(".row-field-long");
      var expanded = wrap.classList.toggle("is-expanded");
      expandButton.setAttribute("aria-expanded", expanded ? "true" : "false");
      if (!expandButton.hasAttribute("data-collapsed-label")) {
        expandButton.setAttribute(
          "data-collapsed-label",
          expandButton.textContent,
        );
      }
      expandButton.textContent = expanded
        ? "Show less"
        : expandButton.getAttribute("data-collapsed-label");
    }
  });
})();
