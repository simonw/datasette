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

``selectors`` - object
    An object providing named aliases to useful CSS selectors, :ref:`listed below <javascript_datasette_manager_selectors>`

.. _javascript_plugin_objects:

JavaScript plugin objects
-------------------------

JavaScript plugins are blocks of code that can be registered with Datasette using the ``registerPlugin()`` method on the :ref:`datasetteManager <javascript_datasette_manager>` object.

The ``implementation`` object passed to this method should include a ``version`` key defining the plugin version, and one or more of the following named functions providing the implementation of the plugin:

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

.. _javascript_datasette_manager_selectors:

Selectors
---------

These are available on the ``selectors`` property of the :ref:`javascript_datasette_manager` object.

.. literalinclude:: ../datasette/static/datasette-manager.js
   :language: javascript
   :start-at: const DOM_SELECTORS = {
   :end-at: };
