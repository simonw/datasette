#!/bin/zsh
set -eu

cd -- "${0:A:h}"
exec uv run --with-editable '.[test]' pytest "$@"
