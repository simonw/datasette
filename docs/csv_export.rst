.. _csv_export:

CSV export
==========

Any Datasette table, view or custom SQL query can be exported as CSV.

To obtain the CSV representation of the table you are looking at, click the
"Export data" button below the filters. The dialog that opens offers the data
in different JSON shapes and as CSV, using the current filters and sort order.
Custom SQL query pages have the same button above their results.

The top of the dialog describes the filters and sort order that the export
will use. Each format shows how many rows it returns: JSON formats return the
rows on the current page, CSV returns up to :ref:`setting_max_returned_rows`
rows, and the **stream all rows** option returns every matching row. If the
page did not count every row, the dialog counts them when it opens.

Each format in that dialog has four actions:

* **View** - open that format in your browser.
* **Download** - save it as a file in your downloads directory.
* **Copy** - copy the data itself to your clipboard.
* **Copy URL** - copy the URL for that format, for use with other tools.

The two copy actions use the browser clipboard, so they are only shown when
Datasette is served over HTTPS or from ``localhost``.

The CSV format has the following options. These are only offered for tables
and views:

* **expand labels** - if your table has any foreign key references this option
  will cause the CSV to gain additional ``COLUMN_NAME_label`` columns with a
  label for each foreign key derived from the linked table. `In this example
  <https://latest.datasette.io/fixtures/facetable.csv?_labels=on&_size=max>`_
  the ``city_id`` column is accompanied by a ``city_id_label`` column.

* **stream all rows** - by default CSV files only contain the first
  :ref:`setting_max_returned_rows` records. This option will cause Datasette to
  loop through every matching record and return them as a single CSV file.

You can try that out on https://latest.datasette.io/fixtures/facetable?_size=4

.. _csv_export_url_parameters:

URL parameters
--------------

The following options can be used to customize the CSVs returned by Datasette.

``?_header=off``
    This removes the first row of the CSV file specifying the headings - only the row data will be returned.

``?_stream=on``
    Stream all matching records, not just the first page of results. See below.

``?_dl=on``
    Causes Datasette to return a ``content-disposition: attachment; filename="filename.csv"`` header.

Streaming all records
---------------------

The *stream all rows* option is designed to be as efficient as possible -
under the hood it takes advantage of Python 3 asyncio capabilities and
Datasette's efficient :ref:`pagination <pagination>` to stream back the full
CSV file.

Since databases can get pretty large, by default this option is capped at 100MB -
if a table returns more than 100MB of data the last line of the CSV will be a
truncation error message.

You can increase or remove this limit using the :ref:`setting_max_csv_mb` config
setting. You can also disable the CSV export feature entirely using
:ref:`setting_allow_csv_stream`.
