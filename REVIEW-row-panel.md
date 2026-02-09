# Review: `row-panel` branch — Side Panel for Row Details

**Branch:** `origin/row-panel`
**Commits:** 2 (`5e0cfa8b` Initial prototype, `472caf4e` Install Playwright in CI)
**Reference:** #2589

## Summary

The `row-panel` branch adds a side panel that slides in from the right when a user clicks a table row. It fetches the row's JSON data via the existing `/{db}/{table}/{pk}.json` API and displays it in a `<dialog>` element with prev/next navigation. The implementation spans:

- **`datasette/static/table.js`** — +310 lines: `initRowDetailPanel()` function
- **`datasette/templates/_table.html`** — +190 lines: dialog HTML + inline `<style>` block
- **`tests/test_row_detail_panel.py`** — +531 lines: Playwright browser tests
- **`pyproject.toml`** / **`.github/workflows/test.yml`** — Adds `pytest-playwright` dependency and CI setup

## What works well

1. Uses the native `<dialog>` element with `showModal()`, which gives correct focus trapping, backdrop, and Escape key behavior for free.
2. Prev/next navigation with automatic pagination — when the user navigates past the last visible row, it fetches the next page via the JSON API.
3. The `escapeHtml()` function properly creates a text node to escape HTML before inserting via `innerHTML`, preventing XSS from row values.
4. Comprehensive Playwright test suite covering open/close, navigation, pagination, keyboard, and responsive scenarios.

---

## Issues to address for production readiness

### 1. Security: XSS via error messages

In `showRowDetails()`, the error path interpolates `error.message` directly into `innerHTML`:

```js
contentDiv.innerHTML = `<p class="error">Error loading row details: ${escapeHtml(error.message)}</p>`;
```

This is safe because it uses `escapeHtml()`. However, the `"No primary key found"` and `"No row data found"` messages are hardcoded strings set via `innerHTML` — while safe today, this pattern is fragile. Consider using `textContent` for all static messages and reserving `innerHTML` only for the structured `<dl>` rendering.

### 2. `_table.html` is included on the row detail page too

The `row.html` template also does `{% include custom_table_templates %}`, which resolves to `_table.html`. This means the side panel `<dialog>`, all its CSS, and the `initRowDetailPanel()` initialization will also run on `/db/table/pk` pages. On that page:

- The table has a single row, so clicking it would open a panel showing the same data already visible on the page.
- The `<dialog>` markup (with id `rowDetailPanel`) would be present but provide no value.

**Fix:** Either guard the dialog HTML with a template conditional (e.g., `{% if is_table_view %}`), or move the dialog markup into `table.html` instead of `_table.html`.

### 3. Missing `data-value` attribute on `<td>` elements

The `extractPkValues()` function reads `cell.getAttribute('data-value')` with a fallback to `cell.textContent.trim()`:

```js
return cell.getAttribute('data-value') || cell.textContent.trim();
```

But the `_table.html` template does **not** emit a `data-value` attribute:

```html
<td class="col-{{ cell.column|to_css_class }} type-{{ cell.value_type }}">{{ cell.value }}</td>
```

This means PK extraction always falls back to `textContent`. This works for simple cases, but will break when:
- The `render_cell` plugin hook transforms the display value (e.g., adding links or formatting)
- The value contains HTML entities
- Binary/blob primary keys are displayed differently from their raw value

**Fix:** Add `data-value="{{ cell.raw|e }}"` to `<td>` elements (at least for PK columns), using the `raw` field that already exists in the cell context.

### 4. Compound and non-integer primary keys

`getRowUrl()` joins PK values with commas:

```js
const rowPath = pkValues.map(v => encodeURIComponent(v)).join(',');
```

The comma separator for compound PKs is correct (`path_from_row_pks` at `datasette/utils/__init__.py:192` uses `",".join(bits)`). However, each PK component is tilde-encoded on the server side — e.g., a PK value of `foo/bar` becomes `foo~2Fbar`, and a comma in a PK value becomes `~2C`. The JS code uses `encodeURIComponent(v)` which produces percent-encoding (`foo%2Fbar`, `%2C`). The server expects tilde-encoded paths, so this mismatch will cause 404 errors for any PK value containing characters that tilde-encoding and percent-encoding handle differently (which includes `/`, `,`, `+`, space, and many others).

**Fix:** Either implement a JS equivalent of `tilde_encode()` (see `datasette/utils/__init__.py:1278`), or — more robustly — extract the row URL from the existing `<a>` tag that Datasette already renders in the PK column of each row.

### 5. Row URL construction assumes table view path structure

```js
const currentPath = window.location.pathname;
return currentPath + '/' + rowPath + '.json';
```

This naively appends to `window.location.pathname`. Issues:
- If the URL has a `base_url` prefix configured, this still works (pathname includes it). OK.
- If query parameters like `_sort`, `_size`, etc. are in the URL, `pathname` won't include them. OK.
- But if the table name itself needs encoding (e.g., tables with dots or special chars), the current pathname may not match what `getRowUrl` expects.
- Most critically: this doesn't work for views/canned queries, only for actual table pages. Since the panel is injected via `_table.html`, it could theoretically appear in contexts where this path construction is wrong.

**Fix:** Extract the row link directly from the rendered `<a>` tag that Datasette already puts in PK columns, rather than constructing it from scratch. This would be more resilient.

### 6. Inline styles should be in a CSS file

The branch adds ~120 lines of CSS inside a `<style>` tag in `_table.html`. Datasette has a `datasette/static/` directory for static assets. Inline styles in a template:
- Can't be cached independently by browsers
- Are duplicated on every page load
- Mix concerns between template structure and presentation
- Will be duplicated if the template is included multiple times

**Fix:** Move the CSS to a separate file like `datasette/static/row-panel.css` and include it via a `<link>` tag or append it to an existing stylesheet.

### 7. No keyboard navigation within the panel

While Escape closes the panel (via native `<dialog>` behavior), there are no keyboard shortcuts for:
- Arrow left/right to navigate between rows
- Pressing Enter on a row to open the panel

These would be expected for accessibility (WCAG).

### 8. Accessibility gaps

- The panel has `aria-label` on buttons, which is good.
- Missing `role` attributes or `aria-live` region for the content area. When row data loads asynchronously, screen readers won't announce the new content.
- The "Row 1" position indicator should be more descriptive (e.g., "Row 1 of 5" or "Row 1 of 5, Laptop").
- No visible focus indicator for the dialog itself.
- The `×` close button character should use `&times;` or an SVG icon with proper `aria-label` (it does have `aria-label="Close"`, which is good).

### 9. Animation timing issues

```js
function animateCloseDialog() {
    dialog.style.transform = 'translateX(100%)';
    setTimeout(() => {
        dialog.close();
    }, 100);
}
```

The 100ms timeout is hardcoded and races against the CSS transition (also 100ms). If the JS event loop is busy, the `close()` call may fire before or during the animation.

**Fix:** Use the `transitionend` event instead of `setTimeout`:

```js
dialog.addEventListener('transitionend', () => { dialog.close(); }, { once: true });
dialog.style.transform = 'translateX(100%)';
```

### 10. Playwright test infrastructure is heavy for this project

The existing test suite uses `pytest` + `httpx` async client (no browser). This branch introduces Playwright, which:
- Adds a significant CI dependency (browser binaries ~300MB+ cached)
- Changes the test.yml workflow for all CI runs, not just the panel tests
- The tests spawn a real Datasette subprocess on a fixed port (8042), which could conflict in parallel test runs or CI
- The `scope="module"` fixture means all tests share one server but get fresh `page` fixtures — this is fine for Playwright but the fixed port is fragile.

**Suggestion:** Consider whether the simpler tests (elements exist, CSS cursor style) could be unit tests checking the HTML output via `ds_client.get()`. Reserve Playwright for the interactive behaviors (click-open, navigation, pagination). Also use a random available port instead of hardcoding 8042.

### 11. No feature flag or way to disable

There's no setting to disable the side panel. If a Datasette instance customizes `_table.html` via template overrides, the panel markup would need to be manually added. Conversely, users who don't want the click behavior have no way to opt out.

**Suggestion:** Consider a metadata/settings flag like `"row_detail_panel": false` to disable it, and/or make it a plugin rather than a core feature.

### 12. Panel doesn't reflect applied filters or column selections

When a user has `?_col=name&_col=price` or filters applied, the side panel still fetches the full row JSON (all columns). This could be confusing if the user has deliberately hidden columns.

### 13. No link to the full row page

The panel shows row data but doesn't provide a link to the actual row detail page (`/db/table/pk`). Users might want to navigate there for the full view with foreign key links, related rows, etc.

---

## Recommended priority order

| Priority | Issue | Effort |
|----------|-------|--------|
| **P0** | #4 — Tilde encoding for PK values (will cause 404s for PKs with special chars) | Small |
| **P0** | #2 — Panel appears on row detail page unnecessarily | Small |
| **P0** | #5 — Fragile URL construction (extract from existing links instead) | Medium |
| **P1** | #3 — Add `data-value` to PK `<td>` elements | Small |
| **P1** | #6 — Move inline CSS to static file | Small |
| **P1** | #9 — Fix animation race condition | Small |
| **P1** | #13 — Add link to full row page in panel | Small |
| **P2** | #8 — Accessibility (aria-live, keyboard nav) | Medium |
| **P2** | #10 — Test infrastructure refinements | Medium |
| **P2** | #7 — Keyboard shortcuts for navigation | Small |
| **P3** | #11 — Feature flag / opt-out mechanism | Medium |
| **P3** | #12 — Reflect column selections in panel | Medium |
