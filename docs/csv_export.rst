.. _csv_export:

CSV and TSV export
==================

Any Datasette table, view or custom SQL query can be exported as CSV or TSV.

To export the table you are looking at, click the "Export data" button below
the filters. The dialog that opens offers the data as TSV, as CSV and in
different JSON shapes, using the current filters and sort order. Custom SQL
query pages have the same button above their results, and each row page has
one that offers that row as TSV, CSV or JSON.

The top of the dialog describes the filters and sort order that the export
will use. Each format shows how many rows it returns: JSON formats return the
rows on the current page, TSV and CSV return up to
:ref:`setting_max_returned_rows` rows starting from the current page, and the
**include all rows** option returns every matching row, starting from the
first one. If you have moved
past the first page the dialog says so, since the exports then start from
that page. If the page did not count every row, the dialog counts them when
it opens.

Each format in that dialog has four actions:

* **Copy** - copy the data itself to your clipboard.
* **View** - open that format in your browser.
* **Download** - save it as a file in your downloads directory.
* **Copy URL** - copy the URL for that format, for use with other tools.

Formats added by plugins can offer fewer of these. A binary format, such as a
spreadsheet file, offers **Download** and **Copy URL**, and a format that opens
a web page offers **View** and **Copy URL**. See
:ref:`plugin_register_output_renderer`.

The two copy actions use the browser clipboard, so they are only shown when
Datasette is served over HTTPS or from ``localhost``.

.. _csv_export_tsv:

TSV for spreadsheets
--------------------

TSV (tab-separated values) is the quickest way to get data into Excel or
Google Sheets: click **Copy** next to TSV, then paste into a spreadsheet and
each value lands in its own cell. This is what those applications put on the
clipboard themselves when you copy a range of cells.

TSV uses the same quoting rules as Excel. A value that contains a tab, a line
break or a double quote is wrapped in double quotes, with any double quotes
inside it doubled, so ``say "hi"`` becomes ``"say ""hi"""``. Excel and Google
Sheets turn those values back into the original text when you paste them.

TSV is available at the same URLs as CSV, with ``.tsv`` in place of ``.csv``,
and accepts the same options and URL parameters. If a plugin registers its own
``tsv`` format using :ref:`plugin_register_output_renderer`, that plugin
handles ``.tsv`` URLs instead.

Options
-------

TSV and CSV share the following options. These are only offered for tables
and views:

* **expand labels** - if your table has any foreign key references this option
  will cause the export to gain additional ``COLUMN_NAME_label`` columns with a
  label for each foreign key derived from the linked table. `In this example
  <https://latest.datasette.io/fixtures/facetable.csv?_labels=on&_size=max>`_
  the ``city_id`` column is accompanied by a ``city_id_label`` column.

* **include all rows** - by default TSV and CSV exports only contain the first
  :ref:`setting_max_returned_rows` records. This option will cause Datasette to
  loop through every matching record and return them as a single file,
  starting from the first one even if you are on a later page. On the first
  page it is only offered when there are more matching rows than that.

You can try that out on https://latest.datasette.io/fixtures/facetable?_size=4

.. _csv_export_url_parameters:

URL parameters
--------------

The following options can be used to customize the CSV and TSV files returned
by Datasette.

``?_header=off``
    This removes the first row of the CSV file specifying the headings - only the row data will be returned.

``?_stream=on``
    Stream all matching records, not just the first page of results. See below.

``?_dl=on``
    Causes Datasette to return a ``content-disposition: attachment; filename="filename.csv"`` header, or ``filename.tsv`` for TSV.

Custom SQL query CSV responses include a ``Datasette-Truncated`` response
header with the value ``true`` or ``false``. A value of ``true`` means rows
were omitted because of the :ref:`setting_max_returned_rows` limit. The
``Datasette-Max-Returned-Rows`` header reports that limit, including ``0``
when it is disabled. These headers are also available to cross-origin browser
clients when CORS is enabled.

Streaming all records
---------------------

The *include all rows* option is designed to be as efficient as possible -
under the hood it takes advantage of Python 3 asyncio capabilities and
Datasette's efficient :ref:`pagination <pagination>` to stream back the full
CSV or TSV file.

Since databases can get pretty large, by default this option is capped at 100MB -
if a table returns more than 100MB of data the last line of the file will be a
truncation error message.

You can increase or remove this limit using the :ref:`setting_max_csv_mb` config
setting. You can also disable the CSV export feature entirely using
:ref:`setting_allow_csv_stream`.
