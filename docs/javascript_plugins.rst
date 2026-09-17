.. _javascript_plugins:

JavaScript plugins
==================

Datasette can run custom JavaScript in several different ways:

- Datasette plugins written in Python can use the :ref:`extra_js_urls() <plugin_hook_extra_js_urls>` or :ref:`extra_body_script() <plugin_hook_extra_body_script>` plugin hooks to inject JavaScript into a page
- Datasette instances with :ref:`custom templates <customization_custom_templates>` can include additional JavaScript in those templates
- The ``extra_js_urls`` key in ``datasette.yaml`` :ref:`can be used to include extra JavaScript <configuration_reference_css_js>`

There are no limitations on what this JavaScript can do. It is executed directly by the browser, so it can manipulate the DOM, fetch additional data and do anything else that JavaScript is capable of.

.. warning::
    Custom JavaScript has security implications, especially for authenticated Datasette instances where the JavaScript might run in the context of the authenticated user. It's important to carefully review any JavaScript you run in your Datasette instance.

.. _javascript_datasette_init:

The datasette_init event
------------------------

Datasette emits a custom event called ``datasette_init`` when the page is loaded. This event is dispatched on the ``document`` object, and includes a ``detail`` object with a reference to the :ref:`datasetteManager <javascript_datasette_manager>` object.

Your JavaScript code can listen out for this event using ``document.addEventListener()`` like this:

.. code-block:: javascript

    document.addEventListener("datasette_init", function (evt) {
        const manager = evt.detail;
        console.log("Datasette version:", manager.VERSION);
    });

.. _javascript_datasette_manager:

datasetteManager
----------------

The ``datasetteManager`` object 

``VERSION`` - string
    The version of Datasette

``plugins`` - ``Map()``
    A Map of currently loaded plugin names to plugin implementations

``registerPlugin(name, implementation)``
    Call this to register a plugin, passing its name and implementation

``makeColumnField(context)``
    Calls the ``makeColumnField()`` hook on registered plugins, returning the first custom insert/edit field control that matches the provided field context. This is used internally by Datasette's row insert and edit dialogs.

``selectors`` - object
    An object providing named aliases to useful CSS selectors, :ref:`listed below <javascript_datasette_manager_selectors>`

.. _javascript_plugin_objects:

JavaScript plugin objects
-------------------------

JavaScript plugins are blocks of code that can be registered with Datasette using the ``registerPlugin()`` method on the :ref:`datasetteManager <javascript_datasette_manager>` object.

The ``implementation`` object passed to this method should include a ``version`` key defining the plugin version, and one or more of the following named functions providing the implementation of the plugin:

.. _javascript_plugins_makeJumpSections:

makeJumpSections(context)
~~~~~~~~~~~~~~~~~~~~~~~~~

This method should return a JavaScript array of objects defining additional sections to be added to the blank state of the ``/`` jump menu, before the user starts typing a search.

It should return an array of objects, each with the following:

``id`` - string
    A unique string ID for the section, for example ``agent-chat``
``render(node, context)`` - function
    A function that will be called with a DOM node to render the section into

Datasette passes a ``context`` object to both ``makeJumpSections(context)`` and ``render(node, context)``. It has the following keys:

``navigationSearch``
    The ``<navigation-search>`` custom element instance.
``container`` - only for ``render()``
    The ``.results-container`` element used by the jump menu.
``input`` - only for ``render()``
    The ``.search-input`` element used by the jump menu.

This example shows how a plugin might add a button for starting a new chat:

.. code-block:: javascript

    document.addEventListener('datasette_init', function(ev) {
      ev.detail.registerPlugin('agent-plugin', {
        version: 0.1,
        makeJumpSections: (context) => {
          return [
            {
              id: 'agent-chat',
              render: (node, context) => {
                node.innerHTML = '<button type="button">Start a new chat</button>';
                node.querySelector('button').addEventListener('click', () => {
                  location.href = '/-/agent/new';
                });
              }
            }
          ];
        }
      });
    });

.. _javascript_plugins_makeAboveTablePanelConfigs:

makeAboveTablePanelConfigs()
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This method should return a JavaScript array of objects defining additional panels to be added to the top of the table page. Each object should have the following:

``id`` - string
    A unique string ID for the panel, for example ``map-panel``
``label`` - string
    A human-readable label for the panel
``render(node)`` - function
    A function that will be called with a DOM node to render the panel into

This example shows how a plugin might define a single panel:

.. code-block:: javascript

    document.addEventListener('datasette_init', function(ev) {
      ev.detail.registerPlugin('panel-plugin', {
        version: 0.1,
        makeAboveTablePanelConfigs: () => {
          return [
            {
              id: 'first-panel',
              label: 'First panel',
              render: node => {
                node.innerHTML = '<h2>My custom panel</h2><p>This is a custom panel that I added using a JavaScript plugin</p>';
              }
            }
          ]
        }
      });
    });

When a page with a table loads, all registered plugins that implement ``makeAboveTablePanelConfigs()`` will be called and panels they return will be added to the top of the table page.

.. _javascript_plugins_makeColumnActions:

makeColumnActions(columnDetails)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This method, if present, will be called when Datasette is rendering the cog action menu icons that appear at the top of the table view. By default these include options like "Sort ascending/descending" and "Facet by this", but plugins can return additional actions to be included in this menu.

The method will be called with a ``columnDetails`` object with the following keys:

``columnName`` - string
    The name of the column
``columnNotNull`` - boolean
    True if the column is defined as NOT NULL
``columnType`` - string
    The SQLite data type of the column
``isPk`` - boolean
    True if the column is part of the primary key

It should return a JavaScript array of objects each with a ``label`` and ``onClick`` property:

``label`` - string
    The human-readable label for the action
``onClick(evt)`` - function
    A function that will be called when the action is clicked

The ``evt`` object passed to the ``onClick`` is the standard browser event object that triggered the click.

This example plugin adds two menu items - one to copy the column name to the clipboard and another that displays the column metadata in an ``alert()`` window:

.. code-block:: javascript

    document.addEventListener('datasette_init', function(ev) {
      ev.detail.registerPlugin('column-name-plugin', {
        version: 0.1,
        makeColumnActions: (columnDetails) => {
          return [
            {
              label: 'Copy column to clipboard',
              onClick: async (evt) => {
                await navigator.clipboard.writeText(columnDetails.columnName)
              }
            },
            {
              label: 'Alert column metadata',
              onClick: () => alert(JSON.stringify(columnDetails, null, 2))
            }
          ];
        }
      });
    });

.. _javascript_plugins_makeColumnField:

makeColumnField(context)
~~~~~~~~~~~~~~~~~~~~~~~~

This method, if present, can provide a custom form field for a column in Datasette's row insert and edit dialogs.

It is designed for plugins that :ref:`register custom column types <plugin_register_column_types>` using the Python ``register_column_types()`` plugin hook. For example, a plugin that defines a ``file`` column type can use ``makeColumnField()`` to replace a plain text input with a file picker, and a plugin that defines a rich text column type can use it to enhance the field with an editor.

Datasette calls ``makeColumnField(context)`` on each registered JavaScript plugin when it renders an editable insert/edit field. Plugins should inspect the ``context`` object and only return a control object if they can handle that field. Otherwise, use a bare ``return;``.

The first plugin to return a truthy control object is used for that field. Plugins are called in registration order. If a plugin raises an exception, Datasette logs the error to the browser console and continues to the next plugin.

The row dialog tracks the value that will be sent to the insert/update API. The ``context`` object describes the column and form environment; custom controls should read and write field values using the ``field`` helper object passed to ``render(field)``.

Context object
^^^^^^^^^^^^^^

``makeColumnField(context)`` is called with a context object describing the field. The current context object has these keys:

``mode`` - string
    ``"insert"`` or ``"edit"``.

``database`` - string or null
    The database name.

``table`` - string or null
    The table name.

``tableUrl`` - string or null
    The path to the table page, including any configured :ref:`base URL prefix <setting_base_url>`.

``column`` - string
    The column name.

``columnType`` - object or null
    The configured Datasette column type for this column, if one exists. This is ``null`` if no column type has been configured.

    If present, this object has exactly these keys:

    ``type`` - string
        The :ref:`registered column type name <plugin_register_column_types>`, matching the ``name`` attribute of the Python ``ColumnType`` subclass.

    ``config`` - object
        Configuration for this specific column type assignment. This is ``{}`` if no configuration has been set.

``sqliteType`` - string or null
    The SQLite affinity for this column, if known. This is one of ``"TEXT"``, ``"INTEGER"``, ``"REAL"``, ``"BLOB"``, ``"NUMERIC"`` or ``null`` if Datasette could not determine the affinity.

``notNull`` - boolean
    True if the column is defined as ``NOT NULL``.

``isPk`` - boolean
    True if this column is part of the table's primary key.

``defaultExpression`` - string or null
    The SQLite default expression for the column, if available. This is ``null`` if the column has no SQLite default. For example, a column defined with ``DEFAULT (datetime('now'))`` will have ``"datetime('now')"`` here. This is the expression from the table schema, not the actual value SQLite will insert.

``form`` - ``HTMLFormElement`` or null
    The row insert/edit form element.

``dialog`` - ``HTMLDialogElement`` or null
    The modal dialog element.

Returned control object
^^^^^^^^^^^^^^^^^^^^^^^

A plugin that wants to handle a field should return an object. Datasette currently recognizes these properties:

``useTextarea`` - boolean, optional
    If true, Datasette creates a ``<textarea>`` as the underlying ``field.input`` before calling ``render()``. If omitted, Datasette chooses either an ``<input>`` or ``<textarea>`` based on the column type and current value.

``render(field)`` - function
    Called once to render the custom field UI. ``field`` is a helper object described below.

    The recommended pattern is to return a DOM node from ``render()``. Datasette appends that node to ``field.root``, a ``<div>`` inside the control area for that field in the row insert/edit dialog. A plugin can alternatively manipulate ``field.root`` directly and return nothing.

``focus(field)`` - function, optional
    Called when Datasette wants to focus this field, for example when focusing the first editable field in the dialog. Use this to focus the most useful interactive element inside the custom UI.

``destroy(field)`` - function, optional
    Called when Datasette tears down the insert/edit form. Use this to remove event listeners, close nested pickers, revoke object URLs, clear timers, or release other resources.

The field helper object
^^^^^^^^^^^^^^^^^^^^^^^

The ``field`` object passed to ``render(field)``, ``focus(field)`` and ``destroy(field)`` provides stable IDs, DOM elements and value helpers for integrating with the row insert/edit dialog:

``context`` - object
    The original context object passed to ``makeColumnField()``.

``id`` - string
    The ID Datasette assigned to ``field.input``, the backing ``<input>`` or ``<textarea>`` element.

``labelId`` - string
    The ID of the visible field label.

``descriptionId`` - string
    The ID of the field metadata/help text. This metadata can include details such as ``Primary key``, ``Required``, ``Current value: NULL`` or ``Custom type: file``.

``root`` - ``HTMLElement``
    The empty ``<div>`` container created by Datasette for this custom field. It is inside the control area for the field in the row insert/edit dialog, next to the field label and above the field metadata. Datasette appends the DOM node returned by ``render(field)`` to this element. Plugins can alternatively manipulate this element directly and return nothing from ``render(field)``.

``input`` - ``HTMLInputElement`` or ``HTMLTextAreaElement``
    The core-owned backing form control. Plugins can keep this visible, wrap it or hide it, but should use the value helper methods below rather than mutating ``input.value`` directly.

``control``
    An alias for ``input``.

``meta`` - ``HTMLElement`` or null
    The field metadata/help text element.

``form`` - ``HTMLFormElement`` or null
    The containing row insert/edit form.

``dialog`` - ``HTMLDialogElement`` or null
    The containing modal dialog.

``getValue()`` - function
    Returns the current value for this field.

    Datasette uses string values by default. Insert fields for ``"INTEGER"`` and ``"REAL"`` SQLite columns return numbers, or ``null`` if left blank. Plugins can use strings, numbers, booleans or ``null``. If a plugin is editing structured data stored in a SQLite ``TEXT`` column, such as JSON, it should serialize that data to a string before calling ``setValue()``.

``setValue(value)`` - function
    Sets the current value for this field. ``value`` should be a string, number, boolean or ``null``.

    Calling ``setValue()`` also stops using the SQLite default for the field, if it was previously selected.

``getInitialValue()`` - function
    Returns the submitted-value representation the field had when the form was rendered. For edit forms this is the raw row value from the database. For insert forms this is the blank starting value.

``hasChanged()`` - function
    Returns true if the field has changed since Datasette last considered it unmodified. By default that means the field state when the insert/edit form was rendered.

``clearValue()`` - function
    Sets the value to ``null``.

``markClean()`` - function
    Tells Datasette to treat the field's current state as unmodified. After calling this method, ``hasChanged()`` returns false until the field value changes again or its SQLite-default state changes.

    This is useful when a plugin rewrites the starting value into an equivalent representation while initializing its editor. For example, a rich text editor might normalize empty HTML or reserialize its initial document before the user has made any edits.

``isUsingSqliteDefault()`` - function
    Returns true if the insert dialog is currently set to omit this column and use the SQLite default.

``setValidity(message)`` - function
    Sets a custom validation message for this field, marks the backing input with ``aria-invalid="true"`` and shows the message in the field metadata area. Pass an empty string to clear the error.

``clearValidity()`` - function
    Clears any custom validation message previously set by ``setValidity()``.

Submitted value contract
^^^^^^^^^^^^^^^^^^^^^^^^

The ``field.setValue()`` method accepts the following value types:

* string
* number
* boolean
* ``null``

These values are used as column values in requests to the :ref:`insert rows <TableInsertView>` and :ref:`update row <RowUpdateView>` JSON APIs.

Plugins should not pass objects or arrays to ``field.setValue()``. If a column stores structured data in SQLite, such as JSON in a ``TEXT`` column, the plugin should serialize that data first and submit the serialized string. Client-side parsing can still be useful for validation or editor state, but the submitted value should match the SQLite value Datasette should write.

Value helpers
^^^^^^^^^^^^^

Custom fields should use ``field.getValue()`` and ``field.setValue(value)`` for value handling:

.. code-block:: javascript

    const currentValue = field.getValue();
    field.setValue("new value");
    field.setValue(null);

Plugins can keep the core input visible, wrap it in a custom element, or hide it and provide a richer interface. If the input is hidden, the custom UI must still expose an accessible name, state and keyboard interaction.

``field.setValue()`` updates both ``field.input`` and the value used in the insert/update request.

For example, a file picker that stores a selected file ID can hide the backing input and call ``field.setValue()`` when the selection changes:

.. code-block:: javascript

    field.input.type = "hidden";
    field.setValue(fileId);

For insert forms with a SQLite default, ``field.isUsingSqliteDefault()`` indicates whether Datasette will omit that column from the insert payload. Calling ``field.setValue(value)`` automatically stops using the SQLite default.

Lazy loading large controls
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The JavaScript file that registers ``makeColumnField()`` should be small. If the actual control is large, load it from inside ``render()`` using dynamic ``import()``. That way the heavier code is only downloaded after a user opens an insert/edit dialog containing a matching column type.

.. code-block:: javascript

    const editorUrl = new URL("./editor.js", import.meta.url).href;

    document.addEventListener("datasette_init", function (event) {
      event.detail.registerPlugin("my-editor", {
        version: "0.1",

        makeColumnField(context) {
          if (!context.columnType || context.columnType.type !== "my-editor") {
            return;
          }
          return {
            useTextarea: true,
            render(field) {
              import(editorUrl).then(function () {
                // Enhance field.input here.
              });
              return field.input;
            }
          };
        }
      });
    });

Example: textarea-backed custom element
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This example handles a ``markdown-editor`` column type by asking Datasette for a textarea and wrapping that textarea in a custom ``<my-markdown-editor>`` Web Component element:

.. code-block:: javascript

    document.addEventListener("datasette_init", function (event) {
      event.detail.registerPlugin("markdown-editor", {
        version: "0.1",

        makeColumnField(context) {
          if (!context.columnType || context.columnType.type !== "markdown-editor") {
            return;
          }

          return {
            useTextarea: true,

            render(field) {
              const editor = document.createElement("my-markdown-editor");
              editor.appendChild(field.input);

              if (field.labelId) {
                field.input.setAttribute("aria-labelledby", field.labelId);
              }
              if (field.descriptionId) {
                field.input.setAttribute("aria-describedby", field.descriptionId);
              }

              return editor;
            },

            focus(field) {
              const editor = field.root.querySelector("my-markdown-editor");
              if (editor && editor.focus) {
                editor.focus();
              } else {
                field.input.focus();
              }
            }
          };
        }
      });
    });

Accessibility
^^^^^^^^^^^^^

Custom fields are responsible for preserving the accessibility of the form:

- The visible field label should name the control. Use ``field.labelId`` with ``aria-labelledby`` when wrapping or replacing the visible input.
- Field metadata should remain available to assistive technology. Use ``field.descriptionId`` with ``aria-describedby``.
- Keyboard users must be able to operate every part of the custom field.
- If the field opens an inline picker or other nested UI, ``Escape`` should close that nested UI first and return focus to a sensible element.
- If a control performs asynchronous loading, expose loading and error states in the UI. Use appropriate ARIA live regions where the state change is important to understand the field.
- If a plugin hides ``field.input``, the replacement UI must still make the current value and available actions clear.

Plugins should not submit the row themselves from inside ``makeColumnField()`` controls. Datasette owns the insert/edit dialog lifecycle, form submission, API call, error handling and row refresh.

.. _javascript_plugins_modals:

Reusable modal dialogs
----------------------

Plugins can use ``DatasetteModal`` to create dialogs with the same appearance and keyboard behavior as Datasette's built-in dialogs. The component provides a native modal dialog, shared styles, Escape and backdrop dismissal, busy-state dismissal guards and focus restoration.

Use the :ref:`javascript_datasette_init` event to set up a dialog, as in this example.

Creating a dialog
~~~~~~~~~~~~~~~~~

``DatasetteModal.create()`` returns a detached ``<datasette-modal>`` element containing a native ``<dialog>``. Access that native element through ``modal.dialog``. Populate its content before appending the wrapper to the page, then call ``modal.show()`` to open it.

This example adds a button that opens a reusable dialog:

.. code-block:: javascript

    document.addEventListener("datasette_init", () => {
        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.textContent = "Open example dialog";
        // Indicate that this button opens a dialog:
        trigger.setAttribute("aria-haspopup", "dialog");
        // Identify which dialog it controls:
        trigger.setAttribute("aria-controls", "my-plugin-dialog");

        const modal = DatasetteModal.create();
        const dialog = modal.dialog;
        dialog.id = "my-plugin-dialog";
        dialog.setAttribute("aria-labelledby", "my-plugin-dialog-title");
        dialog.innerHTML = `
          <div class="modal-header">
            <h2 class="modal-title" id="my-plugin-dialog-title">
              Example dialog
            </h2>
          </div>
          <div class="modal-body">
            This dialog uses Datasette's shared styles and keyboard behavior.
          </div>
          <div class="modal-footer">
            <button type="button" class="modal-btn modal-btn-ghost">Close</button>
          </div>`;

        const closeButton = dialog.querySelector("button");
        closeButton.addEventListener("click", () => {
            modal.requestClose("cancel");
        });
        trigger.addEventListener("click", () => {
            modal.show({ trigger, initialFocus: closeButton });
        });

        document.body.append(modal);
        document.querySelector("section.content").append(trigger);
    });

The example uses ``innerHTML`` for a static template. Use ``textContent`` when inserting database values or other user-supplied text. Give each dialog and its title unique IDs, and use ``aria-labelledby`` or ``aria-label`` to provide an accessible name.

Opening and closing
~~~~~~~~~~~~~~~~~~~

``modal.show({trigger, initialFocus})``
    Opens the native dialog using ``showModal()``. Both options are optional. ``trigger`` is the element to return focus to when the dialog closes; it defaults to the currently focused element. ``initialFocus`` can be an element to focus or a function that focuses a custom control. Without it, the browser chooses initial focus. Calling ``show()`` again while the dialog is open does not change where focus returns when it closes. For example, if an Edit button opened the dialog, focus will still return to that button.

``modal.requestClose(source)``
    Requests dismissal through the busy-state and ``beforeClose`` guards described below. Returns ``true`` if it closes the dialog, or ``false`` if the dialog is already closed or a guard prevents dismissal. Close and Cancel buttons should use this method.

    ``source`` is an optional string identifying what requested dismissal. It is passed to ``beforeClose`` and is never displayed to the user. Datasette supplies ``"escape"`` for the Escape key or a native cancel event and ``"backdrop"`` for a click outside the dialog. Calls to ``requestClose()`` default to ``"cancel"``; callers can supply any other string as their own identifier.

``modal.close({restoreFocus = true})``
    Closes the dialog directly, bypassing the guards. Use this after successfully completing an operation. Pass ``restoreFocus: false`` when your code will navigate away or move focus to another element, such as a newly inserted row.

On normal dismissal, the component restores focus if the trigger is still connected to the document. If the trigger is inside a menu implemented with a closed ``<details>`` element, focus returns to that menu's ``<summary>`` instead.

Closing a dialog leaves it in the page so it can be reopened. Listen for the native dialog's ``close`` event to clean up resources such as pending requests or custom fields:

.. code-block:: javascript

    modal.dialog.addEventListener("close", () => {
        // Clean up content-specific resources here.
    });

If the dialog is no longer needed, remove the wrapper with ``modal.remove()``. The component removes its own listeners and pending keyboard-dismissal callbacks when disconnected.

Dismissal guards and busy state
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Set ``modal.beforeClose`` to a synchronous function that receives the ``source`` string described above and returns ``false`` to keep the dialog open. Your callback can apply different policies to each source, such as prompting before discarding edits on Escape while allowing an explicit Cancel button to close immediately. Any message shown to the user is supplied by your callback, separately from the source identifier:

.. code-block:: javascript

    modal.beforeClose = (source) => {
        if (source === "cancel") return true;
        return confirm("Discard unsaved changes?");
    };

The callback must return synchronously: returning a Promise does not delay dismissal. For an asynchronous confirmation, return ``false`` immediately and call ``modal.close()`` yourself if the user later confirms.

Set ``modal.busy = true`` while saving to prevent user dismissal. This also sets ``aria-busy="true"`` on the native dialog. While busy, ``requestClose()`` returns ``false`` without calling ``beforeClose``. Busy state resets when the dialog closes.

The plugin remains responsible for disabling its form controls, submitting data and displaying progress and errors. If an operation fails, set ``modal.busy = false`` so the user can retry or close the dialog. A successful operation can call ``modal.close()`` even while busy.

Escape dismissal waits until the key is released before consulting the guard, so a discard-confirmation prompt remains usable in Safari. Nested controls, such as an autocomplete list, can consume Escape with ``event.preventDefault()`` to keep the containing dialog open.

.. _javascript_plugins_modal_classes:

Shared CSS classes
~~~~~~~~~~~~~~~~~~

The classes in the example provide built-in styling. None of the classes you add to the dialog's content is required for opening, closing or focus handling.

``datasette-modal``
    Added automatically to the native ``<dialog>`` when the wrapper is connected to the page. Provides the dialog's sizing, background, rounded corners, shadow, backdrop and animations. Keep this class when adding your own styles.

``modal-header``
    Adds padding, a bottom border and a horizontal layout for the title and optional metadata.

``modal-title``
    Sets the title's font size, weight and color. This class only changes its appearance; use ``aria-labelledby`` to associate the title with the dialog.

``modal-meta``
    Styles optional metadata, such as a selected-item count, as small monospace text with a rounded background.

``modal-body``
    Adds padding and makes overflowing content scroll while the header and footer remain visible. Sets ``min-height: 0``, ``overflow: auto`` and ``padding: 16px 24px 24px``. Add your own layout rules, such as ``display: grid`` and ``gap``, or override the padding for content such as a list.

``modal-footer``
    Adds padding, a top border and a background to the action area. Arranges its contents horizontally, with buttons aligned to the right.

``footer-info``
    Styles supporting text in the footer and lets it fill the space before the action buttons.

``modal-btn``
    Provides base button styling, including padding, rounded corners, font and disabled appearance. Use it together with ``modal-btn-primary`` or ``modal-btn-ghost``.

``modal-btn-primary``
    Gives a button an accent-colored background and white text, suitable for a primary action such as Save.

``modal-btn-ghost``
    Gives a button a transparent background, muted text and a border, suitable for a secondary action such as Close or Cancel.

These button classes are also used by Datasette's built-in dialogs. The ``modal-btn`` prefix keeps them separate from generic ``btn`` classes used by plugins or CSS frameworks. The shared CSS scopes them to descendants of ``.datasette-modal``, for example ``:where(.datasette-modal) .modal-btn``. Dialog content remains in the caller's DOM tree, so scope custom CSS to the intended component to avoid unintended overrides.

You can customize layout and sizing without adding extra classes. For example, this CSS uses the dialog's existing ID to widen it while keeping it inside the viewport:

.. code-block:: css

    dialog#my-plugin-dialog {
        width: min(720px, calc(100vw - 32px));
    }

Use ``modal-body`` on the scrolling content container. If that container is inside a form that also contains the footer, the form needs ``display: flex``, ``flex-direction: column``, ``flex: 1 1 auto`` and ``min-height: 0`` so its content can shrink within the dialog.

The dialog shell also uses the CSS custom properties ``--modal-border-radius``, ``--modal-shadow``, ``--modal-backdrop-bg``, ``--modal-backdrop-blur`` and ``--modal-animation-duration``. The shared animations respect the user's reduced-motion preference.

.. _javascript_datasette_manager_selectors:

Selectors
---------

These are available on the ``selectors`` property of the :ref:`javascript_datasette_manager` object.

.. literalinclude:: ../datasette/static/datasette-manager.js
   :language: javascript
   :start-at: const DOM_SELECTORS = {
   :end-at: };
