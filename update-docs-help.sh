#!/bin/bash
echo $'$ datasette serve --help\n\n' "$(datasette serve --help)" > docs/datasette-serve-help.txt
echo $'$ datasette publish --help\n\n' "$(datasette publish --help)" > docs/datasette-publish-help.txt
echo $'$ datasette package --help\n\n' "$(datasette package --help)" > docs/datasette-package-help.txt
