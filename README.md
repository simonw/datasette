lalalallala

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
