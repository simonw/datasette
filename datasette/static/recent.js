// Recently visited tables, views, queries and databases, kept in
// localStorage. Pages that should be remembered include a
// <script type="application/json" data-datasette-recent-item> describing
// themselves; this script records the visit. Jump to and the homepage read
// the list through window.datasetteRecent.
(function () {
  const context = window.datasetteRecentContext || {};
  const STORAGE_KEY = "datasette.recent:" + (context.base || "/");
  const LEGACY_KEY = "datasette.navigationSearch.recentItems";
  const MAX_ITEMS = 50;
  const HALF_LIFE_DAYS = 7;
  const actor = context.actor == null ? null : String(context.actor);

  function load() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      return Array.isArray(parsed)
        ? parsed.filter((item) => item && item.url && item.type)
        : [];
    } catch (e) {
      return [];
    }
  }

  function save(items) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    } catch (e) {
      // localStorage may be unavailable, full, or disabled.
    }
  }

  // Frecency: visit count, decaying by half every HALF_LIFE_DAYS
  function score(item, now) {
    const ageDays = Math.max(now - (item.last || 0), 0) / 86400000;
    return (
      (1 + Math.log2(item.count || 1)) * Math.pow(0.5, ageDays / HALF_LIFE_DAYS)
    );
  }

  function record(entry) {
    if (!entry || !entry.url || !entry.type) {
      return;
    }
    const items = load();
    const now = Date.now();
    const existing = items.find(
      (item) => item.url === entry.url && item.actor === actor,
    );
    const updated = {
      url: String(entry.url),
      type: String(entry.type),
      database: entry.database ? String(entry.database) : "",
      name: entry.name ? String(entry.name) : "",
      title: entry.title ? String(entry.title) : "",
      color: entry.color ? String(entry.color) : "",
      actor,
      count: existing ? (existing.count || 1) + 1 : 1,
      last: now,
    };
    const rest = items.filter((item) => item !== existing);
    rest.sort((a, b) => (b.last || 0) - (a.last || 0));
    save([updated, ...rest].slice(0, MAX_ITEMS));
  }

  // Items for the current actor, best first
  function list(options = {}) {
    const { types = null, limit = 10, exclude = null } = options;
    const now = Date.now();
    return load()
      .filter((item) => item.actor === actor)
      .filter((item) => !types || types.includes(item.type))
      .filter((item) => item.url !== exclude)
      .map((item) => ({ ...item, score: score(item, now) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }

  function clear() {
    save(load().filter((item) => item.actor !== actor));
  }

  // Display label shared by Jump to and the homepage
  function label(item) {
    if (item.type === "database") {
      return item.database || item.name;
    }
    return item.title || item.name;
  }

  window.datasetteRecent = { record, list, clear, label };

  try {
    localStorage.removeItem(LEGACY_KEY);
  } catch (e) {
    // Ignore
  }

  const marker = document.querySelector("script[data-datasette-recent-item]");
  if (marker) {
    try {
      record(JSON.parse(marker.textContent));
    } catch (e) {
      // Ignore malformed markers
    }
  }
})();
