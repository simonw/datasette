# Datasette

[![PyPI](https://img.shields.io/pypi/v/datasette.svg)](https://pypi.org/project/datasette/)
[![Travis CI](https://travis-ci.org/simonw/datasette.svg?branch=master)](https://travis-ci.org/simonw/datasette)
[![Documentation Status](https://readthedocs.org/projects/datasette/badge/?version=latest)](http://datasette.readthedocs.io/en/latest/?badge=latest)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/datasette/blob/master/LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://black.readthedocs.io/en/stable/)

*A tool for exploring and publishing data*

Datasette is a tool for exploring and publishing data. It helps people take data of any shape or size and publish that as an interactive, explorable website and accompanying API.

Datasette is aimed at data journalists, museum curators, archivists, local governments and anyone else who has data that they wish to share with the world.

[Explore a demo](https://fivethirtyeight.datasettes.com/fivethirtyeight), watch [a video about the project](https://simonwillison.net/2018/Oct/25/how-instantly-publish-data-internet-datasette/) or try it out by [uploading and publishing your own CSV data](https://publish.datasettes.com/).

* Documentation: http://datasette.readthedocs.io/
* Examples: https://github.com/simonw/datasette/wiki/Datasettes
* Live demo of current master: https://latest.datasette.io/

## News

 * 24th February 2019: [
sqlite-utils: a Python library and CLI tool for building SQLite databases](https://simonwillison.net/2019/Feb/25/sqlite-utils/) - a partner tool for easily creating SQLite databases for use with Datasette.
 * 31st Janary 2019: [Datasette 0.27](https://datasette.readthedocs.io/en/latest/changelog.html#v0-27) - `datasette plugins` command, newline-delimited JSON export option, new documentation on [The Datasette Ecosystem](https://datasette.readthedocs.io/en/latest/ecosystem.html).
 * 10th January 2019: [Datasette 0.26.1](http://datasette.readthedocs.io/en/latest/changelog.html#v0-26-1) - SQLite upgrade in Docker image, `/-/versions` now shows SQLite compile options.
 * 2nd January 2019: [Datasette 0.26](http://datasette.readthedocs.io/en/latest/changelog.html#v0-26) - minor bug fixes, `datasette publish now --alias` argument.
* 18th December 2018: [Fast Autocomplete Search for Your Website](https://24ways.org/2018/fast-autocomplete-search-for-your-website/) - a new tutorial on using Datasette to build a JavaScript autocomplete search engine.
* 3rd October 2018: [The interesting ideas in Datasette](https://simonwillison.net/2018/Oct/4/datasette-ideas/) - a write-up of some of the less obvious interesting ideas embedded in the Datasette project.
* 19th September 2018: [Datasette 0.25](http://datasette.readthedocs.io/en/latest/changelog.html#v0-25) - New plugin hooks, improved database view support and an easier way to use more recent versions of SQLite.
* 23rd July 2018: [Datasette 0.24](http://datasette.readthedocs.io/en/latest/changelog.html#v0-24) - a number of small new features
* 29th June 2018: [datasette-vega](https://github.com/simonw/datasette-vega), a new plugin for visualizing data as bar, line or scatter charts
* 21st June 2018: [Datasette 0.23.1](http://datasette.readthedocs.io/en/latest/changelog.html#v0-23-1) - minor bug fixes
* 18th June 2018: [Datasette 0.23: CSV, SpatiaLite and more](http://datasette.readthedocs.io/en/latest/changelog.html#v0-23) - CSV export, foreign key expansion in JSON and CSV, new config options, improved support for SpatiaLite and a bunch of other improvements
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

Datasette requires Python 3.5 or higher.

## Basic usage

    datasette serve path/to/database.db

This will start a web server on port 8001 - visit http://localhost:8001/ to access the web interface.

`serve` is the default subcommand, you can omit it if you like.

Use Chrome on OS X? You can run datasette against your browser history like so:

     datasette ~/Library/Application\ Support/Google/Chrome/Default/History

Now visiting http://localhost:8001/History/downloads will show you a web interface to browse your downloads data:

![Downloads table rendered by datasette](https://static.simonwillison.net/static/2017/datasette-downloads.png)

http://localhost:8001/History/downloads.json will return that data as JSON:

    {
        "database": "History",
        "columns": [
            "id",
            "current_path",
            "target_path",
            "start_time",
            "received_bytes",
            "total_bytes",
            ...
        ],
        "rows": [
            [
                1,
                "/Users/simonw/Downloads/DropboxInstaller.dmg",
                "/Users/simonw/Downloads/DropboxInstaller.dmg",
                13097290269022132,
                626688,
                0,
                ...
            ]
        ]
    }


http://localhost:8001/History/downloads.json?_shape=objects will return that data as JSON in a more convenient but less efficient format:

    {
        ...
        "rows": [
            {
                "start_time": 13097290269022132,
                "interrupt_reason": 0,
                "hash": "",
                "id": 1,
                "site_url": "",
                "referrer": "https://www.dropbox.com/downloading?src=index",
                ...
            }
        ]
    }

## datasette serve options

    $ datasette serve --help
    Usage: datasette serve [OPTIONS] [FILES]...

      Serve up specified SQLite database files with a web UI

    Options:
      -h, --host TEXT              host for server, defaults to 127.0.0.1
      -p, --port INTEGER           port for server, defaults to 8001
      --debug                      Enable debug mode - useful for development
      --reload                     Automatically reload if code change detected -
                                   useful for development
      --cors                       Enable CORS by serving Access-Control-Allow-
                                   Origin: *
      --load-extension PATH        Path to a SQLite extension to load
      --inspect-file TEXT          Path to JSON file created using "datasette
                                   inspect"
      -m, --metadata FILENAME      Path to JSON file containing license/source
                                   metadata
      --template-dir DIRECTORY     Path to directory containing custom templates
      --plugins-dir DIRECTORY      Path to directory containing custom plugins
      --static STATIC MOUNT        mountpoint:path-to-directory for serving static
                                   files
      --config CONFIG              Set config option using configname:value
                                   datasette.readthedocs.io/en/latest/config.html
      --help-config                Show available config options
      --help                       Show this message and exit.

## metadata.json

If you want to include licensing and source information in the generated datasette website you can do so using a JSON file that looks something like this:

    {
        "title": "Five Thirty Eight",
        "license": "CC Attribution 4.0 License",
        "license_url": "http://creativecommons.org/licenses/by/4.0/",
        "source": "fivethirtyeight/data on GitHub",
        "source_url": "https://github.com/fivethirtyeight/data"
    }

The license and source information will be displayed on the index page and in the footer. They will also be included in the JSON produced by the API.

## datasette publish

If you have [Zeit Now](https://zeit.co/now) or [Heroku](https://heroku.com/) configured, datasette can deploy one or more SQLite databases to the internet with a single command:

    datasette publish now database.db

Or:

    datasette publish heroku database.db

This will create a docker image containing both the datasette application and the specified SQLite database files. It will then deploy that image to Zeit Now or Heroku and give you a URL to access the API.

    $ datasette publish --help
    Usage: datasette publish [OPTIONS] PUBLISHER [FILES]...

      Publish specified SQLite database files to the internet along with a
      datasette API.

      Options for PUBLISHER:     * 'now' - You must have Zeit Now installed:
      https://zeit.co/now     * 'heroku' - You must have Heroku installed:
      https://cli.heroku.com/

      Example usage: datasette publish now my-database.db

    Options:
      -n, --name TEXT           Application name to use when deploying to Now
                                (ignored for Heroku)
      -m, --metadata FILENAME   Path to JSON file containing metadata to publish
      --extra-options TEXT      Extra options to pass to datasette serve
      --force                   Pass --force option to now
      --branch TEXT             Install datasette from a GitHub branch e.g. master
      --token TEXT              Auth token to use for deploy (Now only)
      --template-dir DIRECTORY  Path to directory containing custom templates
      --plugins-dir DIRECTORY   Path to directory containing custom plugins
      --static STATIC MOUNT     mountpoint:path-to-directory for serving static
                                files
      --install TEXT            Additional packages (e.g. plugins) to install
      --spatialite              Enable SpatialLite extension
      --version-note TEXT       Additional note to show on /-/versions
      --title TEXT              Title for metadata
      --license TEXT            License label for metadata
      --license_url TEXT        License URL for metadata
      --source TEXT             Source label for metadata
      --source_url TEXT         Source URL for metadata
      --help                    Show this message and exit.

## datasette package

If you have docker installed you can use `datasette package` to create a new Docker image in your local repository containing the datasette app and selected SQLite databases:

    $ datasette package --help
    Usage: datasette package [OPTIONS] FILES...

      Package specified SQLite files into a new datasette Docker container

    Options:
      -t, --tag TEXT            Name for the resulting Docker container, can
                                optionally use name:tag format
      -m, --metadata FILENAME   Path to JSON file containing metadata to publish
      --extra-options TEXT      Extra options to pass to datasette serve
      --branch TEXT             Install datasette from a GitHub branch e.g. master
      --template-dir DIRECTORY  Path to directory containing custom templates
      --plugins-dir DIRECTORY   Path to directory containing custom plugins
      --static STATIC MOUNT     mountpoint:path-to-directory for serving static
                                files
      --install TEXT            Additional packages (e.g. plugins) to install
      --spatialite              Enable SpatialLite extension
      --version-note TEXT       Additional note to show on /-/versions
      --title TEXT              Title for metadata
      --license TEXT            License label for metadata
      --license_url TEXT        License URL for metadata
      --source TEXT             Source label for metadata
      --source_url TEXT         Source URL for metadata
      --help                    Show this message and exit.

Both publish and package accept an `extra_options` argument option, which will affect how the resulting application is executed. For example, say you want to increase the SQL time limit for a particular container:

    datasette package parlgov.db \
        --extra-options="--config sql_time_limit_ms:2500 --config default_page_size:10"

The resulting container will run the application with those options.

Here's example output for the package command:

    $ datasette package parlgov.db --extra-options="--config sql_time_limit_ms:2500"
    Sending build context to Docker daemon  4.459MB
    Step 1/7 : FROM python:3
     ---> 79e1dc9af1c1
    Step 2/7 : COPY . /app
     ---> Using cache
     ---> cd4ec67de656
    Step 3/7 : WORKDIR /app
     ---> Using cache
     ---> 139699e91621
    Step 4/7 : RUN pip install datasette
     ---> Using cache
     ---> 340efa82bfd7
    Step 5/7 : RUN datasette inspect parlgov.db --inspect-file inspect-data.json
     ---> Using cache
     ---> 5fddbe990314
    Step 6/7 : EXPOSE 8001
     ---> Using cache
     ---> 8e83844b0fed
    Step 7/7 : CMD datasette serve parlgov.db --port 8001 --inspect-file inspect-data.json --config sql_time_limit_ms:2500
     ---> Using cache
     ---> 1bd380ea8af3
    Successfully built 1bd380ea8af3

You can now run the resulting container like so:

    docker run -p 8081:8001 1bd380ea8af3

This exposes port 8001 inside the container as port 8081 on your host machine, so you can access the application at http://localhost:8081/
