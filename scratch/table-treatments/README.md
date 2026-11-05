# Table treatment explorations

Alternative visual treatments for the rows-and-columns table on Datasette's table pages, switchable from a prototype bar above the table. Nothing in `datasette/` is changed: a local plugin adds the switcher and the treatment stylesheets to table pages only.

Start from the repository root:

```sh
uv run --python 3.15 datasette content.db --reload -p 8821 \
  --plugins-dir scratch/table-treatments/plugins \
  --static tt:scratch/table-treatments/static
```

Then open <http://localhost:8821/content/datasette_repos>. Click a style, or press 1–8. The choice is remembered in localStorage and reflected in the URL as `?_tt=<id>`, so a link can point at a specific treatment. Datasette preserves `_`-prefixed parameters in its sort, facet and export links, so the treatment survives sorting and paging.

## Long values (Datasette treatment)

On tables such as `pypi_packages`, `repos` and `news`, a single long value (a README in `description`, a release-note `body`) used to make every row hundreds of pixels tall: rows on `pypi_packages` had a median height of 1,235px, and the table was 120,631px tall. `treatments/datasette.js` now clamps any value that renders taller than three lines:

- Collapsed, the value shows as a three-line preview with its line breaks folded into spaces, and the end of the last line fades out under a small "more" pill. If the folded value would fit entirely, the preview keeps its real line breaks instead, so "more" always reveals more text. Rows are never taller than three lines, so `pypi_packages` rows are 77–78px and the table is 7,821px tall.
- Clicking "more" expands that one value with its original formatting. The pill moves to the value's top right as "less", on the line you clicked from, and keeps keyboard focus. The clicked row stays where it is on screen.
- Every column with at least one clamped value gets an expand-all toggle in its header, after the gear. It shows chevrons apart while any value is collapsed (in the accent colour once some are open), and chevrons together on the accent tint when all are open, when a click collapses them all. Its `title` explains what a click will do. The gear menu also gains "Expand long values" / "Collapse long values" (through Datasette's `makeColumnActions` JavaScript plugin hook), and Alt-click on a pill does the same.
- The preview length is one setting, `--tt-preview-lines` in `datasette.css`, which the script reads too.
- Details: cells are chosen by measuring their rendered height, so narrow mobile cards and wide CJK text are handled. Rows that Datasette's edit and insert dialogs swap in are clamped too. Copying a collapsed value copies the original text with its line breaks, without the pill's label. Pills are 28px tall on mobile. Screen-reader labels name the column and row ("more: description, airtable-export").
- CSS cannot cap a table cell's height, so the script wraps the contents of long cells only, and only while this treatment is active. Switching to another treatment restores the server's markup exactly. Expanded cells stay expanded across a resize or a switch away and back. A resize that only changes the height does nothing, and one that changes the width re-measures and keeps focus on the pill you were on.

## Freeze pin (Datasette treatment)

The primary key column's header has a pin button after its gear. Pressing it freezes the header row and the row's identity columns, as in the Data grid treatment: the table card becomes a scroll box the height of the window, the header sticks to its top, and the leading Link and primary key columns stick to its left. That is one column for most tables, Link and `rowid` for rowid tables (`news`), and Link plus every key column for compound keys (`stats`), up to four columns.

- The pin is tilted when off, and upright, filled and in the accent colour when on (`aria-pressed`). Its `title` tooltip explains what it freezes and that the choice applies to every table, and changes once pinned.
- Pinning lines the card up with the top of the window and hands the page's scroll position to the card, so the rows you were reading stay where they are. Unpinning does the reverse.
- Hairlines mark the frozen edges, and soft shadows appear once content has scrolled under them.
- The choice is remembered for every table in localStorage (`datasette-table-freeze`).
- Below 577px (Datasette's stacked mobile cards) the pin is hidden and nothing is frozen.
- Switching to another treatment removes the pin and restores the table and wrapper markup exactly.

`--reload` does not watch `--plugins-dir`, so restart the server after editing `plugins/table_treatments.py`. CSS and JS edits only need a page reload.

## How it works

- `plugins/table_treatments.py` uses `extra_css_urls` and `extra_js_urls` to add `switcher.css`, `switcher.js` and every `static/treatments/*.css` file to table pages (`view_name == "table"`). Each URL carries the file's mtime, so saving a file and reloading shows the change.
- `static/switcher.js` runs synchronously in `<head>` and sets `<html data-table-treatment="<id>">` before first paint. It also copies each column's name, declared type and primary-key flag from the `<th>` onto every `<td>` as `data-column`, `data-column-type` and `data-is-pk`, which treatments use for labels and type-aware styling.
- Every treatment is one CSS file whose selectors all start with `html[data-table-treatment="<id>"]`, so they never interfere with each other or with the rest of the page.

## Treatments

| Key | Id | Notes |
| --- | --- | --- |
| 1 | `current` | Datasette as it ships today (no CSS). |
| 2 | `datasette` | The most Datasette-like option, borrowing the modal dialog and filter panel design language. |
| 3 | `agent` | A direct port of datasette-agent's markdown table styling, checked against computed styles from a real saved conversation. |
| 4 | `grid` | Dense spreadsheet in a 75vh scrolling box: row numbers, sticky header and first column, one-line cells cut off with an ellipsis. |
| 5 | `cards` | Each row is a card: a title, a strip of its numbers, then labelled fields. The header row becomes a sticky "Sort by" chip bar. Also replaces the mobile layout. |
| 6 | `soft` | Product-style UI: type glyphs (`Aa`, `#`, key) in a rounded header bar, ID chips, gear revealed on hover or focus. |
| 7 | `swiss` | Printed data sheet: heavy black rules, uppercase type annotations under each header, one-line rows, signal red for keys and hover. |
| 8 | `terminal` | Dark console panel in sqlite3's quoted box style: quoted green text, peach numbers, dim `NULL`, wrapping at 80 columns. |

Selectors are all scoped (checked by script). Each treatment was built by one agent and then reviewed by a separate one, using headless screenshots at 780px, 1280px and 390px, a scrolled view, the column menu open, other tables in `content.db` and a sorted view. Reviewer screenshots are in the session scratchpad, not in this folder.

Known limitations of the CSS-only approach: none of the treatments can make the header stick to the page, because `.table-wrapper` is the horizontal scroll container (`grid` gets round this by giving the table its own scroll box). A descending sort can't be detected from the markup, so only the arrow marks the sorted column. Type-aware alignment depends on the `data-column-type` attribute that `switcher.js` adds, which `_table.html` would need to emit if any of this shipped.
