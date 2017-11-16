Changelog
=========

0.12 (2017-11-16)
-----------------
- Added ``__version__``, now displayed as tooltip in page footer (`#108`_).
- Added initial docs, including a changelog (`#99`_).
- Turned on auto-escaping in Jinja.
- Added a UI for editing named parameters (`#96`_).

  You can now construct a custom SQL statement using SQLite named
  parameters (e.g. ``:name``) and datasette will display form fields for
  editing those parameters. `Here’s an example`_ which lets you see the
  most popular names for dogs of different species registered through
  various dog registration schemes in Australia.

.. _Here’s an example: https://australian-dogs.now.sh/australian-dogs-3ba9628?sql=select+name%2C+count%28*%29+as+n+from+%28%0D%0A%0D%0Aselect+upper%28%22Animal+name%22%29+as+name+from+%5BAdelaide-City-Council-dog-registrations-2013%5D+where+Breed+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28Animal_Name%29+as+name+from+%5BAdelaide-City-Council-dog-registrations-2014%5D+where+Breed_Description+like+%3Abreed%0D%0A%0D%0Aunion+all+%0D%0A%0D%0Aselect+upper%28Animal_Name%29+as+name+from+%5BAdelaide-City-Council-dog-registrations-2015%5D+where+Breed_Description+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28%22AnimalName%22%29+as+name+from+%5BCity-of-Port-Adelaide-Enfield-Dog_Registrations_2016%5D+where+AnimalBreed+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28%22Animal+Name%22%29+as+name+from+%5BMitcham-dog-registrations-2015%5D+where+Breed+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28%22DOG_NAME%22%29+as+name+from+%5Bburnside-dog-registrations-2015%5D+where+DOG_BREED+like+%3Abreed%0D%0A%0D%0Aunion+all+%0D%0A%0D%0Aselect+upper%28%22Animal_Name%22%29+as+name+from+%5Bcity-of-playford-2015-dog-registration%5D+where+Breed_Description+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28%22Animal+Name%22%29+as+name+from+%5Bcity-of-prospect-dog-registration-details-2016%5D+where%22Breed+Description%22+like+%3Abreed%0D%0A%0D%0A%29+group+by+name+order+by+n+desc%3B&breed=pug

- Pin to specific Jinja version. (`#100`_).
- Default to 127.0.0.1 not 0.0.0.0. (`#98`_).
- Added extra metadata options to publish and package commands. (`#92`_).

  You can now run these commands like so::

      datasette now publish mydb.db \
          --title="My Title" \
          --source="Source" \
          --source_url="http://www.example.com/" \
          --license="CC0" \
          --license_url="https://creativecommons.org/publicdomain/zero/1.0/"

  This will write those values into the metadata.json that is packaged with the
  app. If you also pass ``--metadata=metadata.json`` that file will be updated with the extra
  values before being written into the Docker image.
- Added simple production-ready Dockerfile (`#94`_) [Andrew
  Cutler]
- New ``?_sql_time_limit_ms=10`` argument to database and table page (`#95`_)
- SQL syntax highlighting with Codemirror (`#89`_) [Tom Dyson]

.. _#89: https://github.com/simonw/datasette/issues/89
.. _#92: https://github.com/simonw/datasette/issues/92
.. _#94: https://github.com/simonw/datasette/issues/94
.. _#95: https://github.com/simonw/datasette/issues/95
.. _#96: https://github.com/simonw/datasette/issues/96
.. _#98: https://github.com/simonw/datasette/issues/98
.. _#99: https://github.com/simonw/datasette/issues/99
.. _#100: https://github.com/simonw/datasette/issues/100
.. _#108: https://github.com/simonw/datasette/issues/108

0.11 (2017-11-14)
-----------------
- Added ``datasette publish now --force`` option.

  This calls ``now`` with ``--force`` - useful as it means you get a fresh copy of datasette even if Now has already cached that docker layer.
- Enable ``--cors`` by default when running in a container.

0.10 (2017-11-14)
-----------------
- Fixed `#83`_ - 500 error on individual row pages.
- Stop using sqlite WITH RECURSIVE in our tests.

  The version of Python 3 running in Travis CI doesn't support this.

.. _#83: https://github.com/simonw/datasette/issues/83

0.9 (2017-11-13)
----------------
- Added ``--sql_time_limit_ms`` and ``--extra-options``.

  The serve command now accepts ``--sql_time_limit_ms`` for customizing the SQL time
  limit.

  The publish and package commands now accept ``--extra-options`` which can be used
  to specify additional options to be passed to the datasite serve command when
  it executes inside the resulting Docker containers.

0.8 (2017-11-13)
----------------
- V0.8 - added PyPI metadata, ready to ship.
- Implemented offset/limit pagination for views (`#70`_).
- Improved pagination. (`#78`_)
- Limit on max rows returned, controlled by ``--max_returned_rows`` option. (`#69`_)

  If someone executes 'select * from table' against a table with a million rows
  in it, we could run into problems: just serializing that much data as JSON is
  likely to lock up the server.

  Solution: we now have a hard limit on the maximum number of rows that can be
  returned by a query. If that limit is exceeded, the server will return a
  ``"truncated": true`` field in the JSON.

  This limit can be optionally controlled by the new ``--max_returned_rows``
  option. Setting that option to 0 disables the limit entirely.

.. _#70: https://github.com/simonw/datasette/issues/70
.. _#78: https://github.com/simonw/datasette/issues/78
.. _#69: https://github.com/simonw/datasette/issues/69
