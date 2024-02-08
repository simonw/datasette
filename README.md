<img src="https://datasette.io/static/datasette-logo.svg" alt="Datasette">

[![PyPI](https://img.shields.io/pypi/v/datasette.svg)](https://pypi.org/project/datasette/)
[![Changelog](https://img.shields.io/github/v/release/simonw/datasette?label=changelog)](https://docs.datasette.io/en/latest/changelog.html)
[![Python 3.x](https://img.shields.io/pypi/pyversions/datasette.svg?logo=python&logoColor=white)](https://pypi.org/project/datasette/)
[![Tests](https://github.com/simonw/datasette/workflows/Test/badge.svg)](https://github.com/simonw/datasette/actions?query=workflow%3ATest)
[![Documentation Status](https://readthedocs.org/projects/datasette/badge/?version=latest)](https://docs.datasette.io/en/latest/?badge=latest)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/datasette/blob/main/LICENSE)
[![docker: datasette](https://img.shields.io/badge/docker-datasette-blue)](https://hub.docker.com/r/datasetteproject/datasette)
[![discord](https://img.shields.io/discord/823971286308356157?label=discord)](https://datasette.io/discord)

*An open source multi-tool for exploring and publishing data*

Datasette is a tool for exploring and publishing data. It helps people take data of any shape or size and publish that as an interactive, explorable website and accompanying API.

Datasette is aimed at data journalists, museum curators, archivists, local governments, scientists, researchers and anyone else who has data that they wish to share with the world.

[Explore a demo](https://global-power-plants.datasettes.com/global-power-plants/global-power-plants), watch [a video about the project](https://simonwillison.net/2021/Feb/7/video/) or try it out by [uploading and publishing your own CSV data](https://docs.datasette.io/en/stable/getting_started.html#try-datasette-without-installing-anything-using-glitch).

* [datasette.io](https://datasette.io/) is the official project website
* Latest [Datasette News](https://datasette.io/news)
* Comprehensive documentation: https://docs.datasette.io/
* Examples: https://datasette.io/examples
* Live demo of current `main` branch: https://latest.datasette.io/
* Questions, feedback or want to talk about the project? Join our [Discord](https://datasette.io/discord)

Want to stay up-to-date with the project? Subscribe to the [Datasette newsletter](https://datasette.substack.com/) for tips, tricks and news on what's new in the Datasette ecosystem.

## Installation

If you are on a Mac, [Homebrew](https://brew.sh/) is the easiest way to install Datasette:

    brew install datasette

You can also install it using `pip` or `pipx`:

    pip install datasette

Datasette requires Python 3.8 or higher. We also have [detailed installation instructions](https://docs.datasette.io/en/stable/installation.html) covering other options such as Docker.

## Basic usage

    datasette serve path/to/database.db

This will start a web server on port 8001 - visit http://localhost:8001/ to access the web interface.

`serve` is the default subcommand, you can omit it if you like.

Use Chrome on OS X? You can run datasette against your browser history like so:

     datasette ~/Library/Application\ Support/Google/Chrome/Default/History --nolock

Now visiting http://localhost:8001/History/downloads will show you a web interface to browse your downloads data:

![Downloads table rendered by datasette](https://static.simonwillison.net/static/2017/datasette-downloads.png)

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

## Datasette Lite

[Datasette Lite](https://lite.datasette.io/) is Datasette packaged using WebAssembly so that it runs entirely in your browser, no Python web application server required. Read more about that in the [Datasette Lite documentation](https://github.com/simonw/datasette-lite/blob/main/README.md).
