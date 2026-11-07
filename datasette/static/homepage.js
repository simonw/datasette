// Homepage: live search over the catalog, and plugin-provided sections.
(function () {
  const form = document.querySelector("[data-homepage-search]");
  const results = document.querySelector("[data-homepage-results]");
  const summary = document.querySelector(".homepage-summary");
  const sectionsContainer = document.querySelector("[data-homepage-sections]");

  // Live search: fetch the same page with ?q= and swap in the results, so
  // the server does all of the searching and the no-JS form still works.
  if (form && results) {
    const input = form.querySelector('input[name="q"]');
    let timer = null;
    let controller = null;

    const urlFor = (q) => {
      const params = new URLSearchParams(new FormData(form));
      if (!q) {
        params.delete("q");
      } else {
        params.set("q", q);
      }
      params.delete("page");
      const qs = params.toString();
      return form.action + (qs ? "?" + qs : "");
    };

    const update = async (q) => {
      const url = urlFor(q);
      if (controller) {
        controller.abort();
      }
      controller = new AbortController();
      results.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(url, {
          signal: controller.signal,
          headers: { Accept: "text/html" },
        });
        const doc = new DOMParser().parseFromString(
          await response.text(),
          "text/html",
        );
        const newResults = doc.querySelector("[data-homepage-results]");
        const newSummary = doc.querySelector(".homepage-summary");
        if (newResults) {
          results.innerHTML = newResults.innerHTML;
        }
        if (newSummary && summary) {
          summary.innerHTML = newSummary.innerHTML;
        }
        history.replaceState(null, "", url);
      } catch (error) {
        if (error.name !== "AbortError") {
          console.error("Homepage search failed", error);
        }
      } finally {
        results.removeAttribute("aria-busy");
      }
    };

    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => update(input.value.trim()), 180);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      clearTimeout(timer);
      update(input.value.trim());
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && input.value) {
        input.value = "";
        update("");
      } else if (event.key === "Enter") {
        // Enter on a search with one obvious hit jumps straight to it
        const first = results.querySelector(
          ".catalog-item-name, .query-card-name",
        );
        const all = results.querySelectorAll(
          ".catalog-item-name, .query-card-name",
        );
        if (all.length === 1 && first) {
          event.preventDefault();
          window.location.href = first.href;
        }
      }
    });

    // On the homepage, "/" focuses this search box rather than opening the
    // Jump to dialog, since the page itself is the jump interface.
    document.addEventListener(
      "keydown",
      (event) => {
        if (
          event.key === "/" &&
          !event.metaKey &&
          !event.ctrlKey &&
          !event.altKey &&
          !event.target.closest("input, textarea, select, [contenteditable]")
        ) {
          event.preventDefault();
          event.stopImmediatePropagation();
          input.focus();
          input.select();
        }
      },
      true,
    );
  }

  // Plugin sections: plugins register makeHomepageSections() with the
  // Datasette JS manager, the same way they register makeJumpSections().
  const renderSections = (manager) => {
    if (
      !sectionsContainer ||
      !manager ||
      typeof manager.makeHomepageSections !== "function"
    ) {
      return;
    }
    const context = { container: sectionsContainer };
    const sections = (manager.makeHomepageSections(context) || []).filter(
      (section) => section && typeof section.render === "function",
    );
    sections.forEach((section) => {
      const node = document.createElement("div");
      node.className = "homepage-section";
      if (section.id) {
        node.dataset.homepageSectionId = section.id;
      }
      sectionsContainer.appendChild(node);
      try {
        section.render(node, context);
      } catch (error) {
        console.error("Error rendering homepage section", section.id, error);
        node.remove();
      }
    });
  };

  // Wait until every plugin has handled datasette_init and registered itself
  if (window.__DATASETTE__) {
    setTimeout(() => renderSections(window.__DATASETTE__), 0);
  } else {
    document.addEventListener("datasette_init", (event) => {
      setTimeout(() => renderSections(event.detail), 0);
    });
  }
})();
