# Datasette

[![PyPI](https://img.shields.io/pypi/v/datasette.svg)](https://pypi.org/project/datasette/)
[![Changelog](https://img.shields.io/github/v/release/simonw/datasette?label=changelog)](https://docs.datasette.io/en/stable/changelog.html)
[![Python 3.x](https://img.shields.io/pypi/pyversions/datasette.svg?logo=python&logoColor=white)](https://pypi.org/project/datasette/)
[![Tests](https://github.com/simonw/datasette/workflows/Test/badge.svg)](https://github.com/simonw/datasette/actions?query=workflow%3ATest)
[![Documentation Status](https://readthedocs.org/projects/datasette/badge/?version=latest)](https://docs.datasette.io/en/latest/?badge=latest)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/datasette/blob/main/LICENSE)
[![docker: datasette](https://img.shields.io/badge/docker-datasette-blue)](https://hub.docker.com/r/datasetteproject/datasette)

*An open source multi-tool for exploring and publishing data*

Datasette is a tool for exploring and publishing data. It helps people take data of any shape or size and publish that as an interactive, explorable website and accompanying API.

Datasette is aimed at data journalists, museum curators, archivists, local governments and anyone else who has data that they wish to share with the world.

[Explore a demo](https://fivethirtyeight.datasettes.com/fivethirtyeight), watch [a video about the project](https://www.youtube.com/watch?v=pTr1uLQTJNE) or try it out by [uploading and publishing your own CSV data](https://simonwillison.net/2019/Apr/23/datasette-glitch/).

* Comprehensive documentation: https://docs.datasette.io/
* Examples: https://github.com/simonw/datasette/wiki/Datasettes
* Live demo of current main: https://latest.datasette.io/
* Support questions, feedback? Join our [GitHub Discussions forum](https://github.com/simonw/datasette/discussions)

## News

 * 9th October 2020: [Datasette 0.50](https://docs.datasette.io/en/stable/changelog.html#v0-50) - New column actions menu. `datasette.client` object for plugins to make internal API requests. Improved documentation on deploying Datasette. [Annotated release notes](https://simonwillison.net/2020/Oct/9/datasette-0-50/).
 * 14th September 2020: [Datasette 0.49](https://docs.datasette.io/en/stable/changelog.html#v0-49) - JSON API for writable canned queries, path parameters for custom pages. See also [Datasette 0.49: The annotated release notes](https://simonwillison.net/2020/Sep/15/datasette-0-49/).
 * 16th August 2020: [Datasette 0.48](https://docs.datasette.io/en/stable/changelog.html#v0-48) - Documentation now lives at [docs.datasette.io](https://docs.datasette.io/), improvements to the `extra_template_vars`, `extra_css_urls`, `extra_js_urls` and `extra_body_script` plugin hooks.
 * 11th August 2020: [Datasette 0.47](https://docs.datasette.io/en/stable/changelog.html#v0-47) - Datasette can now be installed using Homebrew! `brew install simonw/datasette/datasette`. Also new: `datasette install name-of-plugin` and `datasette uninstall name-of-plugin` commands, and `datasette --get '/-/versions.json'` to output the result of Datasette HTTP calls on the command-line.
 * 9th August 2020: [Datasette 0.46](https://docs.datasette.io/en/stable/changelog.html#v0-46) - security fix relating to CSRF protection for writable canned queries, a new logo, new debugging tools, improved file downloads and more.
 * 6th August 2020: [GraphQL in Datasette with the new datasette-graphql plugin](https://simonwillison.net/2020/Aug/7/datasette-graphql/)
 * 24th July 2020: Two new plugins: [datasette-copyable and datasette-insert-api](https://simonwillison.net/2020/Jul/23/datasette-copyable-datasette-insert-api/). `datasette-copyable` adds copy-and-paste export options, and `datasette-insert-api` lets you create tables and insert or update data by POSTing JSON directly to Datasette.
 * 1st July 2020: [Datasette 0.45](https://docs.datasette.io/en/stable/changelog.html#v0-45) - [Magic parameters for canned queries](https://docs.datasette.io/en/stable/sql_queries.html#canned-queries-magic-parameters), a log out feature, improved plugin documentation and four new plugin hooks. See also [Datasette 0.45: The annotated release notes](https://simonwillison.net/2020/Jul/1/datasette-045/).
 * 20th June 2020: [A cookiecutter template for writing Datasette plugins](https://simonwillison.net/2020/Jun/20/cookiecutter-plugins/)
 * 11th June 2020: [Datasette 0.44](https://docs.datasette.io/en/stable/changelog.html#v0-44) - [Authentication and permissions](https://docs.datasette.io/en/stable/authentication.html), [writable canned queries](https://docs.datasette.io/en/stable/sql_queries.html#writable-canned-queries), flash messages, new plugin hooks and much, much more.
 * 28th May 2020: [Datasette 0.43](https://docs.datasette.io/en/stable/changelog.html#v0-43) - Redesigned [register_output_renderer](https://docs.datasette.io/en/stable/plugins.html#plugin-register-output-renderer) plugin hook and various small improvements and fixes.
 * 8th May 2020: [Datasette 0.42](https://docs.datasette.io/en/stable/changelog.html#v0-42) - Documented internal methods for plugins to execute read queries against a database.
 * 6th May 2020: [Datasette 0.41](https://docs.datasette.io/en/stable/changelog.html#v0-41) - New mechanism for [creating custom pages](https://docs.datasette.io/en/0.41/custom_templates.html#custom-pages), new [configuration directory mode](https://docs.datasette.io/en/0.41/config.html#configuration-directory-mode), new `?column__notlike=` table filter and various other smaller improvements.
 * 21st April 2020: [Datasette 0.40](https://docs.datasette.io/en/stable/changelog.html#v0-40) - Metadata can now be provided as YAML instead of JSON. Publishing to Zeit Now v1 is no longer supported, but Now v2 support is provided by the new [datasette-publish-now](https://github.com/simonw/datasette-publish-now) plugin. Various bug fixes.
 * 24th March 2020: [Datasette 0.39](https://docs.datasette.io/en/stable/changelog.html#v0-39) - New `base_url` configuration option for running Datasette under a different URL prefix, `"sort"` and `"sort_desc"` metadata options for setting a default sort order for a table.
 * 8th March 2020: [Datasette 0.38](https://docs.datasette.io/en/stable/changelog.html#v0-38) - New `--memory` option for `datasete publish cloudrun`, [Docker image](https://hub.docker.com/r/datasetteproject/datasette) upgraded to SQLite 3.31.1.
 * 25th February 2020: [Datasette 0.37](https://docs.datasette.io/en/stable/changelog.html#v0-37) - new internal APIs enabling plugins to safely write to databases. Read more here: [Datasette Writes](https://simonwillison.net/2020/Feb/26/weeknotes-datasette-writes/).
 * 21st February 2020: [Datasette 0.36](https://docs.datasette.io/en/stable/changelog.html#v0-36) - new internals documentation for plugins, `prepare_connection()` now accepts optional `database` and `datasette` arguments.
 * 4th February 2020: [Datasette 0.35](https://docs.datasette.io/en/stable/changelog.html#v0-35) - new `.render_template()` method for plugins.
 * 29th January 2020: [Datasette 0.34](https://docs.datasette.io/en/stable/changelog.html#v0-34) - improvements to search, `datasette publish cloudrun` and `datasette package`.
 * 21st January 2020: [Deploying a data API using GitHub Actions and Cloud Run](https://simonwillison.net/2020/Jan/21/github-actions-cloud-run/) - how to use GitHub Actions and Google Cloud Run to automatically scrape data and deploy the result as an API with Datasette.
 * 22nd December 2019: [Datasette 0.33](https://docs.datasette.io/en/stable/changelog.html#v0-33) - various small improvements.
 * 19th December 2019: [Building tools to bring data-driven reporting to more newsrooms](https://medium.com/jsk-class-of-2020/building-tools-to-bring-data-driven-reporting-to-more-newsrooms-4520a0c9b3f2) - some notes on my JSK fellowship so far.
 * 2nd December 2019: [Niche Museums](https://www.niche-museums.com/) is a new site entirely powered by Datasette, using custom templates and plugins. [niche-museums.com, powered by Datasette](https://simonwillison.net/2019/Nov/25/niche-museums/) describes how the site works, and [datasette-atom: Define an Atom feed using a custom SQL query](https://simonwillison.net/2019/Dec/3/datasette-atom/) describes how the new [datasette-atom plugin](https://github.com/simonw/datasette-atom) was used to add an Atom syndication feed to the site.
 * 14th November 2019: [Datasette 0.32](https://docs.datasette.io/en/stable/changelog.html#v0-32) now uses asynchronous rendering in Jinja templates, which means template functions can perform asynchronous operations such as executing SQL queries. [datasette-template-sql](https://github.com/simonw/datasette-template-sql) is a new plugin uses this capability to add a new custom `sql(sql_query)` template function.
 * 11th November 2019: [Datasette 0.31](https://docs.datasette.io/en/stable/changelog.html#v0-31) - the first version of Datasette to support Python 3.8, which means dropping support for Python 3.5.
 * 18th October 2019: [Datasette 0.30](https://docs.datasette.io/en/stable/changelog.html#v0-30)
 * 13th July 2019: [Single sign-on against GitHub using ASGI middleware](https://simonwillison.net/2019/Jul/14/sso-asgi/) talks about the implementation of [datasette-auth-github](https://github.com/simonw/datasette-auth-github) in more detail.
 * 7th July 2019: [Datasette 0.29](https://docs.datasette.io/en/stable/changelog.html#v0-29) - ASGI, new plugin hooks, facet by date and much, much more...
   * [datasette-auth-github](https://github.com/simonw/datasette-auth-github) - a new plugin for Datasette 0.29 that lets you require users to authenticate against GitHub before accessing your Datasette instance. You can whitelist specific users, or you can restrict access to members of specific GitHub organizations or teams.
   * [datasette-cors](https://github.com/simonw/datasette-cors) - a plugin that lets you configure CORS access from a list of domains (or a set of domain wildcards) so you can make JavaScript calls to a Datasette instance from a specific set of other hosts.
 * 23rd June 2019: [Porting Datasette to ASGI, and Turtles all the way down](https://simonwillison.net/2019/Jun/23/datasette-asgi/)
 * 21st May 2019: The anonymized raw data from [the Stack Overflow Developer Survey 2019](https://stackoverflow.blog/2019/05/21/public-data-release-of-stack-overflows-2019-developer-survey/) has been [published in partnership with Glitch](https://glitch.com/culture/discover-insights-explore-developer-survey-results-2019/), powered by Datasette.
 * 19th May 2019: [Datasette 0.28](https://docs.datasette.io/en/stable/changelog.html#v0-28) - a salmagundi of new features!
   * No longer immutable! Datasette now supports [databases that change](https://docs.datasette.io/en/stable/changelog.html#supporting-databases-that-change).
   * [Faceting improvements](https://docs.datasette.io/en/stable/changelog.html#faceting-improvements-and-faceting-plugins) including facet-by-JSON-array and the ability to define custom faceting using plugins.
   * [datasette publish cloudrun](https://docs.datasette.io/en/stable/changelog.html#datasette-publish-cloudrun) lets you publish databases to Google's new Cloud Run hosting service.
   * New [register_output_renderer](https://docs.datasette.io/en/stable/changelog.html#register-output-renderer-plugins) plugin hook for adding custom output extensions to Datasette in addition to the default `.json` and `.csv`.
   * Dozens of other smaller features and tweaks - see [the release notes](https://docs.datasette.io/en/stable/changelog.html#v0-28) for full details.
   * Read more about this release here: [Datasette 0.28—and why master should always be releasable](https://simonwillison.net/2019/May/19/datasette-0-28/)
 * 24th February 2019: [
sqlite-utils: a Python library and CLI tool for building SQLite databases](https://simonwillison.net/2019/Feb/25/sqlite-utils/) - a partner tool for easily creating SQLite databases for use with Datasette.
 * 31st Janary 2019: [Datasette 0.27](https://docs.datasette.io/en/stable/changelog.html#v0-27) - `datasette plugins` command, newline-delimited JSON export option, new documentation on [The Datasette Ecosystem](https://docs.datasette.io/en/stable/ecosystem.html).
 * 10th January 2019: [Datasette 0.26.1](https://docs.datasette.io/en/stable/changelog.html#v0-26-1) - SQLite upgrade in Docker image, `/-/versions` now shows SQLite compile options.
 * 2nd January 2019: [Datasette 0.26](https://docs.datasette.io/en/stable/changelog.html#v0-26) - minor bug fixes, `datasette publish now --alias` argument.
* 18th December 2018: [Fast Autocomplete Search for Your Website](https://24ways.org/2018/fast-autocomplete-search-for-your-website/) - a new tutorial on using Datasette to build a JavaScript autocomplete search engine.
* 3rd October 2018: [The interesting ideas in Datasette](https://simonwillison.net/2018/Oct/4/datasette-ideas/) - a write-up of some of the less obvious interesting ideas embedded in the Datasette project.
* 19th September 2018: [Datasette 0.25](https://docs.datasette.io/en/stable/changelog.html#v0-25) - New plugin hooks, improved database view support and an easier way to use more recent versions of SQLite.
* 23rd July 2018: [Datasette 0.24](https://docs.datasette.io/en/stable/changelog.html#v0-24) - a number of small new features
* 29th June 2018: [datasette-vega](https://github.com/simonw/datasette-vega), a new plugin for visualizing data as bar, line or scatter charts
* 21st June 2018: [Datasette 0.23.1](https://docs.datasette.io/en/stable/changelog.html#v0-23-1) - minor bug fixes
* 18th June 2018: [Datasette 0.23: CSV, SpatiaLite and more](https://docs.datasette.io/en/stable/changelog.html#v0-23) - CSV export, foreign key expansion in JSON and CSV, new config options, improved support for SpatiaLite and a bunch of other improvements
* 23rd May 2018: [Datasette 0.22.1 bugfix](https://github.com/simonw/datasette/releases/tag/0.22.1) plus we now use [versioneer](https://github.com/warner/python-versioneer)
* 20th May 2018: [Datasette 0.22: Datasette Facets](https://simonwillison.net/2018/May/20/datasette-facets)
* 5th May 2018: [Datasette 0.21: New _shape=, new _size=, search within columns](https://github.com/simonw/datasette/releases/tag/0.21)
* 25th April 2018: [Exploring the UK Register of Members Interests with SQL and Datasette](https://simonwillison.net/2018/Apr/25/register-members-interests/) - a tutorial describing how [register-of-members-interests.datasettes.com](https://register-of-members-interests.datasettes.com/) was built ([source code here](https://github.com/simonw/register-of-members-interests))
* 20th April 2018: [Datasette plugins, and building a clustered map visualization](https://simonwillison.net/2018/Apr/20/datasette-plugins/) - introducing Datasette's new plugin system and [datasette-cluster-map](https://pypi.org/project/datasette-cluster-map/), a plugin for visualizing data on a map
* 20th April 2018: [Datasette 0.20: static assets and templates for plugins](https://github.com/simonw/datasette/releases/tag/0.20)
* 16th April 2018: [Datasette 0.19: plugins preview](https://github.com/simonw/datasette/releases/tag/0.19)
* 14th April 2018: [Datasette 0.18: units](https://github.com/simonw/datasette/releases/tag/0.18)
* 9th April 2018: [Datasette 0.15: sort by column](https://github.com/simonw/datasette/releases/tag/0.15)
* 28th March 2018: [Baltimore Sun Public Salary Records](https://simonwillison.net/2018/Mar/28/datasette-in-the-wild/) - a data journalism project from the Baltimore Sun powered by Datasette - source code [is available here](https://github.com/baltimore-sun-data/salaries-datasette)
* 27th March 2018: [Cloud-first: Rapid webapp deployment using containers](https://wwwf.imperial.ac.uk/blog/research-software-engineering/2018/03/27/cloud-first-rapid-webapp-deployment-using-containers/) - a tutorial covering deploying Datasette using Microsoft Azure by the Research Software Engineering team at Imperial College London
* 28th January 2018: [Analyzing my Twitter followers with Datasette](https://simonwillison.net/2018/Jan/28/analyzing-my-twitter-followers/) - a tutorial on using Datasette to analyze follower data pulled from the Twitter API
* 17th January 2018: [Datasette Publish: a web app for publishing CSV files as an online database](https://simonwillison.net/2018/Jan/17/datasette-publish/)
* 12th December 2017: [Building a location to time zone API with SpatiaLite, OpenStreetMap and Datasette](https://simonwillison.net/2017/Dec/12/building-a-location-time-zone-api/)
* 9th December 2017: [Datasette 0.14: customization edition](https://github.com/simonw/datasette/releases/tag/0.14)
* 25th November 2017: [New in Datasette: filters, foreign keys and search](https://simonwillison.net/2017/Nov/25/new-in-datasette/)
* 13th November 2017: [Datasette: instantly create and publish an API for your SQLite databases](https://simonwillison.net/2017/Nov/13/datasette/)

## Installation

    pip3 install datasette

Datasette requires Python 3.6 or higher. We also have [detailed installation instructions](https://docs.datasette.io/en/stable/installation.html) covering other options such as Docker.

## Basic usage

    datasette serve path/to/database.db

This will start a web server on port 8001 - visit http://localhost:8001/ to access the web interface.

`serve` is the default subcommand, you can omit it if you like.

Use Chrome on OS X? You can run datasette against your browser history like so:

     datasette ~/Library/Application\ Support/Google/Chrome/Default/History

Now visiting http://localhost:8001/History/downloads will show you a web interface to browse your downloads data:

![Downloads table rendered by datasette](https://static.simonwillison.net/static/2017/datasette-downloads.png)

## datasette serve options

    Usage: datasette serve [OPTIONS] [FILES]...

      Serve up specified SQLite database files with a web UI

    Options:
      -i, --immutable PATH      Database files to open in immutable mode
      -h, --host TEXT           Host for server. Defaults to 127.0.0.1 which means
                                only connections from the local machine will be
                                allowed. Use 0.0.0.0 to listen to all IPs and
                                allow access from other machines.
      -p, --port INTEGER        Port for server, defaults to 8001
      --debug                   Enable debug mode - useful for development
      --reload                  Automatically reload if database or code change
                                detected - useful for development
      --cors                    Enable CORS by serving Access-Control-Allow-
                                Origin: *
      --load-extension PATH     Path to a SQLite extension to load
      --inspect-file TEXT       Path to JSON file created using "datasette
                                inspect"
      -m, --metadata FILENAME   Path to JSON file containing license/source
                                metadata
      --template-dir DIRECTORY  Path to directory containing custom templates
      --plugins-dir DIRECTORY   Path to directory containing custom plugins
      --static STATIC MOUNT     mountpoint:path-to-directory for serving static
                                files
      --memory                  Make :memory: database available
      --config CONFIG           Set config option using configname:value
                                docs.datasette.io/en/stable/config.html
      --version-note TEXT       Additional note to show on /-/versions
      --help-config             Show available config options
      --help                    Show this message and exit.

## metadata.json

If you want to include licensing and source information in the generated datasette website you can do so using a JSON file that looks something like this:

    {
        "title": "Five Thirty Eight",
        "license": "CC Attribution 4.0 License",
        "license_url": "http://creativecommons.org/licenses/by/4.0/",
        "source": "fivethirtyeight/data on GitHub",
        "source_url": "https://github.com/fivethirtyeight/data"
    }

Save this in `metadata.json` and run Datasette like so:

    datasette serve fivethirtyeight.db -m metadata.json

The license and source information will be displayed on the index page and in the footer. They will also be included in the JSON produced by the API.

## datasette publish

If you have [Heroku](https://heroku.com/) or [Google Cloud Run](https://cloud.google.com/run/) configured, Datasette can deploy one or more SQLite databases to the internet with a single command:

    datasette publish heroku database.db

Or:

    datasette publish cloudrun database.db

This will create a docker image containing both the datasette application and the specified SQLite database files. It will then deploy that image to Heroku or Cloud Run and give you a URL to access the resulting website and API.

See [Publishing data](https://docs.datasette.io/en/stable/publish.html) in the documentation for more details.
