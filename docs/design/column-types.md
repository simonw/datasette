# Column Types Design

## Current State

Datasette knows three things about columns today:

1. **SQLite schema info** - stored in `catalog_columns` (name, SQLite type affinity, notnull, default, pk, hidden)
2. **Column metadata** - stored in `metadata_columns` (arbitrary key/value pairs per column, currently only `description` is used)
3. **Foreign keys** - stored in `catalog_foreign_keys` (detected from schema, rendered as links automatically)

Rendering customization happens entirely through the `render_cell` plugin hook: plugins inspect the value/column/table and return HTML if they want to handle it. There is no structured way for Datasette or its plugins to declare "this column holds timestamps" or "this column holds file IDs" and have the system respond to that declaration across multiple concerns.

## The Problem

Several features need to know *what kind of data* a column holds, beyond what SQLite's type affinity tells us:

- **datasette-files** needs columns that hold `df-{ULID}` file references to render as file previews, offer file upload widgets on edit forms, and validate on insert
- **Timestamps** need to render with locale-aware formatting, offer date-picker widgets on forms, enable date faceting, and sort correctly
- **URLs** could render as clickable links, be validated on input, and be previewed
- **Email addresses**, **JSON**, **Markdown**, **geographic coordinates** - same pattern

Each of these cross-cuts multiple concerns: rendering, editing, validation, faceting, filtering, API representation. The `render_cell` hook only covers one of those concerns (HTML table rendering).

## What Could Column Types Do?

A column type is a named bundle of behaviors that applies to a column. Here's what each behavior might look like:

### Rendering (display)
- HTML table cell rendering (today's `render_cell`)
- Row detail page rendering (possibly different from table view - a map for coordinates, an image preview for files)
- API representation (should a timestamp column include an ISO 8601 string AND a Unix epoch? Should a file column include the file metadata inline?)

### Editing (forms)
- What widget to show when inserting/updating a row (date picker, file upload, textarea for markdown, dropdown for enums)
- What the insert API should accept (a file upload multipart for file columns, ISO 8601 strings for timestamps)

### Validation
- Reject values that don't match the expected format on write
- Provide clear error messages ("Expected an ISO 8601 datetime, got 'next tuesday'")

### Faceting and Filtering
- Custom facet types (date faceting already exists as a special case in `facets.py`)
- Custom filter operations ("before", "after" for dates; "contains domain" for URLs)

### Type Coercion
- Automatically convert input values (parse "2024-01-15" into the appropriate storage format, resize an uploaded image)

### Sorting
- Custom sort behavior (natural sort for version strings, locale-aware sort for text)

### Column Header / Schema
- Expose the column type in the JSON API schema (`"type": "datetime"` rather than just `"type": "TEXT"`)
- Show a type indicator in the table UI (an icon or label next to the column name)

## Design Options

### Option A: Column metadata is enough (extend what exists)

The `metadata_columns` table already supports arbitrary key/value pairs per column. A "column type" is just `key="type", value="datetime"`. Plugins register themselves as handlers for specific type values and implement whatever behaviors they need.

**How it works:**

```yaml
databases:
  mydb:
    tables:
      events:
        columns:
          created_at:
            description: "When the event was created"
            type: datetime
          photo:
            description: "Event photo"
            type: file
            type:source: event-photos  # type-specific config
```

Stored in `metadata_columns` as:
```
(mydb, events, created_at, type, datetime)
(mydb, events, created_at, description, "When the event was created")
(mydb, events, photo, type, file)
(mydb, events, photo, type:source, event-photos)
```

Plugins check `await datasette.get_column_metadata(db, table, col)` to see if a column has a type they handle. The existing `render_cell` hook already receives the `datasette` instance, so plugins can look up column metadata there.

Runtime configuration works via `await datasette.set_column_metadata(db, table, col, "type", "datetime")` - already implemented.

**New hooks needed:**

```python
# Form widget for editing
def edit_cell_widget(column, table, database, datasette, request, current_value):
    """Return HTML for a form widget for this column"""

# Validation on write
def validate_cell(column, table, database, datasette, value):
    """Return None if valid, or an error message string"""

# Custom facet registration (already somewhat exists via register_facet_classes)
```

**Pros:**
- No new concepts - just more metadata keys and more hooks
- Runtime modification already works (`set_column_metadata`)
- Incremental - can adopt one behavior at a time
- Plugin authors already understand metadata

**Cons:**
- No formal contract for what a "type" provides - each plugin picks and chooses which hooks to implement
- Column metadata is flat key/value (strings only) - complex type config needs conventions (like `type:source` prefix, or JSON-in-a-string)
- Discovery is hard - there's no `get_registered_column_types()` to enumerate what's available
- No way to validate that a column's type value actually corresponds to a registered plugin

### Option B: First-class column type registry

Add a new `register_column_types` plugin hook that lets plugins formally declare column types with all their behaviors in one place.

**How it works:**

```python
from datasette import hookimpl
from datasette.column_types import ColumnType

class DateTimeColumnType(ColumnType):
    name = "datetime"
    label = "Date/Time"
    description = "ISO 8601 datetime values"

    def render_cell(self, value, row, column, table, database, request):
        if value is None:
            return None
        dt = parse_datetime(value)
        return Markup(f'<time datetime="{value}">{dt.strftime("%b %d, %Y %H:%M")}</time>')

    def render_cell_detail(self, value, row, column, table, database, request):
        """Richer rendering for the row detail page"""
        ...

    def edit_widget(self, value, column, table, database, request):
        return Markup(f'<input type="datetime-local" value="{value}">')

    def validate(self, value, column, table, database):
        try:
            parse_datetime(value)
            return None
        except ValueError:
            return f"Invalid datetime: {value}"

    def facet_class(self):
        return DateTimeFacet

    def api_schema(self):
        return {"type": "string", "format": "date-time"}

@hookimpl
def register_column_types():
    return [DateTimeColumnType()]
```

Type assignments still live in column metadata (`metadata_columns` with `key="type"`), but now the type value must correspond to a registered `ColumnType`. Datasette can validate this and provide a UI for selecting from available types.

**Configuration:**

```yaml
databases:
  mydb:
    tables:
      events:
        columns:
          created_at:
            type: datetime
          photo:
            type: file
            type_config:
              source: event-photos
              accept: "image/*"
```

The `type_config` is passed to the ColumnType instance, which can define what config it accepts.

**Runtime UI:**

Because types are registered, Datasette can offer a "Set column type" action:
1. User clicks column header -> "Configure column type"
2. Datasette shows a dropdown of registered types (datetime, file, url, markdown, ...)
3. User selects one -> type-specific config form appears (the ColumnType can provide this)
4. Saves to `metadata_columns`

**Pros:**
- Clear contract - a ColumnType is a concrete thing with known capabilities
- Discovery - `datasette.column_types` gives you the full list
- UI-friendly - Datasette can build configuration UIs automatically
- Composition - a ColumnType bundles all related behaviors together
- Validation - Datasette can reject unknown type names
- Type-specific configuration is structured (not flat key/value hacks)

**Cons:**
- New concept to learn
- More opinionated - may not suit all plugin patterns
- ColumnType methods become an API surface that's harder to evolve
- Could feel heavyweight for simple cases (just wanting to tweak cell rendering)

### Option C: Hybrid - metadata-driven with optional type registry

Use column metadata as the storage and source of truth, but add an optional registry for plugins that want to provide a full "type" experience.

**How it works:**

Column metadata remains the foundation. The `type` key in column metadata is the link between the two systems. If a plugin registers a ColumnType with `name="datetime"`, and a column has `metadata type=datetime`, then Datasette delegates to that ColumnType for all registered behaviors.

But plugins can also just implement individual hooks (`render_cell`, `validate_cell`, etc.) and check column metadata themselves - the registry is not required.

```python
# Full type registration (optional)
@hookimpl
def register_column_types():
    return [DateTimeColumnType()]

# OR just use individual hooks (also fine)
@hookimpl
def render_cell(value, column, table, database, datasette, request):
    metadata = await datasette.get_column_metadata(database, table, column)
    if metadata.get("type") == "datetime":
        return format_datetime(value)
```

**Datasette's behavior when rendering a cell:**
1. Check if the column has `type` metadata
2. If yes, and a registered ColumnType matches, call its `render_cell`
3. Then run `render_cell` plugin hooks as today (plugins can override or augment)
4. Fall back to default rendering

**Pros:**
- Backward compatible - existing render_cell plugins keep working
- Incremental adoption - plugins can start with metadata-only, evolve to ColumnType later
- Runtime config works the same either way (it's all metadata)
- The registry is additive, not required

**Cons:**
- Two ways to do things - potential confusion
- Ordering/priority between ColumnType rendering and render_cell hooks needs clear rules

## Key Design Questions

### 1. Should column types be auto-detected?

Datasette could infer types from:
- SQLite declared types (`DATETIME`, `TIMESTAMP` in the schema)
- Column names (`created_at`, `updated_at` suggest timestamps; `email` suggests email)
- Data patterns (first N rows match ISO 8601 format)

Auto-detection is convenient but can be wrong. Suggestion: auto-detect as *suggestions* shown in the UI, but don't apply without user confirmation. Or: auto-detect for well-known SQLite type declarations (`DATETIME` -> datetime type) since the schema author's intent is clear.

### 2. Where does type configuration live?

All options above use `metadata_columns` for storage. The question is whether `type_config` should also be in `metadata_columns` (as prefixed keys like `type:source`) or in a separate storage mechanism.

Using `metadata_columns` with a convention like `type_config:key` keeps things simple. Alternatively, store a single JSON blob: `key="type_config", value='{"source": "event-photos"}'`.

Recommendation: store type config as a JSON string in `metadata_columns` with `key="type_config"`. This avoids namespace pollution and lets types define arbitrarily structured config.

### 3. How does this interact with the edit/insert API?

Datasette's table create/insert API (`POST /-/create`, `POST /db/table/-/insert`) currently accepts raw values. With column types:

- The API could accept type-appropriate values and coerce them (accept `"2024-01-15"` for a datetime column)
- File columns would need multipart upload support
- Validation errors need to surface clearly in API responses

This is where the `validate_cell` hook (or ColumnType.validate method) becomes important - it runs before the write and returns structured errors.

### 4. Should types affect the JSON API response?

Today the API returns raw SQLite values. Options:
- Add a `column_types` key to the response metadata listing the assigned types
- Optionally transform values (format datetimes, inline file metadata)
- Add a schema endpoint describing column types

Recommendation: expose type information in response metadata, but don't transform values by default (breaking change). Offer an `?_extra=typed_values` or `?_shape=typed` opt-in.

### 5. How do types interact with faceting?

The existing `register_facet_classes` hook lets plugins add facet types. If a column has `type=datetime`, Datasette could auto-suggest the date facet instead of requiring `?_facet_date=col`. This is a natural fit for the ColumnType registry (Option B/C) where a type can declare its preferred facet class.

## Recommendation

**Option C (hybrid)** seems like the best fit for Datasette's design philosophy:

1. **Column metadata is the foundation.** It already exists, already supports runtime modification, and is already persisted. Making the `type` metadata key a first-class convention (documented, supported in YAML, shown in the UI) is the minimal viable step.

2. **The ColumnType registry is the ergonomic layer.** It makes plugin development nicer and enables Datasette to build UIs for type selection and configuration. But it's optional - simple plugins can just check metadata in their hooks.

3. **Incremental delivery.** Ship the metadata convention and a few new hooks first (`edit_cell_widget`, `validate_cell`). Add the ColumnType registry later when patterns solidify.

### Concrete next steps

1. **Document the `type` metadata key convention.** Even before adding new hooks, plugins can start using `set_column_metadata(db, table, col, "type", "datetime")` and checking it in `render_cell`.

2. **Add `type` to the YAML metadata schema.** Currently `columns:` in metadata YAML only supports description strings. Extend it to support:
   ```yaml
   columns:
     created_at:
       description: "When created"
       type: datetime
     # Short form still works for description-only
     name: "The person's name"
   ```

3. **Add a `column_actions` plugin hook.** This lets plugins add actions to column headers (like "Set type to datetime") mirroring the existing `table_actions` pattern.

4. **Add a `validate_cell` hook.** Called before writes, receives column metadata.

5. **Add an `edit_cell_widget` hook.** Returns form HTML for the table editing UI.

6. **Ship built-in datetime and URL column types** as either core code or a bundled plugin, to prove the pattern works.

7. **Later: add `register_column_types` hook** once the per-behavior hooks are proven and the ColumnType base class API stabilizes.
