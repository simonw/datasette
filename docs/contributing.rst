.. _contributing:

Contributing
============

Datasette is an open source project. We welcome contributions!

This document describes how to contribute to Datasette core. You can also contribute to the wider Datasette ecosystem by creating new :ref:`plugins`.

General guidelines
------------------

* **master should always be releasable**. Incomplete features should live in branches. This ensures that any small bug fixes can be quickly released.
* **The ideal commit** should bundle together the implementation, unit tests and associated documentation updates. The commit message should link to an associated issue.
* **New plugin hooks** should only be shipped if accompanied by a separate release of a non-demo plugin that uses them.

.. _devenvironment:

Setting up a development environment
------------------------------------

If you have Python 3.6 or higher installed on your computer (on OS X the easiest way to do this `is using homebrew <https://docs.python-guide.org/starting/install3/osx/>`__) you can install an editable copy of Datasette using the following steps.

If you want to use GitHub to publish your changes, first `create a fork of datasette <https://github.com/simonw/datasette/fork>`__ under your own GitHub account.

Now clone that repository somewhere on your computer::

    git clone git@github.com:YOURNAME/datasette

If you just want to get started without creating your own fork, you can do this instead::

    git clone git@github.com:simonw/datasette

The next step is to create a virtual environment for your project and use it to install Datasette's dependencies::

    cd datasette
    # Create a virtual environment in ./venv
    python3 -m venv ./venv
    # Now activate the virtual environment, so pip can install into it
    source venv/bin/activate
    # Install Datasette and its testing dependencies
    python3 -m pip install -e .[test]

That last line does most of the work: ``pip install -e`` means "install this package in a way that allows me to edit the source code in place". The ``.[test]`` option means "use the setup.py in this directory and install the optional testing dependencies as well".

Once you have done this, you can run the Datasette unit tests from inside your ``datasette/`` directory using `pytest <https://docs.pytest.org/>`__ like so::

    pytest

To run Datasette itself, just type ``datasette``.

You're going to need at least one SQLite database. An easy way to get started is to use the fixtures database that Datasette uses for its own tests.

You can create a copy of that database by running this command::

    python tests/fixtures.py fixtures.db

Now you can run Datasette against the new fixtures database like so::

    datasette fixtures.db

This will start a server at ``http://127.0.0.1:8001/``.

Any changes you make in the ``datasette/templates`` or ``datasette/static`` folder will be picked up immediately (though you may need to do a force-refresh in your browser to see changes to CSS or JavaScript).

If you want to change Datasette's Python code you can use the ``--reload`` option to cause Datasette to automatically reload any time the underlying code changes::

    datasette --reload fixtures.db

You can also use the ``fixtures.py`` script to recreate the testing version of ``metadata.json`` used by the unit tests. To do that::

    python tests/fixtures.py fixtures.db fixtures-metadata.json

Or to output the plugins used by the tests, run this::

    python tests/fixtures.py fixtures.db fixtures-metadata.json fixtures-plugins
    Test tables written to fixtures.db
    - metadata written to fixtures-metadata.json
    Wrote plugin: fixtures-plugins/register_output_renderer.py
    Wrote plugin: fixtures-plugins/view_name.py
    Wrote plugin: fixtures-plugins/my_plugin.py
    Wrote plugin: fixtures-plugins/messages_output_renderer.py
    Wrote plugin: fixtures-plugins/my_plugin_2.py

Then run Datasette like this::

    datasette fixtures.db -m fixtures-metadata.json --plugins-dir=fixtures-plugins/

.. _contributing_debugging:

Debugging
---------

Any errors that occur while Datasette is running while display a stack trace on the console.

You can tell Datasette to open an interactive ``pdb`` debugger session if an error occurs using the ``--pdb`` option::

    datasette --pdb fixtures.db

.. _contributing_documentation:

Editing and building the documentation
--------------------------------------

Datasette's documentation lives in the ``docs/`` directory and is deployed automatically using `Read The Docs <https://readthedocs.org/>`__.

The documentation is written using reStructuredText. You may find this article on `The subset of reStructuredText worth committing to memory <https://simonwillison.net/2018/Aug/25/restructuredtext/>`__ useful.

You can build it locally by installing ``sphinx`` and ``sphinx_rtd_theme`` in your Datasette development environment and then running ``make html`` directly in the ``docs/`` directory::

    # You may first need to activate your virtual environment:
    source venv/bin/activate

    # Install the dependencies needed to build the docs
    pip install -e .[docs]

    # Now build the docs
    cd docs/
    make html

This will create the HTML version of the documentation in ``docs/_build/html``. You can open it in your browser like so::

    open _build/html/index.html

Any time you make changes to a ``.rst`` file you can re-run ``make html`` to update the built documents, then refresh them in your browser.

For added productivity, you can use use `sphinx-autobuild <https://pypi.org/project/sphinx-autobuild/>`__ to run Sphinx in auto-build mode. This will run a local webserver serving the docs that automatically rebuilds them and refreshes the page any time you hit save in your editor.

``sphinx-autobuild`` will have been installed when you ran ``pip install -e .[docs]``. In your ``docs/`` directory you can start the server by running the following::

    make livehtml

Now browse to ``http://localhost:8000/`` to view the documentation. Any edits you make should be instantly reflected in your browser.

.. _contributing_release:

Release process
---------------

Datasette releases are performed using tags. When a new release is published on GitHub, a `GitHub Action workflow <https://github.com/simonw/datasette/blob/main/.github/workflows/deploy-latest.yml>`__ will perform the following:

* Run the unit tests against all supported Python versions. If the tests pass...
* Build a Docker image of the release and push a tag to https://hub.docker.com/r/datasetteproject/datasette
* Re-point the "latest" tag on Docker Hub to the new image
* Build a wheel bundle of the underlying Python source code
* Push that new wheel up to PyPI: https://pypi.org/project/datasette/

To deploy new releases you will need to have push access to the main Datasette GitHub repository.

Datasette follows `Semantic Versioning <https://semver.org/>`__::

    major.minor.patch

We increment ``major`` for backwards-incompatible releases. Datasette is currently pre-1.0 so the major version is always ``0``.

We increment ``minor`` for new features.

We increment ``patch`` for bugfix releass.

:ref:`contributing_alpha_beta` may have an additional ``a0`` or ``b0`` prefix - the integer component will be incremented with each subsequent alpha or beta.

To release a new version, first create a commit that updates the version number in ``datasette/version.py`` and the :ref:`the changelog <changelog>` with highlights of the new version. An example `commit can be seen here <https://github.com/simonw/datasette/commit/0e1e89c6ba3d0fbdb0823272952cf356f3016def>`__::

    # Update changelog
    git commit -m " Release 0.51a1

    Refs #1056, #1039, #998, #1045, #1033, #1036, #1034, #976, #1057, #1058, #1053, #1064, #1066" -a
    git push

Referencing the issues that are part of the release in the commit message ensures the name of the release shows up on those issue pages, e.g. `here <https://github.com/simonw/datasette/issues/581#ref-commit-d56f402>`__.

You can generate the list of issue references for a specific release by pasting the following into the browser devtools while looking at the :ref:`changelog` page (replace ``v0-44`` with the most recent version):

.. code-block:: javascript

    [
        ...new Set(
            Array.from(
                document.getElementById("v0-44").querySelectorAll("a[href*=issues]")
            ).map((a) => "#" + a.href.split("/issues/")[1])
        ),
    ].sort().join(", ");

For non-bugfix releases you may want to update the news section of ``README.md`` as part of the same commit.

To tag and push the releaes, run the following::

    git tag 0.25.2
    git push --tags

Final steps once the release has deployed to https://pypi.org/project/datasette/

* Manually post the new release to GitHub releases: https://github.com/simonw/datasette/releases - you can convert the release notes to Markdown by copying and pasting the rendered HTML into this tool: https://euangoddard.github.io/clipboard2markdown/
* Manually kick off a build of the `stable` branch on Read The Docs: https://readthedocs.org/projects/datasette/builds/

.. _contributing_alpha_beta:

Alpha and beta releases
-----------------------

Alpha and beta releases are published to preview upcoming features that may not yet be stable - in particular to preview new plugin hooks.

You are welcome to try these out, but please be aware that details may change before the final release.

Please join `discussions on the issue tracker <https://github.com/simonw/datasette/issues>`__ to share your thoughts and experiences with on alpha and beta features that you try out.

.. _contributing_upgrading_codemirror:

Upgrading CodeMirror
--------------------

Datasette bundles `CodeMirror <https://codemirror.net/>`__ for the SQL editing interface, e.g. on `this page <https://latest.datasette.io/fixtures>`__. Here are the steps for upgrading to a new version of CodeMirror:

* Download and extract latest CodeMirror zip file from https://codemirror.net/codemirror.zip
* Rename ``lib/codemirror.js`` to ``codemirror-5.57.0.js`` (using latest version number)
* Rename ``lib/codemirror.css`` to ``codemirror-5.57.0.css``
* Rename ``mode/sql/sql.js`` to ``codemirror-5.57.0-sql.js``
* Edit both JavaScript files to make the top license comment a ``/* */`` block instead of multiple ``//`` lines
* Minify the JavaScript files like this::

       npx uglify-js codemirror-5.57.0.js -o codemirror-5.57.0.min.js --comments '/LICENSE/'
       npx uglify-js codemirror-5.57.0-sql.js -o codemirror-5.57.0-sql.min.js --comments '/LICENSE/'

* Check that the LICENSE comment did indeed survive minification
* Minify the CSS file like this::

       npx clean-css-cli codemirror-5.57.0.css -o codemirror-5.57.0.min.css

* Edit the ``_codemirror.html`` template to reference the new files
* ``git rm`` the old files, ``git add`` the new files
