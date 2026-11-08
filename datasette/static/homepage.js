// Homepage: live search over the catalog, and plugin-provided sections.
// The /-/queries pages use the same live search and keyboard navigation.
(function () {
  const form = document.querySelector("[data-homepage-search]");
  const results = document.querySelector("[data-homepage-results]");
  const summary = document.querySelector(".homepage-summary");
  const sectionsContainer = document.querySelector("[data-homepage-sections]");
  const root = document.querySelector("[data-homepage]");
  const setSearching = (q) => {
    if (root) {
      root.classList.toggle("homepage-searching", !!q);
    }
  };

  // Live search: fetch the same page with ?q= and swap in the results, so
  // the server does all of the searching and the no-JS form still works.
  if (form && results) {
    const input = form.querySelector('input[name="q"]');
    let timer = null;
    let controller = null;
    let pendingUpdate = null;

    // Keyboard navigation of results, like the Jump to menu: up/down moves
    // a highlight through the result links in page order (tables, then
    // matching queries) and Enter opens the highlighted one.
    const RESULT_LINKS = ".catalog-item-name, .query-row-name";
    // On /-/queries the list is the whole page, so the arrow keys move
    // through it even before anything has been typed
    const browseWhenEmpty = !!(
      root && root.hasAttribute("data-browse-when-empty")
    );
    let active = -1;
    const resultLinks = () => [...results.querySelectorAll(RESULT_LINKS)];
    const setActive = (index) => {
      const links = resultLinks();
      results
        .querySelectorAll(".is-active")
        .forEach((el) => el.classList.remove("is-active"));
      active = links.length
        ? Math.max(-1, Math.min(index, links.length - 1))
        : -1;
      if (active >= 0) {
        const row = links[active].closest("li") || links[active];
        row.classList.add("is-active");
        row.scrollIntoView({ block: "nearest" });
      }
    };

    const urlFor = (q) => {
      const params = new URLSearchParams(new FormData(form));
      if (!q) {
        params.delete("q");
      } else {
        params.set("q", q);
      }
      // A new search starts again from the first page
      params.delete("page");
      params.delete("_next");
      const qs = params.toString();
      return form.action + (qs ? "?" + qs : "");
    };

    const update = async (q) => {
      const url = urlFor(q);
      const fragmentUrl =
        url + (url.includes("?") ? "&" : "?") + "_fragment=results";
      if (controller) {
        controller.abort();
      }
      controller = new AbortController();
      results.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(fragmentUrl, {
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
        // Highlight the top hit so Enter goes straight to it
        setActive(q ? 0 : -1);
      } catch (error) {
        if (error.name !== "AbortError") {
          console.error("Homepage search failed", error);
        }
      } finally {
        results.removeAttribute("aria-busy");
      }
    };

    input.addEventListener("input", () => {
      setSearching(input.value.trim());
      clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        pendingUpdate = update(input.value.trim());
      }, 180);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      clearTimeout(timer);
      timer = null;
      pendingUpdate = update(input.value.trim());
    });
    input.addEventListener("keydown", (event) => {
      // This box has focus on load, so "/" in an empty box still opens the
      // Jump to menu, as it does everywhere else
      if (
        event.key === "/" &&
        !input.value &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey
      ) {
        const jump = document.querySelector("navigation-search");
        if (jump && typeof jump.openMenu === "function") {
          event.preventDefault();
          jump.openMenu(input);
          return;
        }
      }
      if (event.key === "Escape" && input.value) {
        input.value = "";
        setSearching("");
        update("");
      } else if (
        (event.key === "ArrowDown" || event.key === "ArrowUp") &&
        (input.value.trim() || browseWhenEmpty)
      ) {
        event.preventDefault();
        setActive(active + (event.key === "ArrowDown" ? 1 : -1));
        if (active < 0) {
          setActive(0);
        }
      } else if (
        event.key === "Enter" &&
        (input.value.trim() || (browseWhenEmpty && active >= 0))
      ) {
        event.preventDefault();
        const openActive = () => {
          const link = resultLinks()[active];
          if (!link) {
            return;
          }
          if (event.metaKey || event.ctrlKey) {
            window.open(link.href, "_blank");
          } else {
            window.location.href = link.href;
          }
        };
        if (timer) {
          // Typed faster than the debounce: search now, then open the top hit
          clearTimeout(timer);
          timer = null;
          update(input.value.trim()).then(openActive);
        } else if (pendingUpdate && results.hasAttribute("aria-busy")) {
          pendingUpdate.then(openActive);
        } else {
          openActive();
        }
      }
    });

    if (input.value.trim()) {
      setActive(0);
    }

    // Focus the search box on load, so you can start typing straight away.
    // Skipped when the URL has a #fragment (such as #tables from pagination)
    // and on touch devices, where it would pop up the keyboard.
    if (
      !location.hash &&
      !window.matchMedia("(pointer: coarse)").matches &&
      document.activeElement === document.body
    ) {
      input.focus({ preventScroll: true });
      // Put the cursor after any existing search text
      const end = input.value.length;
      input.setSelectionRange(end, end);
    }
  }

  // Recently visited: read from the visit history kept by recent.js. The
  // server can't see localStorage, so this section is rendered here and
  // stays hidden until there is something to show.
  const recentSection = document.querySelector("[data-homepage-recent]");
  const renderRecent = () => {
    const recent = window.datasetteRecent;
    if (!recentSection || !recent) {
      return;
    }
    const listEl = recentSection.querySelector("[data-homepage-recent-list]");
    const multipleDatabases = !!document.querySelector(".homepage-databases");
    const items = recent.list({
      types: ["table", "view", "query", "database"],
      limit: 8,
    });
    listEl.textContent = "";
    items.forEach((item) => {
      const li = document.createElement("li");
      li.className = "recent-item";
      const link = document.createElement("a");
      link.className = "recent-item-name";
      link.href = item.url;
      link.textContent = recent.label(item);
      li.appendChild(link);
      if (item.type !== "table") {
        const tag = document.createElement("span");
        tag.className = `type-tag type-tag-${item.type}`;
        tag.textContent = item.type;
        li.appendChild(tag);
      }
      if (multipleDatabases && item.type !== "database" && item.database) {
        const dbTag = document.createElement("span");
        dbTag.className = "db-tag";
        if (/^[0-9a-fA-F]{3,8}$/.test(item.color || "")) {
          dbTag.style.setProperty("--db-color", "#" + item.color);
        }
        dbTag.textContent = item.database;
        li.appendChild(dbTag);
      }
      listEl.appendChild(li);
    });
    recentSection.hidden = items.length === 0;
  };
  if (recentSection) {
    renderRecent();
    recentSection
      .querySelector("[data-homepage-recent-clear]")
      .addEventListener("click", () => {
        window.datasetteRecent && window.datasetteRecent.clear();
        renderRecent();
      });
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
      let target = node;
      if (section.title) {
        // Core draws the heading so every plugin section looks the same
        const header = document.createElement("div");
        header.className = "homepage-section-heading";
        const heading = document.createElement("h2");
        heading.textContent = String(section.title);
        header.appendChild(heading);
        node.appendChild(header);
        target = document.createElement("div");
        target.className = "homepage-section-body";
        node.appendChild(target);
      }
      sectionsContainer.appendChild(node);
      try {
        section.render(target, context);
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
