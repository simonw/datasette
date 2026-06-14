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

It is designed for plugins that register custom column types using the Python ``register_column_types()`` plugin hook. For example, a plugin that defines a ``file`` column type can use ``makeColumnField()`` to replace a plain text input with a file picker, and a plugin that defines a rich text column type can use it to enhance the field with an editor.

Datasette calls ``makeColumnField(context)`` on each registered JavaScript plugin when it renders an editable insert/edit field. Plugins should inspect the ``context`` object and return ``null`` or ``undefined`` for fields they do not handle.

The first plugin to return a truthy control object is used for that field. Plugins are called in registration order. If a plugin raises an exception, Datasette logs the error to the browser console and continues to the next plugin.

The value that Datasette submits is still read from the core-owned input or textarea provided to the plugin as ``field.input``. This keeps custom fields progressive: the plugin can render any UI it needs, but it must keep ``field.input.value`` synchronized with the raw value that should be sent to the insert/update API.

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
    The path to the table page, including any configured base URL prefix.

``column`` - string
    The column name.

``value``
    The current JavaScript value for the field. For edit forms this is the row's current value. For insert forms this is usually ``null`` or ``""``.

``originalValue``
    The value the field had when the form was opened. This currently matches ``value``.

``columnType`` - object or null
    The configured Datasette column type for this column, if one exists. This object includes a ``type`` key containing the column type name. Plugins should generally check ``context.columnType && context.columnType.type`` before deciding whether to handle a field.

``sqliteType`` - string or null
    The SQLite column type, if known.

``notNull`` - boolean
    True if the column is defined as ``NOT NULL``.

``isPrimaryKey`` - boolean
    True if this column is part of the table's primary key.

``readOnly`` - boolean
    True if Datasette is rendering the field as read-only. Primary key fields are read-only in edit forms by default.

``hasDefault`` - boolean
    True if the column has a SQLite default value and the insert form can offer the "use default" behavior.

``defaultValue``
    The column default value or expression, if available.

``form`` - ``HTMLFormElement`` or null
    The row insert/edit form element.

``dialog`` - ``HTMLDialogElement`` or null
    The modal dialog element.

Returned control object
^^^^^^^^^^^^^^^^^^^^^^^

A plugin that wants to handle a field should return an object. Datasette currently recognizes these properties:

``inputType`` - string, optional
    If set to ``"textarea"``, Datasette creates a ``<textarea>`` as the underlying ``field.input`` before calling ``render()``. Any other value is ignored.

``render(node, field)`` - function
    Called once to render the custom field UI. ``node`` is an empty container element created by Datasette. ``field`` is a helper object described below.

    The plugin should append its UI to ``node``. If ``render()`` returns a DOM node, Datasette appends that returned node to ``node``.

``focus(node, field)`` - function, optional
    Called when Datasette wants to focus this field, for example when focusing the first editable field in the dialog. Use this to focus the most useful interactive element inside the custom UI.

``destroy(node, field)`` - function, optional
    Called when Datasette tears down the insert/edit form. Use this to remove event listeners, close nested pickers, revoke object URLs, clear timers, or release other resources.

Datasette adds a ``pluginName`` property to the control object internally, based on the name passed to ``registerPlugin()``.

The field helper object
^^^^^^^^^^^^^^^^^^^^^^^

The second argument to ``render(node, field)`` provides the core input and stable IDs that help the plugin integrate with the modal's form and accessibility behavior:

``id`` - string
    The ID Datasette assigned to the underlying form control.

``labelId`` - string
    The ID of the visible field label.

``descriptionId`` - string
    The ID of the field metadata/help text. This metadata can include details such as ``Primary key``, ``Required``, ``Current value: NULL`` or ``Custom type: file``.

``input`` - ``HTMLInputElement`` or ``HTMLTextAreaElement``
    The core-owned form control. Datasette reads this element's ``name``, ``value`` and ``dataset`` properties when the row is inserted or updated.

``control``
    An alias for ``input``.

``form`` - ``HTMLFormElement`` or null
    The containing row insert/edit form.

``dialog`` - ``HTMLDialogElement`` or null
    The containing modal dialog.

``context`` - object
    The original context object passed to ``makeColumnField()``.

Value handling
^^^^^^^^^^^^^^

Custom fields should keep ``field.input.value`` synchronized with the raw value to submit.

If a custom field changes the value programmatically, it should dispatch normal ``input`` and ``change`` events so the rest of the form can observe the update:

.. code-block:: javascript

    function setInputValue(input, value) {
      input.value = value || "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

Plugins can either keep the core input visible, wrap it in a custom element, or hide it and provide a richer interface. If the input is hidden, the custom UI must still expose an accessible name, state and keyboard interaction.

If the plugin changes the kind of value stored in the underlying input, it can adjust ``field.input.dataset.originalValueType``. Datasette uses that dataset value when converting the submitted text back to a JSON value for the insert/update API.

For example, a file picker that stores a string file ID can set:

.. code-block:: javascript

    field.input.type = "hidden";
    field.input.dataset.originalValueType = "null";

This causes an empty string to be submitted as ``null``.

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
            return null;
          }
          return {
            inputType: "textarea",
            render(node, field) {
              node.appendChild(field.input);
              import(editorUrl).then(function () {
                // Enhance field.input here.
              });
            }
          };
        }
      });
    });

Example: textarea-backed custom element
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This example handles a ``markdown-editor`` column type by asking Datasette for a textarea and wrapping that textarea in a custom element:

.. code-block:: javascript

    document.addEventListener("datasette_init", function (event) {
      event.detail.registerPlugin("markdown-editor", {
        version: "0.1",

        makeColumnField(context) {
          if (!context.columnType || context.columnType.type !== "markdown-editor") {
            return null;
          }

          return {
            inputType: "textarea",

            render(node, field) {
              const editor = document.createElement("my-markdown-editor");
              editor.appendChild(field.input);
              node.appendChild(editor);

              if (field.labelId) {
                field.input.setAttribute("aria-labelledby", field.labelId);
              }
              if (field.descriptionId) {
                field.input.setAttribute("aria-describedby", field.descriptionId);
              }
            },

            focus(node, field) {
              const editor = node.querySelector("my-markdown-editor");
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

Example: hidden input with custom picker
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This example handles an ``asset`` column type by hiding the core input and writing an asset ID into it when the user selects an item:

.. code-block:: javascript

    document.addEventListener("datasette_init", function (event) {
      event.detail.registerPlugin("asset-picker", {
        version: "0.1",

        makeColumnField(context) {
          if (!context.columnType || context.columnType.type !== "asset") {
            return null;
          }

          return {
            render(node, field) {
              field.input.type = "hidden";
              field.input.dataset.originalValueType = "null";

              const group = document.createElement("div");
              group.setAttribute("role", "group");
              group.setAttribute("aria-labelledby", field.labelId);
              group.setAttribute("aria-describedby", field.descriptionId);

              const current = document.createElement("span");
              current.textContent = field.input.value || "No asset selected";

              const button = document.createElement("button");
              button.type = "button";
              button.textContent = "Choose asset";
              button.addEventListener("click", async function () {
                const assetId = await chooseAsset();
                if (assetId === null) {
                  return;
                }
                field.input.value = assetId;
                field.input.dispatchEvent(new Event("input", { bubbles: true }));
                field.input.dispatchEvent(new Event("change", { bubbles: true }));
                current.textContent = assetId || "No asset selected";
              });

              group.appendChild(current);
              group.appendChild(button);
              node.appendChild(field.input);
              node.appendChild(group);
            },

            focus(node) {
              const button = node.querySelector("button");
              if (button) {
                button.focus();
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
