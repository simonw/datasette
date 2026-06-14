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

makeJumpSections()
~~~~~~~~~~~~~~~~~~

This method should return a JavaScript array of objects defining additional sections to be added to the blank state of the ``/`` jump menu, before the user starts typing a search.

Each object should have the following:

``id`` - string
    A unique string ID for the section, for example ``agent-chat``
``render(node, context)`` - function
    A function that will be called with a DOM node to render the section into

The ``context`` object has the following keys:

``navigationSearch``
    The ``<navigation-search>`` custom element instance.

This example shows how a plugin might add a button for starting a new chat:

.. code-block:: javascript

    document.addEventListener('datasette_init', function(ev) {
      ev.detail.registerPlugin('agent-plugin', {
        version: 0.1,
        makeJumpSections: () => {
          return [
            {
              id: 'agent-chat',
              render: node => {
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

.. _javascript_datasette_manager_selectors:

Selectors
---------

These are available on the ``selectors`` property of the :ref:`javascript_datasette_manager` object.

.. literalinclude:: ../datasette/static/datasette-manager.js
   :language: javascript
   :start-at: const DOM_SELECTORS = {
   :end-at: };
