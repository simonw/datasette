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

  // The template guesses which values are long from their length, but a
  // value of a few hundred characters can fit within the clamp on a wide
  // screen. Mark those as fitting so CSS drops the fade and the toggle.
  // Re-checked whenever a value's size changes (window resize, font load).
  function updateFits(value) {
    var wrap = value.parentElement;
    if (wrap.classList.contains("is-expanded")) {
      return;
    }
    wrap.classList.toggle("fits", value.scrollHeight <= value.clientHeight + 1);
  }
  var longValues = document.querySelectorAll(
    ".row-field-long > .row-field-value",
  );
  if (window.ResizeObserver) {
    var observer = new ResizeObserver(function (entries) {
      entries.forEach(function (entry) {
        updateFits(entry.target);
      });
    });
    longValues.forEach(function (value) {
      observer.observe(value);
    });
  } else {
    longValues.forEach(updateFits);
  }

  // JSON values (objects and arrays) get a toggle that switches the
  // displayed value between as-stored and pretty-printed. The copy button
  // copies whichever form is currently displayed.
  function prettyJson(text) {
    var trimmed = text.trim();
    var first = trimmed.charAt(0);
    if (first !== "{" && first !== "[") {
      return null;
    }
    try {
      var pretty = JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch (error) {
      return null;
    }
    // Nothing to toggle for "[]", "{}" or values already stored pretty
    return pretty === text ? null : pretty;
  }
  document.querySelectorAll(".row-field-copy").forEach(function (copyButton) {
    var field = copyButton.closest(".row-field");
    var value = field && field.querySelector(".row-field-value");
    var raw = copyButton.getAttribute("data-copy-value") || "";
    var pretty = value ? prettyJson(raw) : null;
    if (pretty === null) {
      return;
    }
    var column = field.getAttribute("data-column");
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "row-field-json";
    toggle.setAttribute("aria-pressed", "false");
    toggle.setAttribute("aria-label", "Pretty-print " + column + " JSON");
    toggle.setAttribute("title", "Pretty-print JSON");
    // Curly braces, drawn to match the copy icon
    toggle.innerHTML =
      '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"/><path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/></svg>';
    toggle._json = {
      raw: raw,
      pretty: pretty,
      html: value.innerHTML,
      value: value,
      copyButton: copyButton,
    };
    copyButton.parentElement.appendChild(toggle);
  });

  function toggleJson(toggle) {
    var json = toggle._json;
    var isPretty = toggle.getAttribute("aria-pressed") !== "true";
    toggle.setAttribute("aria-pressed", isPretty ? "true" : "false");
    toggle.setAttribute(
      "title",
      isPretty ? "Show JSON as stored" : "Pretty-print JSON",
    );
    json.value.classList.toggle("is-json-pretty", isPretty);
    if (isPretty) {
      json.value.textContent = json.pretty;
    } else {
      // Restore the original markup, which may include rendered links
      json.value.innerHTML = json.html;
    }
    json.copyButton.setAttribute(
      "data-copy-value",
      isPretty ? json.pretty : json.raw,
    );
    var wrap = json.value.parentElement;
    if (wrap.classList.contains("row-field-long")) {
      if (isPretty) {
        // Show the whole pretty-printed value rather than its first lines.
        // Content changed but a clamped box keeps its size, so
        // ResizeObserver will not fire: re-check whether it fits first.
        updateFits(json.value);
        if (
          !wrap.classList.contains("is-expanded") &&
          !wrap.classList.contains("fits")
        ) {
          setExpanded(wrap, true);
          json.autoExpanded = true;
        }
      } else {
        // Undo the expansion if pretty-printing is what caused it
        if (json.autoExpanded && wrap.classList.contains("is-expanded")) {
          setExpanded(wrap, false);
        }
        json.autoExpanded = false;
        updateFits(json.value);
      }
    }
  }

  document.addEventListener("click", function (ev) {
    var jsonButton = ev.target.closest(".row-field-json");
    if (jsonButton) {
      toggleJson(jsonButton);
      return;
    }
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
            copyButton.setAttribute("title", "Copy to clipboard");
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
      setExpanded(wrap, !wrap.classList.contains("is-expanded"));
    }
  });

  function setExpanded(wrap, expanded) {
    var expandButton = wrap.querySelector(".row-field-expand");
    wrap.classList.toggle("is-expanded", expanded);
    expandButton.setAttribute("aria-expanded", expanded ? "true" : "false");
    // The label is CSS generated content from data-label, so copying a
    // section of the page leaves it out
    if (!expandButton.hasAttribute("data-collapsed-label")) {
      expandButton.setAttribute(
        "data-collapsed-label",
        expandButton.getAttribute("data-label"),
      );
    }
    var label = expanded
      ? "Show less"
      : expandButton.getAttribute("data-collapsed-label");
    expandButton.setAttribute("data-label", label);
    expandButton.setAttribute("aria-label", label);
  }
})();
